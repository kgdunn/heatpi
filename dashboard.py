#!/usr/bin/env python3
"""Tiny home dashboard: serves web/ and the logged data as JSON, read-only.

Open http://heatpi.lan:8080 on any device at home. The page draws its plots in
the browser with Plotly from a CDN, so the Pi only sends a few kB of data.

    GET /                      -> web/index.html   (live view; web/balance.html is the heat balance)
    GET /api/<series>?hours=24 -> {"time": [...], "<column>": [...], ...}  (local time)
    GET /api/balance?hours=336 -> the heat balance per day (balance.py)
    GET /api/schedule          -> every zone's newest weekly schedule (web/schedule.html)

Usage:  python3 dashboard.py [--port 8080]
"""
import argparse
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import balance

HERE = Path(__file__).parent
DB = HERE / "p1.sqlite"
LOCAL = ZoneInfo("Europe/Amsterdam")
MAX_HOURS = 24 * 31

# The only SQL the web can trigger: named, read-only queries. One line per new series.
QUERIES = {
    "zones": "SELECT ts_utc, zone, temp_c, setpoint_c, zone_mode FROM zones WHERE ts_utc >= ? ORDER BY ts_utc",
    "room": "SELECT ts_utc, temp_c, rh FROM room WHERE ts_utc >= ? ORDER BY ts_utc",
    "power": "SELECT substr(ts_utc, 1, 16) || ':00+00:00' AS ts_utc, avg(power_w) AS total_w, "
             "avg(power_l1_w) AS l1_w FROM p1 WHERE ts_utc >= ? GROUP BY 1 ORDER BY 1",  # 1-min means
    "outdoor": "SELECT ts_utc, temp_c FROM street_consensus WHERE ts_utc >= ? ORDER BY ts_utc",
    "knmi": "SELECT ts_utc, temp_c FROM knmi_temp WHERE ts_utc >= ? ORDER BY ts_utc",
    "loudness": "SELECT ts_utc, mean FROM loudness WHERE ts_utc >= ? ORDER BY ts_utc",  # 1-min means
}


def series(name, hours):
    """Query result as columns, with UTC timestamps turned into local wall-clock time."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as db:
        cursor = db.execute(QUERIES[name], (since,))
        names = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    data = {n: list(col) for n, col in zip(names, zip(*rows))} if rows else {n: [] for n in names}
    data["time"] = [datetime.fromisoformat(t).astimezone(LOCAL).strftime("%Y-%m-%d %H:%M:%S")
                    for t in data.pop("ts_utc")]
    return data


def energy(hours):
    """Heat-pump circuit (L1) energy in kWh since local midnight, per minute; back to 0 at 00:00.
    Each row of the "power" query is a 1-minute mean, so it adds watts / 60 / 1000 kWh. Summing
    starts at the midnight before the window, so the first, partly shown day is right too."""
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    midnight = start.astimezone(LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as db:
        rows = db.execute(QUERIES["power"], (midnight.astimezone(timezone.utc).isoformat(),)).fetchall()
    out, day, kwh = {"time": [], "kwh": []}, None, 0.0
    for ts, _, l1_w in rows:
        local = datetime.fromisoformat(ts).astimezone(LOCAL)
        if local.date() != day:
            day, kwh = local.date(), 0.0
        kwh += (l1_w or 0) / 60 / 1000
        if local >= start:
            out["time"].append(local.strftime("%Y-%m-%d %H:%M:%S"))
            out["kwh"].append(round(kwh, 3))
    return out


def schedule(hours):
    """Every zone's newest stored weekly schedule (evohome_logger.py reads it every 6 hours), as columns:
    zone, day (0 = Monday), start ("HH:MM", local), setpoint_c, and when it was read (local time).
    `hours` is not used: a schedule has no time window."""
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT zone, day, start, setpoint_c, fetched_utc FROM schedule s WHERE fetched_utc = "
                          "(SELECT max(fetched_utc) FROM schedule WHERE zone = s.zone) ORDER BY zone, day, start"
                          ).fetchall()
    names = ("zone", "day", "start", "setpoint_c", "read")
    data = {n: list(col) for n, col in zip(names, zip(*rows))} if rows else {n: [] for n in names}
    data["read"] = [datetime.fromisoformat(t).astimezone(LOCAL).strftime("%Y-%m-%d %H:%M") for t in data["read"]]
    return data


# Series built in Python rather than in one query.
COMPUTED = {"energy": energy, "balance": lambda hours: balance.daily(DB, hours), "schedule": schedule}


def version():
    """The code the Pi runs: its git commit's short hash, time (Unix seconds) and message.
    Asked on every call, so it is right straight after update.sh, even without a restart."""
    try:
        out = subprocess.run(["git", "-C", str(HERE), "log", "-1", "--format=%h%n%ct%n%s"],
                             capture_output=True, text=True, timeout=5, check=True).stdout.split("\n")
        return {"commit": out[0], "time": int(out[1]), "subject": out[2]}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):  # no git, or not a checkout
        return {}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/version":
            return self.send_json(version())
        if not url.path.startswith("/api/"):
            return super().do_GET()              # static files from web/
        name = url.path.removeprefix("/api/")
        if name not in QUERIES and name not in COMPUTED:
            return self.send_error(404, f"Unknown series; try one of {sorted([*QUERIES, *COMPUTED])}")
        try:
            hours = min(float(parse_qs(url.query).get("hours", ["24"])[0]), MAX_HOURS)
            data = COMPUTED[name](hours) if name in COMPUTED else series(name, hours)
        except ValueError:
            return self.send_error(400, "hours must be a number")
        except sqlite3.OperationalError as e:    # e.g. a table that isn't logged yet
            return self.send_error(503, str(e))
        self.send_json(data)

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                # keep the journal quiet
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    handler = partial(Handler, directory=str(HERE / "web"))
    with ThreadingHTTPServer(("", args.port), handler) as server:
        print(f"Dashboard on http://heatpi.lan:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
