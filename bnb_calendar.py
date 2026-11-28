#!/usr/bin/env python3
"""Store the home above's booking calendar (the host's iCal export) in SQLite: when it has guests.

Keeps only the dates and the kind of each entry: "reserved" (guests) or "blocked" (closed by the
host). Guest details in the feed (reservation links, phone digits) are never stored. The feed is
the truth from today on: future entries are replaced each run, so a cancellation disappears;
past entries stay as the history.

The export link holds a token: it lives in ~/.airbnb_ical (one line, chmod 600), never in the repo.
    printf '%s\\n' 'https://www.airbnb.com/calendar/ical/....ics?t=...' > ~/.airbnb_ical && chmod 600 ~/.airbnb_ical

Usage:  python3 bnb_calendar.py [--db p1.sqlite]      (cron: every 6 hours)
"""
import argparse
import sqlite3
import urllib.request
from datetime import date, datetime
from pathlib import Path

LINK = Path.home() / ".airbnb_ical"


def events(text):
    """[(first night, day of leaving, kind)] as ISO dates from an iCal text."""
    lines, out, event = text.replace("\r\n ", "").replace("\n ", "").splitlines(), [], None   # unfold
    for line in lines:
        key, _, value = line.partition(":")
        name = key.split(";")[0]
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT" and event is not None:
            if "DTSTART" in event and "DTEND" in event:
                out.append((event["DTSTART"], event["DTEND"],
                            "reserved" if event.get("SUMMARY") == "Reserved" else "blocked"))
            event = None
        elif event is not None and name in ("DTSTART", "DTEND"):
            event[name] = datetime.strptime(value[:8], "%Y%m%d").date().isoformat()
        elif event is not None and name == "SUMMARY":
            event[name] = value.strip()
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()
    if not LINK.exists():
        raise SystemExit(f"Put the calendar's export link in {LINK} (see --help)")
    request = urllib.request.Request(LINK.read_text().strip(), headers={"User-Agent": "heatpi"})
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode()
    if "BEGIN:VCALENDAR" not in text:              # an error page: leave the stored calendar as it is
        raise SystemExit("The calendar link did not return a calendar")
    found, today = events(text), date.today().isoformat()

    db = sqlite3.connect(args.db, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS bookings (first_night TEXT, leave_day TEXT, kind TEXT, "
               "PRIMARY KEY (first_night, leave_day, kind))")
    with db:
        db.execute("DELETE FROM bookings WHERE leave_day > ?", (today,))     # the feed decides from today on
        db.executemany("INSERT OR IGNORE INTO bookings VALUES (?, ?, ?)", found)
    for first, leave, kind in db.execute("SELECT * FROM bookings WHERE leave_day > ? ORDER BY 1", (today,)):
        print(f"{first} to {leave}  {kind}")


if __name__ == "__main__":
    main()
