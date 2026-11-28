#!/usr/bin/env python3
"""The weather forecast for the house's spot into SQLite, every hour: KNMI's HARMONIE model via Open-Meteo, no key.

  forecast (issued_utc, ts_utc, temp_c, rad_wm2)
    issued_utc  the hour this run fetched it, UTC
    ts_utc      the hour it is a forecast for, UTC
    temp_c      air temperature at 2 m at ts_utc
    rad_wm2     sunshine on a flat surface ("shortwave radiation"), mean of the hour that ends at ts_utc

Each run keeps the next 48 hours. So the table also records how the forecast for an hour changed as
that hour came closer: what a controller needs, to know how far to trust it.

Usage:  python3 forecast_logger.py [--db p1.sqlite]
Hourly via cron (see README.md).
"""
import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import house

API = "https://api.open-meteo.com/v1/forecast"
AHEAD = 48                                 # hours kept from each run


def fetch():
    """[(ts_utc, temp_c, rad_wm2)] for every forecast hour."""
    query = urllib.parse.urlencode({"latitude": house.LAT, "longitude": house.LON, "models": "knmi_seamless",
                                    "hourly": "temperature_2m,shortwave_radiation", "forecast_days": 3,
                                    "timezone": "UTC"})
    with urllib.request.urlopen(f"{API}?{query}", timeout=30) as resp:
        hourly = json.load(resp)["hourly"]
    return [(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), temp, rad)
            for t, temp, rad in zip(hourly["time"], hourly["temperature_2m"], hourly["shortwave_radiation"])]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite", help="SQLite file (shared with the loggers)")
    args = parser.parse_args()
    issued = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    try:
        forecast = fetch()
    except (OSError, ValueError, KeyError) as e:       # network, rate limit, changed answer: try next hour
        raise SystemExit(f"{datetime.now():%Y-%m-%d %H:%M} forecast: none this hour ({e})")
    rows = [(issued.isoformat(), t.isoformat(), temp, rad) for t, temp, rad in forecast
            if issued <= t <= issued + timedelta(hours=AHEAD) and temp is not None]
    db = sqlite3.connect(args.db, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS forecast (issued_utc TEXT, ts_utc TEXT, temp_c REAL, rad_wm2 REAL, "
               "PRIMARY KEY (issued_utc, ts_utc))")
    with db:
        db.executemany("INSERT OR REPLACE INTO forecast VALUES (?, ?, ?, ?)", rows)
    low = min(rows, key=lambda r: r[2])
    print(f"{datetime.now():%Y-%m-%d %H:%M} forecast: {len(rows)} hours, lowest {low[2]:.1f} °C at {low[1][:16]} UTC")


if __name__ == "__main__":
    main()
