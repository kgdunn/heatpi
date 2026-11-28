#!/usr/bin/env python3
"""Frost rule: on a frosty night, lift a zone's low setpoint slot to a floor (default Living Room, 18 °C).

Why: at -4 °C outdoors the heat pump's flow is at its 45 °C maximum and the living room warms only
about 0.3 K an hour through the parquet. A slot at 15 °C before the morning then left the room 1.5 K
short of its setpoint at noon (27 Nov 2026). Holding 18 °C instead costs roughly 0.5 kWh of
electricity a night (an estimate).

Each run (cron, every 15 minutes), cheapest check first:
  1. The stored evohome schedule: is the zone in a slot below the floor right now? If not: done.
  2. The logged zone: is it following its schedule? (A manual change or an away mode wins.)
  3. The forecast (Open-Meteo, KNMI's HARMONIE model): below the frost limit before the slot ends?
  4. Log in, check the zone and the system once more, set a temporary override at the floor that ends
     with the slot, and note it in `events`.
It never sets a permanent override and never lowers a setpoint, so the worst a fault can do ends at
the next switchpoint. Any failure means: do nothing. Remove the cron line to stop it.

Usage:  ~/p1-logger/.venv/bin/python frost_rule.py [--dry-run] [--zone "Living Room"] [--floor 18] [--frost 0]
"""
import argparse
import asyncio
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from evohome_cli.auth import TokenCacheManager
from evohomeasync2 import EvohomeClient

import house

HERE = Path(__file__).parent
CREDENTIALS = Path.home() / ".evohome"
LOCAL = ZoneInfo("Europe/Amsterdam")
FORECAST = "https://api.open-meteo.com/v1/forecast"
WEEK = 7 * 24 * 60                      # minutes
MAX_HOURS = 12                          # an override never lasts longer than this


def schedule(db, zone):
    """The zone's newest stored schedule as [(minute of the week, setpoint)]; Monday 00:00 is 0."""
    rows = db.execute("SELECT day, start, setpoint_c FROM schedule WHERE zone = ? AND fetched_utc = "
                      "(SELECT max(fetched_utc) FROM schedule WHERE zone = ?) ORDER BY day, start",
                      (zone, zone)).fetchall()
    return [(day * 1440 + int(start[:2]) * 60 + int(start[3:5]), setpoint) for day, start, setpoint in rows]


def week_minute(when):
    return when.weekday() * 1440 + when.hour * 60 + when.minute


def low_slot(points, now, floor):
    """If the schedule asks less than `floor` at `now` (local time): (that setpoint, when the low stretch
    ends), else None. It ends at the first switchpoint at or above the floor, at most MAX_HOURS ahead."""
    minute = week_minute(now)
    earlier = [setpoint for m, setpoint in points if m <= minute]
    current = earlier[-1] if earlier else points[-1][1]          # before Monday's first: Sunday's last
    if current >= floor:
        return None
    for ahead, setpoint in sorted(((m - minute) % WEEK, setpoint) for m, setpoint in points):
        if 0 < ahead <= MAX_HOURS * 60 and setpoint >= floor:
            return current, now + timedelta(minutes=ahead)
    return current, now + timedelta(hours=MAX_HOURS)


def logged_mode(db, zone):
    """(zone mode, system mode) at the zone's newest reading, or None if that is over 30 minutes old."""
    row = db.execute("SELECT ts_utc, zone_mode, system_mode FROM zones WHERE zone = ? "
                     "ORDER BY ts_utc DESC LIMIT 1", (zone,)).fetchone()
    if row and datetime.fromisoformat(row[0]) > datetime.now(timezone.utc) - timedelta(minutes=30):
        return row[1], row[2]
    return None


def forecast_low(until):
    """The lowest forecast temperature (°C) from the past hour until `until`, for the house's spot."""
    query = urllib.parse.urlencode({"latitude": house.LAT, "longitude": house.LON, "hourly": "temperature_2m",
                                    "models": "knmi_seamless", "forecast_days": 2, "timezone": "UTC"})
    with urllib.request.urlopen(f"{FORECAST}?{query}", timeout=30) as resp:
        hourly = json.load(resp)["hourly"]
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    temps = [t for ts, t in zip(hourly["time"], hourly["temperature_2m"])
             if t is not None and since <= datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) <= until]
    return min(temps) if temps else None


async def override(zone_name, floor, until):
    """Check the zone and the system in the cloud, then set the temporary override. Returns None when it
    did, else the reason it did not. Reuses the evohome logger's cached token: no new login each time."""
    user, password = CREDENTIALS.read_text().split("\n")[:2]
    async with aiohttp.ClientSession() as session:
        tokens = TokenCacheManager(user.strip(), password.strip(), session)
        await tokens.load_from_cache()
        evo = EvohomeClient(tokens)
        await evo.setup()
        location = evo.locations[0]
        await location.get_status()
        found = [(tcs, zone) for gateway in location.gateways for tcs in gateway.systems
                 for zone in tcs.zones if zone.name == zone_name]
        if not found:
            return f"no zone called {zone_name!r}"
        tcs, zone = found[0]
        if str(tcs.mode) != "auto":
            return f"the system is in {tcs.mode} mode"
        if str(zone.mode) != "follow_schedule":
            return f"the zone is in {zone.mode} (someone changed it)"
        await zone.set_temperature(floor, until=until)
        await tokens.save_access_token()
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zone", default="Living Room")
    parser.add_argument("--floor", type=float, default=18.0, help="lowest setpoint on a frosty night (°C)")
    parser.add_argument("--frost", type=float, default=0.0, help="act when the forecast falls below this (°C)")
    parser.add_argument("--dry-run", action="store_true", help="say what it would do; write nothing")
    parser.add_argument("--db", default=str(HERE / "p1.sqlite"))
    args = parser.parse_args()
    floor = min(max(args.floor, 5.0), 21.0)                     # never a silly setpoint

    now = datetime.now(LOCAL).replace(second=0, microsecond=0)
    stamp = f"{now:%Y-%m-%d %H:%M} {args.zone}:"
    with sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30) as db:
        points = schedule(db, args.zone)
        mode = logged_mode(db, args.zone)
    if not points:
        return print(stamp, "no stored schedule yet (the evohome logger reads it); nothing done")
    slot = low_slot(points, now, floor)
    if slot is None:
        return print(stamp, f"the schedule is at or above {floor:.1f} °C now; nothing to do")
    current, until = slot
    if mode and (mode[0] != "follow_schedule" or mode[1] != "auto"):
        return print(stamp, f"in a {current:.1f} °C slot, but the zone is {mode[0]} and the system {mode[1]}; "
                            "left alone")
    try:
        low = forecast_low(until.astimezone(timezone.utc))
    except (OSError, ValueError, KeyError) as e:
        return print(stamp, f"no forecast ({e}); nothing done")
    if low is None or low >= args.frost:
        return print(stamp, f"in a {current:.1f} °C slot until {until:%H:%M}; forecast low "
                            f"{'?' if low is None else f'{low:.1f}'} °C, no frost; nothing to do")

    note = (f"frost rule: {args.zone} {floor:.1f} °C until {until:%H:%M} "
            f"(schedule {current:.1f} °C, forecast low {low:.1f} °C)")
    if args.dry_run:
        return print(stamp, "would set", note.removeprefix("frost rule: "))
    reason = asyncio.run(override(args.zone, floor, until))
    if reason:
        return print(stamp, f"not set: {reason}")
    with sqlite3.connect(args.db, timeout=30) as db:            # the note: when and why the heating changed
        db.execute("CREATE TABLE IF NOT EXISTS events (ts_utc TEXT, note TEXT)")
        db.execute("INSERT INTO events VALUES (?, ?)",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"), note))
    print(stamp, "set", note.removeprefix("frost rule: "))


if __name__ == "__main__":
    main()
