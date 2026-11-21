#!/usr/bin/env python3
"""How far each evohome room is from its setpoint: room temperature minus setpoint.

Positive = warmer than asked (heat wasted), negative = colder than asked.

Usage:  python3 deviation.py [--hours 24] [--step 60]
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd

DB = Path(__file__).with_name("p1.sqlite")
BAND = 0.5  # °C: within this of the setpoint counts as "on target"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--step", type=int, default=60, help="minutes per row")
    args = parser.parse_args()

    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=args.hours)).isoformat()
    z = pd.read_sql("SELECT ts_utc, zone, temp_c, setpoint_c FROM zones WHERE ts_utc >= ?",
                    sqlite3.connect(DB), params=(since,))
    z["ts"] = pd.to_datetime(z.ts_utc, utc=True).dt.tz_convert("Europe/Amsterdam")
    z["dev"] = z.temp_c - z.setpoint_c

    # Per zone: averages, and the share of time above, on, and below target.
    summary = z.groupby("zone").agg(temp=("temp_c", "mean"), setpoint=("setpoint_c", "mean"),
                                    dev=("dev", "mean"))
    summary["above_%"] = z.dev.gt(BAND).groupby(z.zone).mean() * 100
    summary["on_%"] = z.dev.abs().le(BAND).groupby(z.zone).mean() * 100
    summary["below_%"] = z.dev.lt(-BAND).groupby(z.zone).mean() * 100
    print(f"Last {args.hours:g} h, room minus setpoint (°C); 'on' = within ±{BAND} °C\n")
    print(summary.round(1).rename_axis(None).to_string(), "\n")

    # Over time: one column per zone.
    table = (z.set_index("ts").groupby("zone").dev.resample(f"{args.step}min").mean()
              .unstack("zone").round(1))
    table.index = table.index.strftime("%a %H:%M")
    print(table.rename_axis(None).rename_axis(None, axis=1).to_string(na_rep="-"))


if __name__ == "__main__":
    main()
