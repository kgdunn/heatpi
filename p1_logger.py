#!/usr/bin/env python3
"""Log HomeWizard P1 meter readings (local API v2) to SQLite.

Usage:
    python3 p1_logger.py token <ip>                 # then press the P1 meter's button
    python3 p1_logger.py log <ip> [--every 10] [--db p1.sqlite]

Standard library only, so it runs on a stock Raspberry Pi OS.
"""
import argparse
import json
import signal
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOKEN_FILE = Path.home() / ".p1_token"

# Fields from GET /api/measurement. All are optional: a single-phase house
# has no L2/L3, so missing values are stored as NULL.
COLUMNS = [
    "power_w", "power_l1_w", "power_l2_w", "power_l3_w",
    "energy_import_kwh", "energy_export_kwh", "average_power_15m_w",
]

# The meter's certificate is signed by HomeWizard for the hostname
# "appliance/p1dongle/<serial>", which urllib cannot check. On a home LAN
# we skip verification; HomeWizard's API docs explain how to pin their CA.
TLS = ssl.create_default_context()
TLS.check_hostname = False
TLS.verify_mode = ssl.CERT_NONE


def request(ip, path, token=None, body=None):
    """Call the P1 meter's v2 API and return the decoded JSON."""
    headers = {"X-Api-Version": "2", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"https://{ip}{path}", data=data, headers=headers)
    with urllib.request.urlopen(req, context=TLS, timeout=5) as resp:
        return json.load(resp)


def get_token(ip):
    """Ask for a token; the meter only grants one within 30 s of a button press."""
    print("Press the button on the P1 meter now...")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            token = request(ip, "/api/user", body={"name": "local/pi-logger"})["token"]
        except urllib.error.HTTPError as e:
            if e.code != 403:  # 403 = button not pressed yet, keep asking
                raise
            time.sleep(1)
            continue
        TOKEN_FILE.write_text(token)
        TOKEN_FILE.chmod(0o600)  # the token gives full access to the meter
        print(f"Token saved to {TOKEN_FILE}")
        return
    sys.exit("No button press seen within 60 s, try again.")


def to_row(m):
    """Flatten one measurement into a table row (Pi time in UTC + meter time)."""
    gas = next((x for x in m.get("external", []) if x.get("type") == "gas_meter"), {})
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        m.get("timestamp"),
        *(m.get(c) for c in COLUMNS),
        gas.get("value"),
        gas.get("timestamp"),
    )


def log(ip, db_path, every, flush_every=60):
    """Poll every `every` seconds; write to disk once a minute to spare the SD card."""
    token = TOKEN_FILE.read_text().strip()
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")  # lets you read the file while it logs
    db.execute(
        "CREATE TABLE IF NOT EXISTS p1 (ts_utc TEXT PRIMARY KEY, meter_ts TEXT, "
        + ", ".join(f"{c} REAL" for c in COLUMNS)
        + ", gas_m3 REAL, gas_ts TEXT)"
    )
    insert = f"INSERT OR IGNORE INTO p1 VALUES ({','.join('?' * (len(COLUMNS) + 4))})"
    buffer, last_flush = [], time.monotonic()

    def flush():
        nonlocal last_flush
        with db:
            db.executemany(insert, buffer)
        buffer.clear()
        last_flush = time.monotonic()

    # systemd stops services with SIGTERM: turn it into a normal exit so the
    # `finally` below still writes the last minute of data.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            try:
                buffer.append(to_row(request(ip, "/api/measurement", token)))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    sys.exit("Token rejected: run the 'token' command again.")
                print(f"HTTP {e.code}: {e.reason}", file=sys.stderr)
            except OSError as e:  # network down, timeout, meter rebooting
                print(f"Read failed: {e}", file=sys.stderr)
            if time.monotonic() - last_flush >= flush_every:
                flush()
            time.sleep(every - time.time() % every)  # stay on round seconds
    finally:
        flush()
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("token", help="get an API token").add_argument("ip")
    p = sub.add_parser("log", help="log measurements to SQLite")
    p.add_argument("ip")
    p.add_argument("--every", type=float, default=10, help="seconds between reads")
    p.add_argument("--db", default="p1.sqlite", help="SQLite file")
    args = parser.parse_args()

    if args.cmd == "token":
        get_token(args.ip)
    else:
        try:
            log(args.ip, args.db, args.every)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
