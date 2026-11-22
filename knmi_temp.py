#!/usr/bin/env python3
"""KNMI's official 10-minute observations for one station (default Rotterdam) into SQLite.

  knmi_temp (ts_utc, station, temp_c)   air temperature, for the dashboard
  knmi_live (ts_utc, rad_wm2, wind_ms, wind_dir, rain_mmh, soil_c)
      for the heat balance, without the two-day wait of knmi_hourly.py:
      rad_wm2   sunshine on a flat surface ("global radiation"), W/m2, mean of the 10 minutes before ts_utc
      wind_ms   wind speed at 10 m, m/s;  wind_dir: degrees it comes from
      rain_mmh  rain intensity, mm/h;  soil_c: soil temperature at 1 m depth, °C

Source: the KNMI Data Platform EDR API (Dutch government weather service). It needs a free
personal API key: register at https://developer.dataplatform.knmi.nl, request an EDR API key,
and put it in ~/.knmi (chmod 600). Each run fetches the last 6 hours, so missed runs fill in;
the very first run asks for 3 days.

Usage:  python3 knmi_temp.py                   # fetch and store (cron: every 10 minutes)
        python3 knmi_temp.py --show            # print the latest values, store nothing
        python3 knmi_temp.py --find rotterdam  # list matching stations and their ids
"""
import argparse
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.dataplatform.knmi.nl/edr/v1/collections/10-minute-in-situ-meteorological-observations"
KEY_FILE = Path.home() / ".knmi"
STATION = "0-20000-0-06344"  # Rotterdam The Hague Airport: WIGOS id "0-20000-0-" + WMO number 06344
LIVE = ("qg", "ff", "dd", "rg", "tb5")   # KNMI's names for the knmi_live columns, in table order


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
    """CoverageJSON -> {utc iso time: {KNMI name: value}}, skipping missing values. One station can
    come as one coverage or as a collection of them."""
    out = {}
    for one in cov.get("coverages", [cov]):
        times = one["domain"]["axes"]["t"]["values"]
        for name, column in one.get("ranges", {}).items():
            for t, v in zip(times, column["values"]):
                if v is not None:
                    out.setdefault(datetime.fromisoformat(t.replace("Z", "+00:00")).isoformat(), {})[name] = v
    return dict(sorted(out.items()))


def fetch(station, hours=6):
    """The last `hours` of readings. If KNMI refuses the request (a span too long, a quantity this
    station lacks), a smaller one is tried, so the temperature keeps coming in whatever happens."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for names, span in ((("ta", *LIVE), hours), (("ta", *LIVE), 6), (("ta",), 6)):
        when = f"{end - timedelta(hours=span):%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"
        query = urllib.parse.urlencode({"datetime": when, "parameter-name": ",".join(names)})
        try:
            return readings(get_json(f"{API}/locations/{station}?{query}"))
        except urllib.error.HTTPError as e:
            error = e
            print(f"knmi refused {len(names)} quantities over {span} h ({e.code}): trying less")
    raise error


def store(db, station, rows):
    db.execute("CREATE TABLE IF NOT EXISTS knmi_temp "
               "(ts_utc TEXT, station TEXT, temp_c REAL, PRIMARY KEY (ts_utc, station))")
    db.execute("CREATE TABLE IF NOT EXISTS knmi_live (ts_utc TEXT PRIMARY KEY, rad_wm2 REAL, wind_ms REAL, "
               "wind_dir REAL, rain_mmh REAL, soil_c REAL)")
    with db:
        db.executemany("INSERT OR REPLACE INTO knmi_live VALUES (?, ?, ?, ?, ?, ?)",
                       ((t, *(r.get(name) for name in LIVE)) for t, r in rows.items()))
        return db.executemany("INSERT OR IGNORE INTO knmi_temp VALUES (?, ?, ?)",
                              ((t, station, r["ta"]) for t, r in rows.items() if "ta" in r)).rowcount


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(Path(__file__).with_name("p1.sqlite")))
    parser.add_argument("--station", default=STATION)
    parser.add_argument("--show", action="store_true", help="print the latest values, store nothing")
    parser.add_argument("--find", metavar="NAME", help="list stations whose name contains NAME")
    args = parser.parse_args()

    if args.find:
        return find(args.find)
    if args.show:
        print("time (UTC)                 " + "".join(f"{name:>7}" for name in ("ta", *LIVE)))
        for t, r in list(fetch(args.station).items())[-6:]:
            print(t, "".join(f"{r.get(name, '-'):>7}" for name in ("ta", *LIVE)))
        return
    db = sqlite3.connect(args.db, timeout=30)
    first = not db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'knmi_live'").fetchone()[0]
    added = store(db, args.station, fetch(args.station, 72 if first else 6))
    print(f"{datetime.now():%Y-%m-%d %H:%M} knmi {args.station}: +{added} rows")


if __name__ == "__main__":
    main()
