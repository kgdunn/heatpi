#!/usr/bin/env python3
"""Sudden jumps in power per phase: which appliance is on which phase, and how long it runs.

A heater (oven, kettle, dishwasher, hot water) switches on and off in one step of a fixed size; the
heat pump ramps. So a step up followed later by a step down of about the same size, on the same
phase, is one run of one appliance, whatever else is running on that phase at the time.

Usage:  python3 steps.py                      # every jump over 700 W in the last 3 hours
        python3 steps.py --hours 12 --runs    # matching pairs of jumps: runs of 3 minutes or longer
"""
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

PHASES = ("l1", "l2", "l3")


def steps(db, phase, since, watts):
    """[(time, watts before, watts after)] for jumps of more than `watts` between two 10 s readings."""
    rows = db.execute(f"""SELECT ts_utc, was, w FROM (
                              SELECT ts_utc, power_{phase}_w AS w, lag(power_{phase}_w) OVER (ORDER BY ts_utc) AS was
                              FROM p1 WHERE ts_utc >= ?)
                          WHERE abs(w - was) > ?""", (since, watts))
    return [(datetime.fromisoformat(t).astimezone(), was, w) for t, was, w in rows]   # local time


def runs(jumps, tolerance=0.25):
    """Pair each step down with the newest open step up of about the same size (within `tolerance`):
    [(start, end, mean watts)]. Steps that find no partner are left out."""
    out, open_ups = [], []
    for when, was, w in jumps:
        size = w - was
        if size > 0:
            open_ups.append((when, size))
            continue
        for i in range(len(open_ups) - 1, -1, -1):            # newest first: a kettle inside a longer run
            start, up = open_ups[i]
            if abs(up + size) <= tolerance * (up - size) / 2:
                out.append((start, when, (up - size) / 2))
                del open_ups[i]
                break
    return sorted(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=3, help="how far back to look (default 3)")
    parser.add_argument("--watts", type=float, default=700, help="smallest jump that counts (default 700)")
    parser.add_argument("--runs", action="store_true", help="show matching pairs of jumps as appliance runs")
    parser.add_argument("--minutes", type=float, default=3, help="with --runs: shortest run shown (default 3)")
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()

    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    found = {phase: steps(db, phase, since, args.watts) for phase in PHASES}
    if args.runs:
        print("phase  start     end       min    watts   kWh")
        rows = [(start, phase, end, watts) for phase in PHASES for start, end, watts in runs(found[phase])
                if end - start >= timedelta(minutes=args.minutes)]
        for start, phase, end, watts in sorted(rows):
            hours = (end - start).total_seconds() / 3600
            print(f"{phase.upper()}     {start:%H:%M:%S}  {end:%H:%M:%S}  {60 * hours:5.1f}  {watts:6.0f}"
                  f"  {watts * hours / 1000:5.2f}")
    else:
        print("time      phase   from     to    step (W)")
        for when, phase, was, w in sorted((when, phase, was, w) for phase in PHASES for when, was, w in found[phase]):
            print(f"{when:%H:%M:%S}  {phase.upper()}    {was:5.0f}  {w:5.0f}  {w - was:+6.0f}")


if __name__ == "__main__":
    main()
