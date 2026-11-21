#!/usr/bin/env python3
"""Compare the street sensor (Ohnics OHN_de-4190) with KNMI Rotterdam, hour by hour.

Usage (Mac or Pi, needs internet):
    sudo apt install -y python3-pandas python3-matplotlib   # Pi; on a Mac: pip install pandas matplotlib
    python3 compare_temps.py --start 2026-08-01 --end 2026-10-31

Prints a summary and writes compare_temps.png and compare_temps.csv.
Needs outdoor_temp.py in the same folder (it fetches the street sensor).
"""
import argparse
import json
import urllib.parse
import urllib.request

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from outdoor_temp import SENSORS, datastreams, observations

KNMI_URL = "https://www.daggegevens.knmi.nl/klimatologie/uurgegevens"
SUNLIT = 10  # J/cm² of global radiation in the hour (~30 W/m²): "the sun is up"
STREET, KNMI, SUN, INK, MUTED = "#2a78d6", "#eb6834", "#eda100", "#0b0b0b", "#52514e"


def knmi_hourly(start, end, station=344):
    """KNMI hourly data in UTC: T at the end of each hour, sunshine energy and fog flag."""
    form = urllib.parse.urlencode({
        "stns": station, "vars": "T:Q:M", "fmt": "json",
        "start": f"{start:%Y%m%d}01", "end": f"{end:%Y%m%d}24",  # hours 1-24 of every day
    }).encode()
    with urllib.request.urlopen(KNMI_URL, data=form, timeout=120) as resp:
        raw = pd.DataFrame(json.load(resp))
    # Hour HH of a day ends at HH:00 UT, so hour 24 is midnight of the next day.
    ts = pd.to_datetime(raw["date"].astype(str), utc=True).dt.normalize() + pd.to_timedelta(raw["hour"], unit="h")
    num = lambda col: pd.to_numeric(raw[col], errors="coerce")
    return pd.DataFrame({"knmi": num("T") / 10, "sun": num("Q"), "fog": num("M")}).set_index(pd.DatetimeIndex(ts))


def street_hourly(start, end):
    """Hourly street-sensor temperature in UTC, from RIVM Samen Meten."""
    temp_stream = datastreams(SENSORS["de-4190"])["temp"]
    rows = observations(temp_stream, f"{start:%Y-%m-%d}T00:00:00+00:00")
    s = pd.DataFrame(rows, columns=["ts", "street"])
    s.index = pd.to_datetime(s.pop("ts"), utc=True)
    return s[s.index < end + pd.Timedelta(days=1)]


def align(street, knmi):
    """Join the two series, picking the hour shift that fits best in the dark (no sun error)."""
    mismatch = {}
    for lag in (-1, 0, 1):
        m = street.shift(lag, freq="h").join(knmi, how="inner").dropna(subset=["street", "knmi"])
        dark = m[m.sun < SUNLIT]
        mismatch[lag] = (dark.street - dark.knmi).abs().mean()
    lag = min(mismatch, key=mismatch.get)
    d = street.shift(lag, freq="h").join(knmi, how="inner").dropna(subset=["street", "knmi"])
    d["diff"] = d.street - d.knmi
    d["local_hour"] = d.index.tz_convert("Europe/Amsterdam").hour
    return d, lag, mismatch


def summarise(d, lag, mismatch):
    dark, lit = d[d.sun < SUNLIT], d[d.sun >= SUNLIT]
    # Daily means as an energy signature would use them; "corrected" swaps
    # sunlit street readings for KNMI plus the street's typical dark offset.
    corrected = d.street.where(d.sun < SUNLIT, d.knmi + dark["diff"].median())
    daily = pd.DataFrame({"street": d.street, "corrected": corrected, "knmi": d.knmi}) \
              .resample("D").mean().dropna()
    worst = d["diff"].idxmax()
    print(f"Hours compared: {len(d)} ({d.index.min():%d %b} – {d.index.max():%d %b})")
    print(f"Time alignment: street shifted {lag:+d} h (mean |diff| in the dark: "
          + ", ".join(f"{k:+d} h → {v:.2f}" for k, v in mismatch.items()) + ")")
    print(f"Correlation, all hours:  r = {d.street.corr(d.knmi):.3f}")
    print(f"Correlation, dark hours: r = {dark.street.corr(dark.knmi):.3f}")
    print(f"Street − KNMI, dark:     median {dark['diff'].median():+.2f} °C")
    print(f"Street − KNMI, sunlit:   median {lit['diff'].median():+.2f} °C, "
          f"max {d['diff'].max():+.1f} °C on {worst.tz_convert('Europe/Amsterdam'):%d %b %H:%M}")
    if d.fog.sum():
        print(f"Street − KNMI, fog hours ({int(d.fog.sum())}): median {d[d.fog == 1]['diff'].median():+.2f} °C")
    for col in ("street", "corrected"):
        gap = daily[col] - daily.knmi
        print(f"Daily means, {col:>9} vs KNMI: r = {daily[col].corr(daily.knmi):.3f}, "
              f"mean gap {gap.mean():+.2f} °C, worst day {gap.abs().max():.1f} °C")
    return daily


def plot(d, daily, path):
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": "#e6e5e0",
                         "grid.linewidth": 0.6, "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb"})
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(16, 5), gridspec_kw={"width_ratios": [1, 1, 1.6]})

    dark = d.sun < SUNLIT
    a1.scatter(d.knmi[dark], d.street[dark], s=8, color=MUTED, alpha=0.35, lw=0, label="Dark hours")
    a1.scatter(d.knmi[~dark], d.street[~dark], s=8, color=SUN, alpha=0.6, lw=0, label="Sunlit hours")
    lo, hi = d[["knmi", "street"]].min().min(), d[["knmi", "street"]].max().max()
    a1.plot([lo, hi], [lo, hi], color=INK, lw=1, ls="--", label="Equal")
    a1.set(title="Every hour: street vs Rotterdam", xlabel="KNMI Rotterdam (°C)", ylabel="Street sensor (°C)")
    a1.legend(frameon=False, loc="upper left")

    by_hour = d.groupby("local_hour")["diff"]
    a2.fill_between(by_hour.median().index, by_hour.quantile(0.25), by_hour.quantile(0.75),
                    color=STREET, alpha=0.18, lw=0, label="Middle 50% of days")
    a2.plot(by_hour.median(), color=STREET, lw=2, label="Median")
    a2.axhline(0, color=INK, lw=1)
    a2.set(title="Street − Rotterdam by time of day", xlabel="Local hour", ylabel="Difference (°C)",
           xticks=range(0, 24, 3))
    a2.legend(frameon=False, loc="upper left")

    a3.plot(daily.knmi, color=KNMI, lw=2, label="KNMI Rotterdam")
    a3.plot(daily.street, color=STREET, lw=2, label="Street sensor")
    a3.plot(daily.corrected, color=STREET, lw=1.5, ls=":", label="Street, sun-corrected")
    a3.set(title="Daily mean temperature", ylabel="°C")
    a3.xaxis.set_major_formatter(mdates.DateFormatter("%-d %b"))
    a3.legend(frameon=False, loc="upper right")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    print(f"Chart saved to {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    utc_date = lambda s: pd.Timestamp(s, tz="UTC")
    parser.add_argument("--start", type=utc_date, default=utc_date("2026-08-01"))
    parser.add_argument("--end", type=utc_date, default=utc_date("2026-10-31"))
    parser.add_argument("--out", default="compare_temps", help="prefix for the .png and .csv")
    args = parser.parse_args()

    knmi = knmi_hourly(args.start, args.end)
    street = street_hourly(args.start, args.end)
    d, lag, mismatch = align(street, knmi)
    daily = summarise(d, lag, mismatch)
    d.to_csv(f"{args.out}.csv")
    plot(d, daily, f"{args.out}.png")


if __name__ == "__main__":
    main()
