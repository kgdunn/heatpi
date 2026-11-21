#!/usr/bin/env python3
"""Fetch hourly data from the street sensors around the house.

The Luchtwachters Delft sensors publish hourly means to RIVM's open Samen
Meten API: temperature and PM2.5 (raw, and RIVM-calibrated "pm25_kal").
This copies everything each sensor publishes into the same SQLite file as
the P1 logger, fetching only the hours it doesn't have yet. New quantities
a sensor starts publishing are picked up automatically.

  street_temp  (ts_utc, sensor, temp_c)            -> street_consensus.py
  street_air   (ts_utc, sensor, quantity, value)   e.g. pm25, pm25_kal

Usage:  python3 outdoor_temp.py [--db p1.sqlite]
Hourly via cron (see README.md).
"""
import argparse
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime

API = "https://api-samenmeten.rivm.nl/v1.0"
START = "2025-09-01T00:00:00+00:00"  # first backfill: covers last heating season

# Samen Meten "Thing" id of each sensor, nearest first.
SENSORS = {
    "de-4190": 11611,  # ours (data from Feb 2026)
    "de-4084": 9416,
    "de-4062": 9402,
    "de-4071": 9466,
    "de-1093": 9447,   # against a warm wall: stored, left out of the consensus
    "de-4096": 9424,
    "de-4064": 9403,   # against a warm wall: stored, left out of the consensus
    "de-4051": 9401,
}


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def datastreams(thing):
    """{quantity: datastream id}, e.g. {'temp': 53165, 'pm25': 53163, 'pm25_kal': 53164}.

    Datastream names look like 'OHN_de-4190-12-pm25_kal'; the quantity is the last part.
    """
    page = get_json(f"{API}/Things({thing})/Datastreams")
    return {ds["name"].rsplit("-", 1)[-1]: ds["@iot.id"] for ds in page["value"]}


def observations(datastream, after):
    """Yield (utc_time, value) for every hour after `after`, page by page."""
    query = urllib.parse.urlencode({
        "$filter": f"phenomenonTime gt {after.replace('+00:00', 'Z')}",
        "$orderby": "phenomenonTime asc",
        "$select": "phenomenonTime,result",
        "$top": 1000,
    })
    url = f"{API}/Datastreams({datastream})/Observations?{query}"
    while url:
        page = get_json(url)
        for obs in page["value"]:
            ts = datetime.fromisoformat(obs["phenomenonTime"].replace("Z", "+00:00"))
            yield ts.isoformat(timespec="seconds"), obs["result"]
        # The API keeps offering a next page; an empty page means we're done.
        url = page.get("@iot.nextLink") if page["value"] else None


# Temperature keeps its own table (the consensus reads it); everything else is "long".
def last_hour(db, sensor, quantity):
    if quantity == "temp":
        row = db.execute("SELECT max(ts_utc) FROM street_temp WHERE sensor = ?", (sensor,))
    else:
        row = db.execute("SELECT max(ts_utc) FROM street_air WHERE sensor = ? AND quantity = ?",
                         (sensor, quantity))
    return row.fetchone()[0] or START


def store(db, sensor, quantity, rows):
    if quantity == "temp":
        sql, rows = "INSERT OR IGNORE INTO street_temp VALUES (?, ?, ?)", ((t, sensor, v) for t, v in rows)
    else:
        sql, rows = ("INSERT OR IGNORE INTO street_air VALUES (?, ?, ?, ?)",
                     ((t, sensor, quantity, v) for t, v in rows))
    with db:  # one transaction per sensor and quantity
        return db.executemany(sql, rows).rowcount


def update(db):
    """Fetch new hours for every sensor and quantity; failures don't stop the rest."""
    db.execute("CREATE TABLE IF NOT EXISTS street_temp "
               "(ts_utc TEXT, sensor TEXT, temp_c REAL, PRIMARY KEY (ts_utc, sensor))")
    db.execute("CREATE TABLE IF NOT EXISTS street_air "
               "(ts_utc TEXT, sensor TEXT, quantity TEXT, value REAL, PRIMARY KEY (ts_utc, sensor, quantity))")
    for sensor, thing in SENSORS.items():
        report = []
        try:
            for quantity, ds in datastreams(thing).items():
                added = store(db, sensor, quantity, observations(ds, last_hour(db, sensor, quantity)))
                report.append(f"{quantity} +{added}")
            print(f"{sensor}: " + ", ".join(report))
        except OSError as e:  # network/API trouble: retried next run
            print(f"{sensor}: failed after {', '.join(report) or 'nothing'} ({e})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite", help="SQLite file (shared with the P1 logger)")
    update(sqlite3.connect(parser.parse_args().db))


if __name__ == "__main__":
    main()
