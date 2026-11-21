#!/usr/bin/env python3
"""Did the heat pump run? Finds it in the P1 data and summarises the last days.

Idea: the heat pump is a long, steady extra load on one phase. Kettles and
toasters are big but short, so only stretches of at least 10 minutes with
more than 400 W above the phase's quiet level count as "heat pump running".
(An oven or washing machine heating up can still sneak in: treat as a first look.)

Usage:  python3 hp_check.py [--days 7]
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd

EXTRA_W, MIN_RUN = 400, 10           # watts above the quiet level, minutes in a row
LEVELS, BINS = " .:-=+*#", [0.1, 0.3, 0.6, 1.0, 1.5, 2.0, 2.5]   # kW per symbol


def runs(on, min_len=MIN_RUN):
    """True inside stretches of `on` lasting at least min_len minutes."""
    stretch = (on != on.shift()).cumsum()
    return on & (on.groupby(stretch).transform("size") >= min_len)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()
    db = sqlite3.connect(args.db)

    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.days + 1)).isoformat()
    p1 = pd.read_sql("SELECT ts_utc, power_l1_w, power_l2_w, power_l3_w FROM p1 WHERE ts_utc >= ?",
                     db, params=(since,))
    p1.index = pd.to_datetime(p1.pop("ts_utc"), utc=True).dt.tz_convert("Europe/Amsterdam")
    m = p1.resample("1min").mean()                      # 1-minute averages per phase

    # Which phase? The one with the most minutes of long, steady extra load.
    quiet = m.quantile(0.05)                            # each phase's quiet level
    extra = m - quiet
    long_on = {p: runs(extra[p] > EXTRA_W) for p in m}
    phase = max(long_on, key=lambda p: long_on[p].sum())
    on, hp_w = long_on[phase], extra[phase].where(long_on[phase], 0)
    print(f"Heat pump appears to be on {phase[6:8].upper()} "
          f"(quiet level {quiet[phase]:.0f} W); minutes of long load per phase: "
          + ", ".join(f"{p[6:8].upper()} {int(v.sum())}" for p, v in long_on.items()))

    # Daily summary, with outdoor temperature when available.
    starts = on & ~on.shift(fill_value=False)
    daily = pd.DataFrame({
        "run_h": on.resample("D").sum() / 60,
        "starts": starts.resample("D").sum(),
        "kWh": hp_w.resample("D").sum() / 60 / 1000,
        "peak_kW": hp_w.resample("D").max() / 1000,
    })
    try:
        out = pd.read_sql("SELECT ts_utc, temp_c FROM street_consensus", db)
        out.index = pd.to_datetime(out.pop("ts_utc"), utc=True).dt.tz_convert("Europe/Amsterdam")
        daily["outdoor_C"] = out.temp_c.resample("D").mean()
    except Exception:
        pass
    daily = daily.tail(args.days)
    daily.index = daily.index.strftime("%a %d %b").rename(None)
    print("\n" + daily.round(1).to_string())

    # Hour-by-hour picture: one character per hour, darker = more heat-pump power.
    hourly = (hp_w / 1000).resample("h").mean().tail(24 * args.days)
    print(f"\nHeat pump power per hour  ( {LEVELS[1]} <0.3  {LEVELS[3]} <1  {LEVELS[5]} <2  {LEVELS[7]} 2.5+ kW,  ? no data )")
    print("           0     6     12    18")
    for day, row in hourly.groupby(hourly.index.date):
        by_hour = row.groupby(row.index.hour).mean().reindex(range(24))
        line = "".join("?" if np.isnan(v) else LEVELS[np.searchsorted(BINS, v, side="right")]
                       for v in by_hour)
        print(f"{day:%a %d %b} |{line}|")


if __name__ == "__main__":
    main()
