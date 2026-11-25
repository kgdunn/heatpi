#!/usr/bin/env python3
"""Log every evohome zone's temperature and setpoint, every 5 minutes, to SQLite.

Reads from Honeywell/Resideo's Total Connect Comfort cloud (the same data the
evohome app shows). Polling every 5 minutes keeps well clear of the vendor's
rate limit, and the access token is cached between restarts for the same reason.

Also reads each zone's weekly schedule at start and every 6 hours, and stores it
only when it changed: the `schedule` table is the history of schedule changes.
It only reads; nothing is ever written to evohome.

Needs, once:
    python3 -m venv --system-site-packages ~/p1-logger/.venv
    ~/p1-logger/.venv/bin/pip install 'evohome-async[cli]'
    printf '%s\n%s\n' 'your-tcc-email' 'your-tcc-password' > ~/.evohome && chmod 600 ~/.evohome

Usage:  ~/p1-logger/.venv/bin/python evohome_logger.py [--db p1.sqlite] [--every 300]
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
from evohomeasync2 import EvohomeClient

CREDENTIALS = Path.home() / ".evohome"  # line 1: e-mail, line 2: password
SCHEDULE_EVERY = 6 * 3600               # schedules rarely change: one call per zone every 6 hours
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
log = logging.getLogger("evohome_logger")


def connect(db_path):
    db = sqlite3.connect(db_path, timeout=30)  # wait up to 30 s for another writer (default: 5 s)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS zones (ts_utc TEXT, zone TEXT, temp_c REAL, setpoint_c REAL, "
               "zone_mode TEXT, system_mode TEXT, PRIMARY KEY (ts_utc, zone))")
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
    for zone in (zone for gateway in location.gateways for tcs in gateway.systems for zone in tcs.zones):
        if store_schedule(db, zone.name, schedule_rows(await zone.get_schedule()), ts):
            changed.append(zone.name)
    log.info("Schedules read; changed: %s", ", ".join(changed) or "none")


def zone_rows(location, ts):
    """One row per zone: time, name, temperature, setpoint, zone mode, system mode (e.g. Away)."""
    for gateway in location.gateways:
        for tcs in gateway.systems:
            for zone in tcs.zones:
                yield (ts, zone.name, zone.temperature, zone.target_heat_temperature,
                       str(zone.mode), str(tcs.mode))


async def run(db_path, every):
    if not CREDENTIALS.exists():
        raise SystemExit(f"Put your TCC e-mail (line 1) and password (line 2) in {CREDENTIALS}")
    user, password = CREDENTIALS.read_text().split("\n")[:2]
    db = connect(db_path)
    async with aiohttp.ClientSession() as session:
        tokens = TokenCacheManager(user.strip(), password.strip(), session)
        await tokens.load_from_cache()      # reuse the last token instead of logging in again
        evo = EvohomeClient(tokens)
        await evo.setup()                   # once: which zones exist
        location = evo.locations[0]
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
            try:
                await location.get_status()
                rows = list(zone_rows(location, ts))
                with db:
                    db.executemany("INSERT OR IGNORE INTO zones VALUES (?, ?, ?, ?, ?, ?)", rows)
                await tokens.save_access_token()
                log.info("  ".join(f"{name} {temp}/{sp}" for _, name, temp, sp, *_ in rows))
            except Exception:               # network or vendor trouble: log it, retry next round
                log.exception("Poll failed")
            await asyncio.sleep(every - time.time() % every)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite")
    parser.add_argument("--every", type=int, default=300, help="seconds between polls (keep >= 180)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(run(args.db, max(args.every, 180)))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
