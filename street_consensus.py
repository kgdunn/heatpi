#!/usr/bin/env python3
"""Combine the street sensors into one sun-robust outdoor temperature per hour.

Sun only ever pushes a sensor's reading UP, so:
  1. calibrate each sensor's siting offset in the dark (rolling 14-day median),
  2. in sunlit hours, take the coolest quarter as the honest reference and drop
     sensors more than 1 °C above it (they're in the sun); average the rest.
Also stores our own sensor's excess over the consensus: a free measure of
sunshine on our facade, for the heat-loss model.

Usage:  python3 street_consensus.py [--db p1.sqlite]
Needs:  pandas (sudo apt install -y python3-pandas)
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd

OURS = "de-4190"
WALL_COUPLED = ["de-1093", "de-4064"]  # read the wall as much as the air
LAT, LON = 52.012, 4.368               # Delft


def sun_elevation(ts, lat=LAT, lon=LON):
    """Solar elevation in degrees for UTC timestamps (approximation, ~0.5°)."""
    jd = (ts - pd.Timestamp("2000-01-01T12:00Z")).total_seconds().to_numpy() / 86400
    g = np.radians((357.529 + 0.98560028 * jd) % 360)
    q = (280.459 + 0.98564736 * jd) % 360
    ecl_lon = np.radians(q + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g))
    tilt = np.radians(23.439 - 0.00000036 * jd)
    ra = np.arctan2(np.cos(tilt) * np.sin(ecl_lon), np.cos(ecl_lon))
    dec = np.arcsin(np.sin(tilt) * np.sin(ecl_lon))
    hour_angle = np.radians((18.697374558 + 24.06570982441908 * jd) % 24 * 15 + lon) - ra
    lat = np.radians(lat)
    return np.degrees(np.arcsin(np.sin(lat) * np.sin(dec)
                                + np.cos(lat) * np.cos(dec) * np.cos(hour_angle)))


def consensus(temps, in_sun=1.0):
    """temps: hourly °C, one column per sensor (UTC index). Returns a DataFrame."""
    half = pd.Timedelta(minutes=30)  # an hourly mean spans either side of its label
    elev = np.maximum(sun_elevation(temps.index - half), sun_elevation(temps.index + half))
    sunlit = pd.Series(elev > 0, index=temps.index)
    dark = pd.Series(elev < -6, index=temps.index)  # past civil twilight

    # 1. Each sensor's siting offset from the group, learned in the dark.
    dev = temps.sub(temps.median(axis=1), axis=0)
    dev.loc[~dark] = np.nan
    offset = dev.rolling("14D", min_periods=24).median().bfill().ffill().fillna(0)
    t = temps - offset

    # 2. In sunlit hours keep only sensors near the cool side.
    ref = t.quantile(0.25, axis=1)
    keep = t.le(ref + in_sun, axis=0)
    keep.loc[~sunlit] = True
    used = t.where(keep)
    out = pd.DataFrame({
        "temp_c": used.mean(axis=1).round(2),
        "n_sensors": used.notna().sum(axis=1),
        "sun_elev": np.round(elev, 1),
    })
    if OURS in t:
        out["facade_excess"] = (t[OURS] - out.temp_c).round(2)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite")
    parser.add_argument("--exclude", nargs="*", default=WALL_COUPLED, help="sensors to leave out")
    args = parser.parse_args()

    db = sqlite3.connect(args.db)
    long = pd.read_sql("SELECT ts_utc, sensor, temp_c FROM street_temp", db)
    long = long[~long.sensor.isin(args.exclude)]
    temps = long.pivot(index="ts_utc", columns="sensor", values="temp_c")
    temps.index = pd.to_datetime(temps.index, utc=True)
    temps = temps.sort_index()

    out = consensus(temps)
    out.index = out.index.map(lambda ts: ts.isoformat())
    out.rename_axis("ts_utc").to_sql("street_consensus", db, if_exists="replace")
    print(f"{len(out)} hours, {out.index[0][:10]} → {out.index[-1][:16]}, "
          f"median {out.n_sensors.median():.0f} sensors per hour")
    print(out.tail(6).to_string())


if __name__ == "__main__":
    main()
