#!/usr/bin/env python3
"""KNMI's official 10-minute air temperature for one station (default Rotterdam) into SQLite.

Source: the KNMI Data Platform EDR API (Dutch government weather service). It needs a free
personal API key: register at https://developer.dataplatform.knmi.nl, request an EDR API key,
and put it in ~/.knmi (chmod 600). Each run fetches the last 6 hours, so missed runs fill in.

Usage:  python3 knmi_temp.py                   # fetch and store (cron: every 10 minutes)
        python3 knmi_temp.py --show            # print the latest values, store nothing
        python3 knmi_temp.py --find rotterdam  # list matching stations and their ids
"""
import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.dataplatform.knmi.nl/edr/v1/collections/10-minute-in-situ-meteorological-observations"
KEY_FILE = Path.home() / ".knmi"
STATION = "0-20000-0-06344"  # Rotterdam The Hague Airport: WIGOS id "0-20000-0-" + WMO number 06344


def get_json(url):
    if not KEY_FILE.exists():
        raise SystemExit(f"Put your KNMI EDR API key in {KEY_FILE}")
    request = urllib.request.Request(url, headers={"Authorization": KEY_FILE.read_text().strip()})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.load(resp)


def find(word):
    """Print every station whose name or properties contain `word`."""
    for feature in get_json(f"{API}/locations")["features"]:
        if word.lower() in json.dumps(feature.get("properties", {})).lower():
            print(feature["id"], feature["properties"].get("name", ""))


def readings(cov):
    """CoverageJSON -> [(utc iso time, °C)], skipping missing values."""
    if cov.get("type") == "CoverageCollection":
        cov = cov["coverages"][0]
    times = cov["domain"]["axes"]["t"]["values"]
    temps = cov["ranges"]["ta"]["values"]
    return [(datetime.fromisoformat(t.replace("Z", "+00:00")).isoformat(), v)
            for t, v in zip(times, temps) if v is not None]


def fetch(station, hours=6):
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    span = f"{end - timedelta(hours=hours):%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"
    query = urllib.parse.urlencode({"datetime": span, "parameter-name": "ta"})
    return readings(get_json(f"{API}/locations/{station}?{query}"))


def store(db_path, station, rows):
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE IF NOT EXISTS knmi_temp "
               "(ts_utc TEXT, station TEXT, temp_c REAL, PRIMARY KEY (ts_utc, station))")
    with db:
        return db.executemany("INSERT OR IGNORE INTO knmi_temp VALUES (?, ?, ?)",
                              ((t, station, v) for t, v in rows)).rowcount


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(Path(__file__).with_name("p1.sqlite")))
    parser.add_argument("--station", default=STATION)
    parser.add_argument("--show", action="store_true", help="print the latest values, store nothing")
    parser.add_argument("--find", metavar="NAME", help="list stations whose name contains NAME")
    args = parser.parse_args()

    if args.find:
        return find(args.find)
    rows = fetch(args.station)
    if args.show:
        for t, v in rows[-6:]:
            print(t, v)
        return
    print(f"{datetime.now():%Y-%m-%d %H:%M} knmi {args.station}: +{store(args.db, args.station, rows)} rows")


if __name__ == "__main__":
    main()
