#!/usr/bin/env python3
"""Heat balance of the house per day: heat in, the losses we can compute, and what is left over.

    in        = heat pump + appliances + people + sun
    explained = every part in house.py whose other side has a known temperature (outdoors, ground)
    left over = in - explained: heat to the neighbours, heat stored in the building, and every error

How each term is made, hour by hour:
  heat pump   COP x its electricity. Electricity: L1 more than 400 W above its quiet level for at
              least 10 minutes in a row (the rule of hp_check.py). COP: from the Vaillant's
              "this month" counters when you log them, else house.COP_DEFAULT:
                  python3 note.py "counters: heating=253 yield=319"
  hot water   the electric heater on L1: a plain resistance, so it switches on and off in one step
              of about 2.6 kW, where the heat pump ramps. Its runs are found by those steps and
              taken out: that heat leaves by the drain, so it is no heat in.
  appliances  all other electricity: it ends as heat indoors.
  people      house.PEOPLE_W, day and night.
  sun         KNMI sunshine on a flat surface, turned onto each facade (needs knmi_hourly.py).
  explained   W/K x (inside - other side) for each part.

Only the standard library, so dashboard.py can import it.

Usage:  python3 balance.py [--days 14] [--db p1.sqlite]
"""
import argparse
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from math import asin, atan2, cos, pi, radians, sin, tan
from zoneinfo import ZoneInfo

import house

LOCAL = ZoneInfo("Europe/Amsterdam")
EXTRA_W, MIN_RUN = 400, 10            # heat pump = this many watts above quiet, this many minutes
HEATER_W = (2200, 2900)               # hot-water heater: one step of this size on L1 (seen: 2.4-2.6 kW)
HEATER_MIN, HEATER_MAX = 5 * 60, 4 * 3600   # seconds a heater run lasts: longer than a kettle, shorter than a lost step
IN = ("heat_pump", "appliances", "people", "sun")
OUT = ("windows", "walls", "ventilation", "draughts", "ground")
HOURLY = "CAST(strftime('%s', substr(ts_utc, 1, 13) || ':00') AS INTEGER) / 3600"   # hours since 1970
# Jumps on L1 between two readings at most 30 s apart: (second, watts up or down).
STEPS = """SELECT t, step FROM (
             SELECT t, w - lag(w) OVER (ORDER BY t) AS step, t - lag(t) OVER (ORDER BY t) AS gap
             FROM (SELECT CAST(strftime('%s', substr(ts_utc, 1, 19)) AS INTEGER) AS t, power_l1_w AS w
                   FROM p1 WHERE ts_utc >= ?))
           WHERE abs(step) BETWEEN ? AND ? AND gap <= 30 ORDER BY t"""


def rows(db, sql, *args):
    """Query result, or nothing when the table does not exist yet."""
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise


def heat_pump_watts(l1):
    """{minute: watts}: L1 above its quiet level, inside runs of at least MIN_RUN minutes with more
    than EXTRA_W extra. `l1` maps minute number -> mean watts on L1."""
    quiet = sorted(l1.values())[len(l1) // 20]                # 5th percentile
    out, run = {}, []
    for minute in [*sorted(l1), None]:                        # None: closes the last run
        on = minute is not None and l1[minute] - quiet > EXTRA_W
        if run and not (on and minute == run[-1] + 1):        # the run ends here
            if len(run) >= MIN_RUN:
                out.update((m, l1[m] - quiet) for m in run)
            run = []
        if on:
            run.append(minute)
    return out


def heater_watts(steps, end):
    """{minute: watts}: the hot-water heater's mean power in each minute. `steps` are the jumps of
    HEATER_W on L1, as (second, watts). A step up opens a run and the next step down closes it; a
    kettle may switch on and off inside a heater run, so the newest open run closes first. A run
    still open at `end` (the newest reading) counts up to there. Runs shorter than HEATER_MIN are
    not the heater; runs longer than HEATER_MAX mean a step was missed."""
    out, open_runs = defaultdict(float), []
    for second, step in [*steps, (end, 0)]:                   # the last entry closes a run still going
        if step > 0:
            open_runs.append((second, step))
        elif open_runs:
            start, up = open_runs.pop()
            if HEATER_MIN <= second - start <= HEATER_MAX:
                watts = (up - step) / 2 if step else up       # mean of the step up and the step down
                for minute in range(start // 60, second // 60 + 1):
                    seconds_on = min(second, minute * 60 + 60) - max(start, minute * 60)
                    out[minute] += watts * seconds_on / 60
    return out


def sun_position(when):
    """(height above the horizon, direction from north) of the sun in degrees, good to about 0.5°."""
    days = (when - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
    anomaly = radians((357.529 + 0.98560028 * days) % 360)
    longitude = radians((280.459 + 0.98564736 * days) % 360 + 1.915 * sin(anomaly) + 0.020 * sin(2 * anomaly))
    tilt = radians(23.439 - 0.00000036 * days)
    right_ascension = atan2(cos(tilt) * sin(longitude), cos(longitude))
    declination = asin(sin(tilt) * sin(longitude))
    hour_angle = radians((18.697374558 + 24.06570982441908 * days) % 24 * 15 + house.LON) - right_ascension
    lat = radians(house.LAT)
    height = asin(sin(lat) * sin(declination) + cos(lat) * cos(declination) * cos(hour_angle))
    direction = atan2(-sin(hour_angle), tan(declination) * cos(lat) - sin(lat) * cos(hour_angle))
    return height * 180 / pi, direction * 180 / pi % 360


def on_facade(flat, when, facing):
    """W/m2 on an upright wall facing `facing` (degrees from north), from `flat`: the measured
    sunshine on a flat surface. Three parts reach the wall:
      beam     straight from the sun: flat-surface beam, re-aimed at the wall
      diffuse  from the sky: a wall sees half the sky
      bounce   off the ground, which reflects about 20 %
    How much of `flat` is beam follows from how clear the sky is (Erbs' rule of thumb)."""
    height, direction = sun_position(when)
    if not flat or height <= 0:
        return 0.0
    up = sin(radians(height))
    above_air = 1361 * (1 + 0.033 * cos(2 * pi * when.timetuple().tm_yday / 365)) * up
    clear = min(flat / above_air, 1.0)                        # 0 = overcast, about 0.75 = cloudless
    if height < 3:                                            # sun on the horizon: all of it diffuse
        share = 1.0
    elif clear <= 0.22:
        share = 1 - 0.09 * clear
    elif clear <= 0.80:
        share = 0.9511 - 0.1604 * clear + 4.388 * clear**2 - 16.638 * clear**3 + 12.336 * clear**4
    else:
        share = 0.165
    diffuse = share * flat
    aim = cos(radians(height)) * cos(radians(direction - facing))   # 1 = sun square onto the wall
    return (flat - diffuse) / up * max(aim, 0) + 0.5 * diffuse + 0.5 * 0.2 * flat


def cop_steps(notes):
    """[(from_time, COP), ...] from the Vaillant's "this month" counters logged with note.py.
    COP = (electricity + heat taken from the air) / electricity = 1 + yield / heating, between two
    readings. The first reading of a month counts from the 1st, where the counters restart."""
    steps, last = [], None
    for ts, note in notes:
        found = re.search(r"counters:.*?heating=([\d.]+).*?yield=([\d.]+)", note, re.I)
        if not found:
            continue
        when, heating, air = datetime.fromisoformat(ts), float(found[1]), float(found[2])
        month = when.astimezone(LOCAL).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if last and last[0] >= month and last[1] <= heating and last[2] <= air:
            start, heating0, air0 = last
        else:
            start, heating0, air0 = month, 0.0, 0.0
        if heating - heating0 >= 10:                          # under 10 kWh the 1 kWh steps are too coarse
            steps.append((start, 1 + (air - air0) / (heating - heating0)))
        last = (when, heating, air)
    return steps


def hourly(db_path, since):
    """One dict per hour with every term in kWh, for hours where power and all temperatures exist."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)) as db:
        power = rows(db, "SELECT CAST(strftime('%s', substr(ts_utc, 1, 16)) AS INTEGER) / 60, avg(power_w), "
                         "avg(power_l1_w) FROM p1 WHERE ts_utc >= ? GROUP BY 1", since)
        zones = rows(db, f"SELECT {HOURLY}, zone, avg(temp_c) FROM zones WHERE ts_utc >= ? GROUP BY 1, 2", since)
        outdoor = dict(rows(db, f"SELECT {HOURLY}, avg(temp_c) FROM street_consensus WHERE ts_utc >= ? GROUP BY 1",
                            since))
        sunshine = dict(rows(db, f"SELECT {HOURLY}, rad_wm2 FROM knmi_hourly WHERE ts_utc >= ?", since))
        steps = cop_steps(rows(db, "SELECT ts_utc, note FROM events WHERE note LIKE '%counters:%' ORDER BY ts_utc"))
        jumps = rows(db, STEPS, since, *HEATER_W)
    if not power:
        return []

    heater = heater_watts(jumps, max(minute for minute, _, _ in power) * 60 + 60)
    heat_pump = heat_pump_watts({minute: l1 - heater.get(minute, 0)          # L1 without the heater
                                 for minute, _, l1 in power if l1 is not None})
    used = defaultdict(lambda: [0, 0.0, 0.0, 0.0])            # hour -> minutes; Wh: total, heat pump, hot water
    for minute, total, _ in power:
        if total is not None:
            u = used[minute // 60]
            u[0] += 1
            u[1] += total / 60
            u[2] += heat_pump.get(minute, 0) / 60
            u[3] += heater.get(minute, 0) / 60

    inside = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))   # hour -> level -> area x temp, area
    for hour, zone, temp in zones:
        if zone in house.ZONES and temp is not None:
            level, area = house.ZONES[zone]
            for key in (level, "house"):
                inside[hour][key][0] += area * temp
                inside[hour][key][1] += area

    out = []
    for hour in sorted(used):
        minutes, total_wh, hp_wh, hot_wh = used[hour]
        temp = {level: weighted / area for level, (weighted, area) in inside[hour].items()}
        other = {"outdoors": outdoor.get(hour), "ground": house.GROUND_C}
        if minutes < 50 or len(temp) < 3 or other["outdoors"] is None:
            continue                                          # an hour needs power, both levels and outdoors
        when = datetime.fromtimestamp(hour * 3600, timezone.utc)
        logged = next((c for start, c in reversed(steps) if start <= when), None)   # newest reading before
        cop = logged or house.COP_DEFAULT
        fill = 60 / minutes / 1000                            # Wh over the logged minutes -> kWh for the hour
        row = {"when": when, "cop": cop, "cop_logged": logged is not None,
               "t_in": temp["house"], "t_out": other["outdoors"],
               "heat_pump": cop * hp_wh * fill, "appliances": (total_wh - hp_wh - hot_wh) * fill,
               "hot_water": hot_wh * fill,
               "people": house.PEOPLE_W / 1000, "sun": None, **dict.fromkeys(OUT, 0.0)}
        if hour in sunshine:
            noon = when + timedelta(minutes=30)               # the middle of the hour
            row["sun"] = sum(hole * on_facade(sunshine[hour], noon, facing)
                             for facing, hole in house.SUN_HOLES.items()) / 1000
        for part in house.PARTS:
            if part["to"] in house.MEASURED:
                row[part["group"]] += part["wk"] * (temp[part["inside"]] - other[part["to"]]) / 1000
        out.append(row)
    return out


def daily(db_path, hours):
    """Per local day, as columns: kWh for each term, the left-over, temperatures and coverage.
    `sun` is None on days KNMI has not published yet; such a day's left-over still holds the sun.
    `hourly` holds heat in and explained losses per hour, for a moving window."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since = since.astimezone(LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)   # whole days
    days, by_hour = defaultdict(list), hourly(db_path, since.astimezone(timezone.utc).isoformat())
    for row in by_hour:
        days[row["when"].astimezone(LOCAL).strftime("%Y-%m-%d")].append(row)

    out = {key: [] for key in ("day", "hours", "t_in", "t_out", "cop", "cop_logged",
                               *IN, *OUT, "hot_water", "left_over")}
    for day, group in sorted(days.items()):
        def total(key):
            values = [row[key] for row in group]
            return None if None in values else round(sum(values), 2)
        sums = {key: total(key) for key in (*IN, *OUT, "hot_water")}
        out["day"].append(day)
        out["hours"].append(len(group))
        out["cop_logged"].append(all(row["cop_logged"] for row in group))
        for key in ("t_in", "t_out", "cop"):
            out[key].append(round(sum(row[key] for row in group) / len(group), 2))
        for key, value in sums.items():
            out[key].append(value)
        out["left_over"].append(round(sum(sums[k] or 0 for k in IN) - sum(sums[k] for k in OUT), 2))
    out["parts"] = house.PARTS
    out["hourly"] = {                                         # for the moving window on the page; kWh in the hour
        "time": [row["when"].astimezone(LOCAL).strftime("%Y-%m-%d %H:%M") for row in by_hour],
        "heat_in": [round(sum(row[k] or 0 for k in IN), 3) for row in by_hour],
        "explained": [round(sum(row[k] for k in OUT), 3) for row in by_hour],
        "t_out": [round(row["t_out"], 2) for row in by_hour],
        "sun": [row["sun"] is not None for row in by_hour],   # False: KNMI has not published this hour yet
    }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()
    b = daily(args.db, 24 * args.days)
    if not b["day"]:
        raise SystemExit("No day has power, room temperatures and outdoor temperature together yet.")
    print("kWh per day. in: heat pump, appliances, people, sun.  out: the parts with a known other side.")
    print("hotw: electricity of the hot-water heater; it leaves by the drain, so it is in neither.\n")
    print(f"{'day':10} {'h':>2} {'in°C':>5} {'out°C':>5} {'COP':>4} | {'pump':>5} {'appl':>5} {'ppl':>4} {'sun':>4} |"
          f" {'windw':>5} {'walls':>5} {'vent':>5} {'drght':>5} {'grnd':>5} | {'left':>5} {'expl':>5} | {'hotw':>4}")
    for i, day in enumerate(b["day"]):
        heat_in = sum(b[k][i] or 0 for k in IN)
        explained = sum(b[k][i] for k in OUT)
        sun = "   ?" if b["sun"][i] is None else f"{b['sun'][i]:4.1f}"
        print(f"{day} {b['hours'][i]:2d} {b['t_in'][i]:5.1f} {b['t_out'][i]:5.1f} {b['cop'][i]:4.2f} |"
              f" {b['heat_pump'][i]:5.1f} {b['appliances'][i]:5.1f} {b['people'][i]:4.1f} {sun} |"
              + "".join(f" {b[k][i]:5.1f}" for k in OUT)
              + f" | {b['left_over'][i]:5.1f} {100 * explained / heat_in:4.0f}% | {b['hot_water'][i]:4.1f}")
    print("\nh = hours with data (24 = a whole day).  ? = KNMI sunshine not published yet (about 2 days late).")


if __name__ == "__main__":
    main()
