#!/usr/bin/env python3
"""Log a change to the heating setup, or list all logged changes.

Usage:  python3 note.py "heat curve 1.20 -> 1.00"    # add a note (time = now)
        python3 note.py --counters                   # asks for the Vaillant's two "this month" numbers
        python3 note.py                              # list all notes
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.argv[1:] == ["--counters"]:          # typed in, so an example number can never be logged by mistake
    heating = float(input("Heating, kWh this month: "))
    air = float(input("Environmental yield, kWh this month: "))
    sys.argv[1:] = [f"counters: heating={heating:g} yield={air:g}"]
db = sqlite3.connect(Path(__file__).with_name("p1.sqlite"))
db.execute("CREATE TABLE IF NOT EXISTS events (ts_utc TEXT, note TEXT)")
if len(sys.argv) > 1:
    with db:
        db.execute("INSERT INTO events VALUES (?, ?)",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"), " ".join(sys.argv[1:])))
for ts, note in db.execute("SELECT ts_utc, note FROM events ORDER BY ts_utc"):
    print(f"{datetime.fromisoformat(ts).astimezone():%a %d %b %H:%M}  {note}")  # in local time
