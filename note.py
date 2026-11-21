#!/usr/bin/env python3
"""Log a change to the heating setup, or list all logged changes.

Usage:  python3 note.py "heat curve 1.20 -> 1.00"    # add a note (time = now)
        python3 note.py                              # list all notes
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

db = sqlite3.connect(Path(__file__).with_name("p1.sqlite"))
db.execute("CREATE TABLE IF NOT EXISTS events (ts_utc TEXT, note TEXT)")
if len(sys.argv) > 1:
    with db:
        db.execute("INSERT INTO events VALUES (?, ?)",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"), " ".join(sys.argv[1:])))
for ts, note in db.execute("SELECT ts_utc, note FROM events ORDER BY ts_utc"):
    print(f"{datetime.fromisoformat(ts).astimezone():%a %d %b %H:%M}  {note}")  # in local time
