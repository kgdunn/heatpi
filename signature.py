#!/usr/bin/env python3
"""The house's heat loss per kelvin (W/K) and the sun's weight, from every whole day on the Pi.

The forecast scales W/K on the last 3 days only, so one cold or sunny day moves it a lot. Here every
whole day counts. Per day (means, kW and K):

    heating = heat pump (electricity x COP model) + appliances + people
    sun     = sunshine through the windows as house.py's window holes let it in (KNMI hourly)
    ΔT      = inside - outdoor

The balance  heating + weight x sun = W/K x ΔT,  divided by ΔT, is a straight line (Siviour's form,
as used for co-heating tests):

    heating / ΔT = W/K - weight x (sun / ΔT)

The intercept is W/K; the slope is minus the sun's weight (1: house.py's window holes are right).
It is fitted with the repeated median (process-improve's robust_regression), so a day with heat
going into or out of storage does not pull the line, and the 95 % intervals come from a bootstrap
of the days. For comparison, the median of the days' W/K holds the sun at house.py's weight.

Days are left out when they have under 23 hours of data, no sunshine data, a mean outdoor temperature
above --warm (the heating hardly runs), or a logged heat-curve change. Before 20 Nov 2026 there are no
room temperatures, so --inside stands in for them.

Needs process-improve, in its own venv so the loggers' packages stay as they are:
    python3 -m venv ~/analysis-venv && ~/analysis-venv/bin/pip install --only-binary :all: process-improve
Usage:  ~/analysis-venv/bin/python signature.py [--days 60] [--inside 19.5] [--warm 12] [--db p1.sqlite]
"""
import argparse
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone

import numpy as np
from process_improve.regression import robust_regression

from balance import LOCAL, hourly, rows

HEATING = ("heat_pump", "appliances", "people")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=60, help="how far back to look (default 60)")
    parser.add_argument("--inside", type=float, default=19.5, help="°C for days without room temperatures")
    parser.add_argument("--warm", type=float, default=12.0, help="leave out days with a warmer mean outdoors")
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()

    skip = changed_days(args.db)
    print("day          h  out°C   ΔT   COP  heating kW  sun kW   W/K that day  rooms")
    days = []                                                       # (ΔT, heating, sun) per usable day
    for day, group in sorted(days_of(args.db, args.days, args.inside).items()):
        hours, t_out = len(group), sum(row["t_out"] for row in group) / len(group)
        no_sun = any(row["sun"] is None for row in group)
        why = ("partial" if hours < 23 else "no sun data" if no_sun
               else "warm" if t_out > args.warm else "setting changed" if day in skip else "")
        dt = sum(row["t_in"] - row["t_out"] for row in group) / hours
        heating = sum(row[k] or 0 for row in group for k in HEATING) / hours
        sun = sum(row["sun"] or 0 for row in group) / hours
        pump = sum(row["heat_pump"] for row in group)
        cop = pump / sum(row["heat_pump"] / row["cop"] for row in group) if pump else float("nan")
        measured = "measured" if all(row["t_in_measured"] for row in group) else \
            "partly measured" if any(row["t_in_measured"] for row in group) else f"{args.inside} assumed"
        print(f"{day:%a %d %b} {hours:2d}  {t_out:5.1f}  {dt:4.1f}  {cop:4.2f}  {heating:9.2f}  "
              f"{'   ?' if no_sun else f'{sun:5.2f}'}   {1000 * (heating + sun) / dt if dt > 0 else float('nan'):10.0f}"
              f"  {measured}{'   left out: ' + why if why else ''}")
        if not why and dt > 0:
            days.append((dt, heating, sun))

    if len(days) < 5:
        raise SystemExit(f"\nOnly {len(days)} usable days: need at least 5.")
    dt, heating, sun = np.array(days).T
    fit = robust_regression(sun / dt, heating / dt, intervals="bootstrap")
    w_per_k, (w_lo, w_hi) = 1000 * fit["intercept"], 1000 * fit["conf_interval_intercept"]
    weight, (s_hi, s_lo) = -fit["coefficients"][0], -fit["conf_intervals"][0]
    ratios = 1000 * (heating + sun) / dt
    boots = np.median(np.random.default_rng(0).choice(ratios, size=(999, ratios.size)), axis=1)
    print(f"\n{len(days)} days, robust fits with 95 % bootstrap intervals:")
    print(f"  sun weight from the data:  W/K = {w_per_k:.0f} [{w_lo:.0f}, {w_hi:.0f}];  "
          f"sun weight {weight:.1f} [{s_lo:.1f}, {s_hi:.1f}]  (1: house.py's windows are right)")
    print(f"  sun as house.py has it:    W/K = {np.median(ratios):.0f} "
          f"[{np.percentile(boots, 2.5):.0f}, {np.percentile(boots, 97.5):.0f}]  (the median day)")
    print(f"  the days' sun / ΔT spans {1000 * (sun / dt).min():.0f}-{1000 * (sun / dt).max():.0f} W/K: "
          "the wider this, the better the data can tell the sun's weight")
    print("For comparison: the year's Vaillant counters gave 170-180 W/K; house.py accounts for about 160.")


if __name__ == "__main__":
    main()
