#!/usr/bin/env python3
"""Recent overview: power, heat pump, technical-room climate and loudness.

Usage:  python3 recent.py [--hours 6] [--step 20]
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd

DB = Path(__file__).with_name("p1.sqlite")
HP_ON_W = 500  # L1 above this = heat pump running (quiet ~60-90 W, running >1.2 kW)
QUERIES = {
    "p1": "SELECT ts_utc, power_w AS total_W, power_l1_w AS L1_W FROM p1 WHERE ts_utc >= ?",
    "room": "SELECT ts_utc, temp_c AS room_C, rh AS rh_pct FROM room WHERE ts_utc >= ?",
    "loudness": "SELECT ts_utc, mean AS loud, p95 AS loud_p95 FROM loudness WHERE ts_utc >= ?",
}


def load(db, sql, since):
    """One table as a DataFrame on local time; empty if the table doesn't exist yet."""
    try:
        df = pd.read_sql(sql, db, params=(since,))
    except pd.errors.DatabaseError:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.pop("ts_utc")).dt.tz_convert("Europe/Amsterdam")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=6)
    parser.add_argument("--step", type=int, default=20, help="minutes per row")
    args = parser.parse_args()

    db = sqlite3.connect(DB)
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=args.hours)).isoformat()
    frames = {name: load(db, sql, since) for name, sql in QUERIES.items()}

    p1 = frames["p1"]  # share of each step the heat pump ran, from the 10 s readings
    p1["hp_on_pct"] = 100 * (p1.L1_W > HP_ON_W)

    table = pd.concat([f.resample(f"{args.step}min").mean() for f in frames.values() if len(f)], axis=1)
    whole = table.columns.drop("room_C", errors="ignore")   # everything but °C as integers
    table[whole] = table[whole].round().astype("Int64")
    if "room_C" in table:
        table["room_C"] = table.room_C.round(1)
    table.index = table.index.strftime("%a %H:%M")
    print(table.rename_axis(None).to_string(na_rep="-"))


if __name__ == "__main__":
    main()
