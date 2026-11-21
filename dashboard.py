#!/usr/bin/env python3
"""Tiny home dashboard: serves web/ and the logged data as JSON, read-only.

Open http://heatpi.lan:8080 on any device at home. The page draws its plots in
the browser with Plotly from a CDN, so the Pi only sends a few kB of data.

    GET /                      -> web/index.html
    GET /api/<series>?hours=24 -> {"time": [...], "<column>": [...], ...}  (local time)

Usage:  python3 dashboard.py [--port 8080]
"""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
DB = HERE / "p1.sqlite"
LOCAL = ZoneInfo("Europe/Amsterdam")
MAX_HOURS = 24 * 31

# The only SQL the web can trigger: named, read-only queries. One line per new series.
QUERIES = {
    "zones": "SELECT ts_utc, zone, temp_c, setpoint_c FROM zones WHERE ts_utc >= ? ORDER BY ts_utc",
    "room": "SELECT ts_utc, temp_c, rh FROM room WHERE ts_utc >= ? ORDER BY ts_utc",
    "power": "SELECT substr(ts_utc, 1, 16) || ':00+00:00' AS ts_utc, avg(power_w) AS total_w, "
             "avg(power_l1_w) AS l1_w FROM p1 WHERE ts_utc >= ? GROUP BY 1 ORDER BY 1",  # 1-min means
    "outdoor": "SELECT ts_utc, temp_c FROM street_consensus WHERE ts_utc >= ? ORDER BY ts_utc",
    "knmi": "SELECT ts_utc, temp_c FROM knmi_temp WHERE ts_utc >= ? ORDER BY ts_utc",
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


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return super().do_GET()              # static files from web/
        name = url.path.removeprefix("/api/")
        if name not in QUERIES:
            return self.send_error(404, f"Unknown series; try one of {sorted(QUERIES)}")
        try:
            hours = min(float(parse_qs(url.query).get("hours", ["24"])[0]), MAX_HOURS)
            body = json.dumps(series(name, hours)).encode()
        except ValueError:
            return self.send_error(400, "hours must be a number")
        except sqlite3.OperationalError as e:    # e.g. a table that isn't logged yet
            return self.send_error(503, str(e))
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
