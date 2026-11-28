#!/usr/bin/env python3
"""The house's heat loss per kelvin (W/K) from every whole day on the Pi: an energy signature.

The forecast scales W/K on the last 3 days only, so one cold or sunny day moves it a lot. Here every
whole day counts. Per day:

    heat in  = heat pump (electricity x COP model) + appliances + people + sun     (kW, the day's mean)
    ΔT       = inside - outdoor                                                     (K, the day's mean)

and a straight line through the origin, heat in = W/K x ΔT, fitted by least squares. Its standard
error comes from the scatter of the days around the line. A second fit, with an intercept, is a check:
an intercept far from 0 means gains or sun are off, or the house is not in steady state.

Days are left out when they have under 23 hours of data, no sunshine data, a mean outdoor temperature
above --warm (the heating hardly runs), or a logged heat-curve change. Before 20 Nov 2026 there are no
room temperatures, so --inside stands in for them. Storage in floors and walls is not modelled: it
evens out over many days.

Usage:  python3 signature.py [--days 60] [--inside 19.5] [--warm 12] [--db p1.sqlite]
"""
import argparse
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from math import sqrt

from balance import IN, LOCAL, hourly, rows


def days_of(db_path, days, inside):
    """{local date: [hourly rows]} for the last `days` days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).astimezone(LOCAL)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    out = defaultdict(list)
    for row in hourly(db_path, since.astimezone(timezone.utc).isoformat(), inside=inside):
        out[row["when"].astimezone(LOCAL).date()].append(row)
    return out


def changed_days(db_path):
    """Local dates with a logged heat-curve or maximum-flow change: the heat pump was not in one state."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)) as db:
        notes = rows(db, "SELECT ts_utc FROM events WHERE note LIKE '%heat curve%' OR note LIKE '%max target flow%'")
    return {datetime.fromisoformat(ts).astimezone(LOCAL).date() for (ts,) in notes}


def fit(points):
    """Least squares for y = b x through the origin, and for y = a + b x. points: [(x, y)]."""
    n = len(points)
    sxx, sxy = sum(x * x for x, _ in points), sum(x * y for x, y in points)
    b0 = sxy / sxx
    se0 = sqrt(sum((y - b0 * x) ** 2 for x, y in points) / (n - 1) / sxx) if n > 1 else float("nan")
    mx, my = sum(x for x, _ in points) / n, sum(y for _, y in points) / n
    cxx = sum((x - mx) ** 2 for x, _ in points)
    b1 = sum((x - mx) * (y - my) for x, y in points) / cxx if cxx else float("nan")
    a1 = my - b1 * mx
    tot = sum((y - my) ** 2 for _, y in points)
    r2 = 1 - sum((y - b0 * x) ** 2 for x, y in points) / tot if tot else float("nan")
    return b0, se0, r2, a1, b1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=60, help="how far back to look (default 60)")
    parser.add_argument("--inside", type=float, default=19.5, help="°C for days without room temperatures")
    parser.add_argument("--warm", type=float, default=12.0, help="leave out days with a warmer mean outdoors")
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()

    skip = changed_days(args.db)
    print("day          h  out°C   ΔT   COP  heat in kW   W/K that day  rooms")
    points = []
    for day, group in sorted(days_of(args.db, args.days, args.inside).items()):
        hours, t_out = len(group), sum(row["t_out"] for row in group) / len(group)
        why = ("partial" if hours < 23 else "no sun data" if any(row["sun"] is None for row in group)
               else "warm" if t_out > args.warm else "setting changed" if day in skip else "")
        dt = sum(row["t_in"] - row["t_out"] for row in group) / hours
        heat = sum(row[k] or 0 for row in group for k in IN) / hours
        pump = sum(row["heat_pump"] for row in group)
        cop = pump / sum(row["heat_pump"] / row["cop"] for row in group) if pump else float("nan")
        measured = "measured" if all(row["t_in_measured"] for row in group) else \
            "partly measured" if any(row["t_in_measured"] for row in group) else f"{args.inside} assumed"
        print(f"{day:%a %d %b} {hours:2d}  {t_out:5.1f}  {dt:4.1f}  {cop:4.2f}  {heat:9.2f}   "
              f"{1000 * heat / dt if dt > 0 else float('nan'):10.0f}  {measured}{'   left out: ' + why if why else ''}")
        if not why and dt > 0:
            points.append((dt, heat))

    if len(points) < 3:
        raise SystemExit(f"\nOnly {len(points)} usable days: need at least 3.")
    w_per_k, se, r2, a, b = fit(points)
    print(f"\n{len(points)} days: W/K = {1000 * w_per_k:.0f} ± {1000 * se:.0f} (one standard error; R² {r2:.2f})")
    print(f"Check, with an intercept: heat in = {a:+.2f} kW + {1000 * b:.0f} W/K x ΔT "
          f"(an intercept near 0 says gains and sun are about right)")
    print("For comparison: the year's Vaillant counters gave 170-180 W/K; house.py accounts for about 160.")


if __name__ == "__main__":
    main()
