#!/usr/bin/env python3
"""KNMI's validated hourly weather for one station (default Rotterdam) into SQLite. No key needed.

The heat balance needs sunshine, and later wind and rain. KNMI publishes these per hour, checked,
about two days late. Each run fetches only the hours not stored yet (first run: from Sep 2025).

  knmi_hourly (ts_utc, temp_c, wind_ms, wind_dir, rad_wm2, rain_mm, rain_h)
    ts_utc    start of the hour, UTC
    wind_ms   mean wind speed, m/s;  wind_dir: degrees it comes from (empty when calm or variable)
    rad_wm2   mean sunshine on a flat surface ("global radiation"), W/m2
    rain_mm   rain in the hour, mm;  rain_h: part of the hour it rained

Usage:  python3 knmi_hourly.py [--db p1.sqlite] [--station 344]
Every 6 hours via cron (see README.md).
"""
import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://www.daggegevens.knmi.nl/klimatologie/uurgegevens"
START = "2025-09-01T00:00:00+00:00"  # first backfill: as far back as the street sensors
HOUR = timedelta(hours=1)


def fetch(station, first_day, last_day):
    """Every hour of the days first_day..last_day. In KNMI's request the two-digit hour is a range
    within each day (01-24), not a moment: so always ask for whole days."""
    form = urllib.parse.urlencode({"start": f"{first_day:%Y%m%d}01", "end": f"{last_day:%Y%m%d}24",
                                   "stns": station, "vars": "T:FH:DD:Q:RH:DR", "fmt": "json"}).encode()
    with urllib.request.urlopen(urllib.request.Request(API, data=form), timeout=60) as resp:
        return json.load(resp)


def convert(row):
    """KNMI's integers -> one table row in plain units. KNMI counts hours 1-24: hour 1 runs from
    00:00 to 01:00 UTC, so a row starts at (hour - 1)."""
    def tenth(name):
        return None if row[name] is None else max(row[name], 0) / 10      # rain -1 means under 0.05 mm
    start = datetime.fromisoformat(row["date"][:10]).replace(tzinfo=timezone.utc) + (row["hour"] - 1) * HOUR
    direction = row["DD"] if row["DD"] not in (0, 990) else None           # 0 calm, 990 variable
    radiation = None if row["Q"] is None else round(row["Q"] * 1e4 / 3600, 1)   # J/cm2 per hour -> W/m2
    temp = None if row["T"] is None else row["T"] / 10                     # may be below zero
    return (start.isoformat(), temp, tenth("FH"), direction, radiation, tenth("RH"), tenth("DR"))


def update(db, station):
    db.execute("CREATE TABLE IF NOT EXISTS knmi_hourly (ts_utc TEXT PRIMARY KEY, temp_c REAL, wind_ms REAL, "
               "wind_dir REAL, rad_wm2 REAL, rain_mm REAL, rain_h REAL)")
    newest = db.execute("SELECT max(ts_utc) FROM knmi_hourly").fetchone()[0] or START
    rows = [convert(r) for r in fetch(station, datetime.fromisoformat(newest), datetime.now(timezone.utc))]
    rows = [r for r in rows if r[0] > newest or newest == START]          # only hours not stored yet
    with db:
        db.executemany("INSERT OR REPLACE INTO knmi_hourly VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    print(f"knmi_hourly: +{len(rows)} hours, newest {rows[-1][0][:16]}" if rows else "knmi_hourly: nothing newer yet")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite", help="SQLite file (shared with the P1 logger)")
    parser.add_argument("--station", type=int, default=344, help="KNMI station number (344 = Rotterdam)")
    args = parser.parse_args()
    update(sqlite3.connect(args.db, timeout=30), args.station)


if __name__ == "__main__":
    main()
