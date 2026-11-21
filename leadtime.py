#!/usr/bin/env python3
"""How long each room takes to reach a raised evohome setpoint, and under what conditions.

For every step up in a zone's setpoint (e.g. 16 -> 19 °C in the morning) it finds
the first reading at or near the new setpoint, and reports the lead time, the
warm-up rate, the outdoor temperature and how hard the heat pump worked meanwhile.

Usage:  python3 leadtime.py [--days 7] [--step-up 1.0] [--near 0.25]
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd

DB = Path(__file__).with_name("p1.sqlite")
TZ = "Europe/Amsterdam"
HP_ON_W = 500  # L1 above this = heat pump running


def read(db, sql, since):
    df = pd.read_sql(sql + " WHERE ts_utc >= ?", db, params=(since,))
    df.index = pd.to_datetime(df.pop("ts_utc"), utc=True).dt.tz_convert(TZ)
    return df


def warmups(zone, step_up, near):
    """Yield one dict per setpoint step-up in a single zone's readings."""
    jumps = zone.setpoint_c.diff() >= step_up
    changes = zone.index[zone.setpoint_c.diff().fillna(0) != 0]
    for t0 in zone.index[jumps]:
        target = zone.setpoint_c[t0]
        end = changes[changes > t0].min() if (changes > t0).any() else zone.index[-1]
        window = zone[t0:end]
        reached = window.index[window.temp_c >= target - near]
        start_temp = zone.temp_c[t0]
        t1 = reached[0] if len(reached) else None
        hours = ((t1 or end) - t0).total_seconds() / 3600
        yield {"start": t0, "setpoint": f"{zone.setpoint_c.shift()[t0]:g}->{target:g}",
               "room_at_start": start_temp, "gap_K": target - start_temp,
               "lead_h": round(hours, 1) if t1 else f">{hours:.1f}",
               "rate_K_per_h": round((target - start_temp) / hours, 2) if t1 and hours else None,
               "until": t1 or end}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--step-up", type=float, default=1.0, help="°C rise that counts as a step up")
    parser.add_argument("--near", type=float, default=0.25, help="°C below setpoint that counts as reached")
    args = parser.parse_args()

    db = sqlite3.connect(DB)
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.days)).isoformat()
    zones = read(db, "SELECT ts_utc, zone, temp_c, setpoint_c FROM zones", since)
    l1 = read(db, "SELECT ts_utc, power_l1_w FROM p1", since).power_l1_w
    outdoor = read(db, "SELECT ts_utc, temp_c FROM street_consensus", since).temp_c

    events = []
    for name, zone in zones.groupby("zone"):
        for e in warmups(zone.drop(columns="zone"), args.step_up, args.near):
            span = slice(e["start"], e.pop("until"))
            e.update(zone=name,
                     outdoor_C=outdoor[span].mean(),                  # street sensors lag 1-2 h: may be empty
                     hp_kW=l1[span].mean() / 1000,
                     hp_on_pct=100 * (l1[span] > HP_ON_W).mean())
            events.append(e)
    if not events:
        print(f"No setpoint step-ups of >= {args.step_up} °C in the last {args.days:g} days yet.")
        return

    table = pd.DataFrame(events).sort_values(["start", "zone"])
    table["start"] = table.start.dt.strftime("%a %d %b %H:%M")
    cols = ["start", "zone", "setpoint", "room_at_start", "gap_K", "lead_h", "rate_K_per_h",
            "outdoor_C", "hp_kW", "hp_on_pct"]
    print(table[cols].round(1).to_string(index=False, na_rep="-"))


if __name__ == "__main__":
    main()
