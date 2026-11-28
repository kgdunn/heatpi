#!/usr/bin/env python3
"""Log every evohome zone's temperature and setpoint, every 5 minutes, to SQLite.

Reads from Honeywell/Resideo's Total Connect Comfort cloud (the same data the
evohome app shows). The v2 API rounds temperatures to 0.5 °C; the older v1 API, read with the
same login, gives them to 0.1 °C (`temp_fine_c`). A v1 failure leaves that column empty and
pauses v1 for an hour; the v2 rows are written either way. Polling every 5 minutes keeps well clear of the vendor's
rate limit, and the access token is cached between restarts for the same reason.

Also reads each zone's weekly schedule at start and every 6 hours, and stores it
only when it changed: the `schedule` table is the history of schedule changes.
It only reads; nothing is ever written to evohome.

Needs, once:
    python3 -m venv --system-site-packages ~/p1-logger/.venv
    ~/p1-logger/.venv/bin/pip install 'evohome-async[cli]'
    printf '%s\n%s\n' 'your-tcc-email' 'your-tcc-password' > ~/.evohome && chmod 600 ~/.evohome

Usage:  ~/p1-logger/.venv/bin/python evohome_logger.py [--db p1.sqlite] [--every 300] [--once]
        --once: read both APIs once, print them side by side, write nothing
"""
import argparse
import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from evohome_cli.auth import TokenCacheManager
from evohomeasync import EvohomeClient as EvohomeClientV1
from evohomeasync2 import EvohomeClient

CREDENTIALS = Path.home() / ".evohome"  # line 1: e-mail, line 2: password
FINE_PAUSE = 3600                       # after a v1 failure, leave v1 alone this long
SCHEDULE_EVERY = 6 * 3600               # schedules rarely change: one call per zone every 6 hours
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
log = logging.getLogger("evohome_logger")


def connect(db_path):
    db = sqlite3.connect(db_path, timeout=30)  # wait up to 30 s for another writer (default: 5 s)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS zones (ts_utc TEXT, zone TEXT, temp_c REAL, setpoint_c REAL, "
               "zone_mode TEXT, system_mode TEXT, temp_fine_c REAL, PRIMARY KEY (ts_utc, zone))")
    if "temp_fine_c" not in {column[1] for column in db.execute("PRAGMA table_info(zones)")}:
        with db:                                # tables made before the v1 readout
            db.execute("ALTER TABLE zones ADD COLUMN temp_fine_c REAL")
    # One row per switchpoint. day: 0 = Monday; start: local time "HH:MM"; fetched_utc: when it was read.
    db.execute("CREATE TABLE IF NOT EXISTS schedule (fetched_utc TEXT, zone TEXT, day INTEGER, start TEXT, "
               "setpoint_c REAL, PRIMARY KEY (fetched_utc, zone, day, start))")
    return db


def schedule_rows(schedule):
    """The library's weekly schedule -> [(day, "HH:MM", setpoint)], sorted, Monday = 0."""
    return sorted((DAYS.index(str(day["day_of_week"]).lower()), point["time_of_day"][:5], float(point["heat_setpoint"]))
                  for day in schedule for point in day["switchpoints"])


def store_schedule(db, zone, rows, ts):
    """Store a zone's schedule if it differs from the last one stored. Returns True if it did."""
    last = db.execute("SELECT max(fetched_utc) FROM schedule WHERE zone = ?", (zone,)).fetchone()[0]
    stored = db.execute("SELECT day, start, setpoint_c FROM schedule WHERE zone = ? AND fetched_utc = ? "
                        "ORDER BY day, start", (zone, last)).fetchall()
    if stored == rows:
        return False
    with db:
        db.executemany("INSERT INTO schedule VALUES (?, ?, ?, ?, ?)", ((ts, zone, *row) for row in rows))
    return True


async def read_schedules(db, location, ts):
    """Read every zone's schedule from the cloud; store the ones that changed."""
    changed = []
    for zone in zones_of(location):
        if store_schedule(db, zone.name, schedule_rows(await zone.get_schedule()), ts):
            changed.append(zone.name)
    log.info("Schedules read; changed: %s", ", ".join(changed) or "none")


def zones_of(location):
    """Every zone of a location, from either API (both have gateways -> systems -> zones)."""
    return [zone for gateway in location.gateways for tcs in gateway.systems for zone in tcs.zones]


def zone_rows(location, ts, fine):
    """One row per zone: time, name, temperature, setpoint, zone mode, system mode (e.g. Away), and
    the 0.1 °C temperature from `fine` ({zone name: °C}; empty when v1 failed)."""
    for gateway in location.gateways:
        for tcs in gateway.systems:
            for zone in tcs.zones:
                yield (ts, zone.name, zone.temperature, zone.target_heat_temperature,
                       str(zone.mode), str(tcs.mode), fine.get(zone.name))


async def read_fine(v1):
    """{zone name: °C to 0.1} from the v1 API: one call for all zones (two the first time)."""
    await v1.get_status()
    return {zone.name: zone.temperature for location in v1.locations for zone in zones_of(location)}


async def run(db_path, every, once=False):
    if not CREDENTIALS.exists():
        raise SystemExit(f"Put your TCC e-mail (line 1) and password (line 2) in {CREDENTIALS}")
    user, password = CREDENTIALS.read_text().split("\n")[:2]
    async with aiohttp.ClientSession() as session:
        tokens = TokenCacheManager(user.strip(), password.strip(), session)
        await tokens.load_from_cache()      # reuse the last token instead of logging in again
        evo = EvohomeClient(tokens)
        await evo.setup()                   # once: which zones exist
        location = evo.locations[0]
        v1, fine_after = EvohomeClientV1(tokens), 0.0
        if once:
            await location.get_status()
            fine = await read_fine(v1)
            await tokens.save_access_token()
            await tokens.save_session_id()
            for _, name, temp, setpoint, *_ in zone_rows(location, "", fine):
                print(f"{name:12} v2 {temp} °C   v1 {fine.get(name)} °C   setpoint {setpoint} °C")
            return
        db = connect(db_path)
        log.info("Logging %d zones every %d s", sum(len(t.zones) for g in location.gateways for t in g.systems), every)

        next_schedule = 0                   # read the schedules straight away
        while True:
            ts = datetime.fromtimestamp(time.time() // every * every, timezone.utc).isoformat()
            if time.time() >= next_schedule:
                try:
                    await read_schedules(db, location, ts)
                    next_schedule = time.time() + SCHEDULE_EVERY
                except Exception:           # a schedule failure must not stop the temperatures
                    log.exception("Reading the schedules failed; trying again in 30 minutes")
                    next_schedule = time.time() + 1800
            fine = {}
            if time.time() >= fine_after:
                try:
                    fine = await read_fine(v1)
                    await tokens.save_session_id()
                except Exception:           # v1 is a bonus: never let it stop the v2 rows
                    log.exception("Reading the 0.1 °C temperatures (v1) failed; pausing v1 for an hour")
                    fine_after = time.time() + FINE_PAUSE
            try:
                await location.get_status()
                rows = list(zone_rows(location, ts, fine))
                with db:
                    db.executemany("INSERT OR IGNORE INTO zones (ts_utc, zone, temp_c, setpoint_c, zone_mode, "
                                   "system_mode, temp_fine_c) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                await tokens.save_access_token()
                log.info("  ".join(f"{name} {temp} ({fine_c})/{sp}" for _, name, temp, sp, _, _, fine_c in rows))
            except Exception:               # network or vendor trouble: log it, retry next round
                log.exception("Poll failed")
            await asyncio.sleep(every - time.time() % every)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite")
    parser.add_argument("--every", type=int, default=300, help="seconds between polls (keep >= 180)")
    parser.add_argument("--once", action="store_true", help="read both APIs once, print them, write nothing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(run(args.db, max(args.every, 180), args.once))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
