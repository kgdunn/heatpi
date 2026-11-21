#!/usr/bin/env python3
"""Two questions from the logged data.

1. Does the technical room get louder when the heat pump runs?
2. Does the heat pump use less electricity since the heat-curve change?

The two moments that matter are read from the events log (note.py):
the Pi's move to the technical room, and the heat-curve change.

Usage:  python3 insights.py
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from hp_check import EXTRA_W, runs

DB = Path(__file__).with_name("p1.sqlite")
TZ = "Europe/Amsterdam"


def event_time(db, words):
    """Time of the first logged note containing `words`, in local time."""
    row = db.execute("SELECT min(ts_utc) FROM events WHERE note LIKE ?", (f"%{words}%",)).fetchone()
    if row[0] is None:
        raise SystemExit(f"No note containing '{words}' in the events table.")
    return pd.Timestamp(row[0]).tz_convert(TZ)


def read(db, sql, since=None):
    """A table on a local-time index."""
    where, params = (" WHERE ts_utc >= ?", (since.tz_convert("UTC").isoformat(),)) if since else ("", ())
    df = pd.read_sql(sql + where, db, params=params)
    df.index = pd.to_datetime(df.pop("ts_utc"), utc=True).dt.tz_convert(TZ)
    return df


def heat_pump_minutes(db, since=None):
    """Per minute: is the heat pump running, and its electrical power (W) above L1's quiet level."""
    l1 = read(db, "SELECT ts_utc, power_l1_w FROM p1", since).power_l1_w.resample("1min").mean()
    extra = l1 - l1.quantile(0.05)
    on = runs(extra > EXTRA_W)          # same rule as hp_check.py: >= 10 min of steady extra load
    return pd.DataFrame({"on": on, "hp_w": extra.where(on, 0), "has_data": l1.notna()})


# ---------------------------------------------------------------- 1. loudness
def loudness_vs_heat_pump(db):
    moved = event_time(db, "Pi moved")
    hp = heat_pump_minutes(db, moved)
    loud = read(db, "SELECT ts_utc, mean AS loud FROM loudness", moved).loud
    d = hp.join(loud, how="inner").dropna()
    d["hour"] = d.index.hour

    print(f"1. LOUDNESS vs HEAT PUMP  (since the Pi moved, {moved:%a %d %b %H:%M}; {len(d)} minutes)")
    groups = d.groupby("on").loud
    print(f"   off: {groups.mean()[False]:6.0f} ± {groups.std()[False]:3.0f}  ({groups.size()[False]} min)")
    print(f"   on : {groups.mean()[True]:6.0f} ± {groups.std()[True]:3.0f}  ({groups.size()[True]} min)")
    print(f"   correlation (on/off vs loudness): r = {d.on.astype(float).corr(d.loud):+.2f}")

    # People make noise in the daytime, when the heat pump also runs most: compare
    # on vs off within the same hour of the day, then average those differences.
    by_hour = d.groupby(["hour", "on"]).loud.agg(["mean", "size"]).unstack("on").dropna()
    weight = by_hour["size"].min(axis=1)
    diff = by_hour["mean"][True] - by_hour["mean"][False]
    print(f"   same-hour difference (on − off): {np.average(diff, weights=weight):+.0f}"
          f"  from {len(diff)} hours of the day that have both")
    night = d[d.hour < 6]
    if night.on.nunique() == 2:
        n = night.groupby("on").loud.mean()
        print(f"   night only (00–06 h), on − off: {n[True] - n[False]:+.0f}")

    # Event study: average loudness around every start and stop.
    for name, edge in (("start", d.on & ~d.on.shift(fill_value=False)),
                       ("stop", ~d.on & d.on.shift(fill_value=False))):
        times = d.index[edge]
        lags = range(-10, 21, 5)
        rows = [[loud.get(t + pd.Timedelta(minutes=k)) for k in lags] for t in times]
        if rows:
            avg = pd.DataFrame(rows, columns=list(lags)).mean().round()
            print(f"   around a {name:5} ({len(times)} events), minutes → loudness: "
                  + "  ".join(f"{k:+d}:{v:.0f}" for k, v in avg.items()))


# ---------------------------------------------------------------- 2. energy
def energy_before_after(db):
    change = event_time(db, "heat curve 1.85")
    hp = heat_pump_minutes(db)
    out = read(db, "SELECT ts_utc, temp_c FROM street_consensus").temp_c
    daily = pd.DataFrame({
        "kWh": hp.hp_w.resample("D").sum() / 60 / 1000,
        "outdoor_C": out.resample("D").mean(),
        "coverage": hp.has_data.resample("D").sum() / 1440,   # share of the day's minutes with data
    }).dropna()
    daily = daily[daily.coverage > 0.95]                  # whole days only
    day = daily.index.normalize()
    before = daily[day < change.normalize()]
    after = daily[day > change.normalize()]               # the change day itself is mixed: skipped

    print(f"\n2. ENERGY BEFORE vs AFTER the curve change ({change:%a %d %b %H:%M})")
    if len(before) < 4 or not len(after):
        print(f"   Not enough whole days yet: {len(before)} before, {len(after)} after.")
        return
    # Baseline: before-days' kWh as a straight line in outdoor temperature (an energy signature).
    b, a = np.polyfit(before.outdoor_C, before.kWh, 1)
    resid = before.kWh - (a + b * before.outdoor_C)
    s = resid.std(ddof=2)
    r2 = 1 - resid.var() / before.kWh.var()
    print(f"   baseline from {len(before)} days: kWh/day = {a:.1f} {b:+.2f} × outdoor °C"
          f"   (R² = {r2:.2f}, day-to-day scatter ± {s:.1f} kWh)")

    table = daily.assign(expected=a + b * daily.outdoor_C)
    table["saved_%"] = 100 * (1 - table.kWh / table.expected)
    table["period"] = np.where(day < change.normalize(), "before",
                               np.where(day > change.normalize(), "after", "change"))
    table.index = table.index.strftime("%a %d %b")
    print(table[["period", "outdoor_C", "kWh", "expected", "saved_%"]].round(1).to_string(index_names=False))

    expected = (a + b * after.outdoor_C).mean()
    saved = expected - after.kWh.mean()
    margin = 2 * s / np.sqrt(len(after))                  # rough 95% band for the mean of n days
    print(f"   after the change: {saved:+.1f} ± {margin:.1f} kWh/day saved "
          f"({100 * saved / expected:+.0f}% of expected), from {len(after)} days")


def main():
    db = sqlite3.connect(DB)
    loudness_vs_heat_pump(db)
    energy_before_after(db)


if __name__ == "__main__":
    main()
