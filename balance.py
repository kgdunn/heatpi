#!/usr/bin/env python3
"""Heat balance of the house per day: heat in, the losses we can compute, and what is left over.

    in        = heat pump + appliances + people + sun
    explained = every part in house.py whose other side has a known temperature (outdoors, ground)
                or an estimated one (house.ESTIMATED: the NW neighbour, from spot readings of the wall)
    left over = in - explained: heat to the neighbours, heat stored in the building, and every error

How each term is made, hour by hour:
  heat pump   COP x its electricity. Electricity: L1 more than 400 W above its quiet level for at
              least 10 minutes in a row (the rule of hp_check.py). COP: follows the outdoor
              temperature, hour by hour: the installer guide's COP at the flow temperature the heat
              curve asks for, scaled so that each period between two logged readings of the
              Vaillant's "this month" counters comes out as measured (see cop_model).
  hot water   the electric heater on L1: a plain resistance, so it switches on and off in one step
              of about 2.6 kW, where the heat pump ramps. Its runs are found by those steps and
              taken out: that heat leaves by the drain, so it is no heat in.
  appliances  all other electricity: it ends as heat indoors.
  people      house.PEOPLE_W, day and night.
  sun         KNMI sunshine on a flat surface, turned onto each facade. From knmi_hourly.py (checked
              values, two days late) and, until those arrive, from knmi_temp.py (10-minute values).
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
OUT = ("windows", "walls", "ventilation", "draughts", "ground", "neighbour_nw")
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


# COP of the aroTHERM pure from the installer guide (EN 14511; mean of the 8 and 10 kW units, the likely
# sizes). Only its shape is used: each period between two counter readings scales it to what was measured.
GUIDE_W35 = ((-7, 2.895), (2, 3.385), (7, 4.665))      # outdoor °C -> COP at 35 °C flow (+2 °C includes defrost)
GUIDE_FLOW = ((35, 1.0), (45, 0.759), (55, 0.678))     # flow °C -> COP relative to 35 °C flow, at +7 °C outdoors


def interpolate(points, x, extend=False):
    """Straight lines between the points; beyond the ends flat, or along the first segment if `extend`.
    shortcut: flat above +7 °C outdoors, where the guide gives nothing; upgrade once eBUS reads the COP."""
    (x0, y0), (x1, y1) = points[0], points[1]
    if x <= x0:
        return y0 + (x - x0) * (y1 - y0) / (x1 - x0) if extend else y0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    return points[-1][1]


def flow_temperature(t_out, curve, max_flow, desired=20.0):
    """The Vaillant's target flow (°C) at an outdoor temperature: fitted to its readings of 14 and 22 Nov
    2026 (curves 1.20 and 1.00, within about 1 K; curve 1.85 is an extrapolation), capped at its maximum."""
    return min(max_flow, desired + 4.55 * max(0.0, curve * (desired - t_out)) ** 0.54)


def cop_guide(t_out, flow):
    """The guide's COP at an outdoor and a flow temperature."""
    return interpolate(GUIDE_W35, t_out) * min(1.3, interpolate(GUIDE_FLOW, flow, extend=True))


def settings(notes):
    """[(from, curve, max flow)] from the logged changes ("heat curve 1.20 -> 1.00", "max target flow
    55 -> 45"). The first entry holds what was set before the first change, else house.py's values."""
    changes = []
    for ts, note in notes:
        for key, pattern in (("curve", r"heat curve\s+([\d.]+)\s*->\s*([\d.]+)"),
                             ("max", r"max target flow\s+([\d.]+)\s*->\s*([\d.]+)")):
            if found := re.search(pattern, note, re.I):
                changes.append((datetime.fromisoformat(ts), key, float(found[1]), float(found[2])))
    changes.sort()
    state = {"curve": house.HEAT_CURVE, "max": house.MAX_FLOW_C}
    for key in state:                                         # before a setting's first change: its old value
        state[key] = next((before for _, k, before, _ in changes if k == key), state[key])
    out = [(datetime.min.replace(tzinfo=timezone.utc), state["curve"], state["max"])]
    for when, key, _, after in changes:
        state[key] = after
        out.append((when, state["curve"], state["max"]))
    return out


def counter_periods(notes):
    """[(start, end, COP)] between the Vaillant's "this month" counters logged with note.py
    (`python3 note.py --counters`, which stores "counters: heating=N yield=N").
    COP = (electricity + heat taken from the air) / electricity = 1 + yield / heating. The first
    reading of a month counts from the 1st, where the counters restart."""
    periods, last = [], None
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
            periods.append((start, when, 1 + (air - air0) / (heating - heating0)))
        last = (when, heating, air)
    return periods


def cop_model(db):
    """COP for any hour, following the outdoor temperature: the guide's COP at the flow temperature the
    heat curve then asked for, times a scale per counter period. The scale makes each period's
    heat-weighted COP equal what the counters measured; after the last period, its scale carries on.

    Returns (cop, periods): cop(when, t_out) -> (COP, inside a measured period?), and the periods as
    [(start, end, measured, guide, scale)] for checking. shortcut: a period's heat is taken to follow
    the degree-hours below house.HEATING_BELOW_C, not the logged heat-pump power."""
    notes = rows(db, "SELECT ts_utc, note FROM events ORDER BY ts_utc")
    setting = settings(notes)

    def guide_at(when, t_out):
        _, curve, max_flow = next(s for s in reversed(setting) if s[0] <= when)
        return cop_guide(t_out, flow_temperature(t_out, curve, max_flow))

    periods = []
    for start, end, measured in counter_periods(notes):
        hours = rows(db, f"SELECT {HOURLY}, avg(temp_c) FROM street_consensus WHERE ts_utc >= ? AND ts_utc < ? "
                         "GROUP BY 1", *(t.astimezone(timezone.utc).isoformat() for t in (start, end)))
        heat = [(20 - t, guide_at(datetime.fromtimestamp(h * 3600, timezone.utc), t))
                for h, t in hours if t is not None and t < house.HEATING_BELOW_C]
        guide = sum(q for q, _ in heat) / sum(q / c for q, c in heat) if heat else None   # heat-weighted
        periods.append((start, end, measured, guide, measured / guide if guide else house.COP_SCALE_DEFAULT))

    def cop(when, t_out):
        inside = next((p for p in periods if p[0] <= when < p[1]), None)
        before = [p for p in periods if p[1] <= when]
        nearest = inside or (before[-1] if before else periods[0] if periods else None)
        scale = nearest[4] if nearest else house.COP_SCALE_DEFAULT
        return scale * guide_at(when, t_out), inside is not None
    return cop, periods


def hourly(db_path, since, inside=None):
    """One dict per hour with every term in kWh, for hours where power and all temperatures exist.
    `inside` (°C) stands in for the rooms in hours without room temperatures (before 20 Nov 2026)."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)) as db:
        power = rows(db, "SELECT CAST(strftime('%s', substr(ts_utc, 1, 16)) AS INTEGER) / 60, avg(power_w), "
                         "avg(power_l1_w) FROM p1 WHERE ts_utc >= ? GROUP BY 1", since)
        zones = rows(db, f"SELECT {HOURLY}, zone, avg(temp_c) FROM zones WHERE ts_utc >= ? GROUP BY 1, 2", since)
        outdoor = dict(rows(db, f"SELECT {HOURLY}, avg(temp_c) FROM street_consensus WHERE ts_utc >= ? GROUP BY 1",
                            since))
        # Sunshine: the 10-minute values of the last days (knmi_temp.py; an hour needs 4 of its 6),
        # replaced by KNMI's checked hourly values once those are published, two days later.
        sunshine = dict(rows(db, "SELECT (CAST(strftime('%s', substr(ts_utc, 1, 19)) AS INTEGER) - 600) / 3600, "
                                 "avg(rad_wm2) FROM knmi_live WHERE ts_utc >= ? GROUP BY 1 HAVING count(rad_wm2) >= 4",
                             since))
        sunshine.update(rows(db, f"SELECT {HOURLY}, rad_wm2 FROM knmi_hourly WHERE ts_utc >= ? "
                                 "AND rad_wm2 IS NOT NULL", since))
        cop_at, _ = cop_model(db)
        jumps = rows(db, STEPS, since, *HEATER_W)
    if not power:
        return []

    heater = heater_watts(jumps, max(minute for minute, _, _ in power) * 60 + 60)
    heat_pump = heat_pump_watts({minute: l1 - heater.get(minute, 0)          # L1 without the heater
                                 for minute, _, l1 in power if l1 is not None})
    used = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])       # hour -> minutes; Wh: total, heat pump, hot water, L1
    for minute, total, l1 in power:
        if total is not None:
            u = used[minute // 60]
            u[0] += 1
            u[1] += total / 60
            u[2] += heat_pump.get(minute, 0) / 60
            u[3] += heater.get(minute, 0) / 60
            u[4] += (l1 or 0) / 60

    rooms = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))    # hour -> level -> area x temp, area
    for hour, zone, temp in zones:
        if zone in house.ZONES and temp is not None:
            level, area = house.ZONES[zone]
            for key in (level, "house"):
                rooms[hour][key][0] += area * temp
                rooms[hour][key][1] += area

    out = []
    for hour in sorted(used):
        minutes, total_wh, hp_wh, hot_wh, l1_wh = used[hour]
        temp = {level: weighted / area for level, (weighted, area) in rooms[hour].items()}
        measured_in = len(temp) >= 3
        if not measured_in and inside is not None:
            temp = dict.fromkeys(("house", "living", "basement"), inside)
        other = {"outdoors": outdoor.get(hour), "ground": house.GROUND_C}
        if minutes < 50 or len(temp) < 3 or other["outdoors"] is None:
            continue                                          # an hour needs power, both levels and outdoors
        when = datetime.fromtimestamp(hour * 3600, timezone.utc)
        cop, measured = cop_at(when, other["outdoors"])
        fill = 60 / minutes / 1000                            # Wh over the logged minutes -> kWh for the hour
        row = {"when": when, "cop": cop, "cop_logged": measured,
               "t_in": temp["house"], "t_in_measured": measured_in, "t_out": other["outdoors"],
               "heat_pump": cop * hp_wh * fill, "appliances": (total_wh - hp_wh - hot_wh) * fill,
               "hot_water": hot_wh * fill, "l1": l1_wh * fill,
               "people": house.PEOPLE_W / 1000, "sun": None, **dict.fromkeys(OUT, 0.0)}
        if hour in sunshine:
            noon = when + timedelta(minutes=30)               # the middle of the hour
            row["sun"] = sum(hole * on_facade(sunshine[hour], noon, facing)
                             for facing, hole in house.SUN_HOLES.items()) / 1000
        for name, share in house.ESTIMATED.items():           # a side without a sensor: part of the way to us
            other[name] = other["outdoors"] + share * (temp["living"] - other["outdoors"])
        for part in house.PARTS:
            if part["to"] in other:
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
        for key in ("t_in", "t_out"):
            out[key].append(round(sum(row[key] for row in group) / len(group), 2))
        heat = sum(row["heat_pump"] for row in group)          # the day's COP: its heat over its electricity
        out["cop"].append(round(heat / sum(row["heat_pump"] / row["cop"] for row in group), 2) if heat
                          else round(sum(row["cop"] for row in group) / len(group), 2))
        for key, value in sums.items():
            out[key].append(value)
        out["left_over"].append(round(sum(sums[k] or 0 for k in IN) - sum(sums[k] for k in OUT), 2))
    out["parts"], out["estimated"] = house.PARTS, house.ESTIMATED
    out["hourly"] = {                                         # for the moving window on the page; kWh in the hour
        "time": [row["when"].astimezone(LOCAL).strftime("%Y-%m-%d %H:%M") for row in by_hour],
        "heat_in": [round(sum(row[k] or 0 for k in IN), 3) for row in by_hour],
        "explained": [round(sum(row[k] for k in OUT), 3) for row in by_hour],
        "t_out": [round(row["t_out"], 2) for row in by_hour],
        "sun": [row["sun"] is not None for row in by_hour],   # False: KNMI has not published this hour yet
    }
    return out


def forecast(db_path, days=3):
    """The heat pump for the coming hours, from the newest weather forecast (forecast_logger.py) and the
    house: its heat loss per kelvin (W/K) is scaled so that the last `days` whole days close at 100 %.

        heat pump heat = W/K x (inside - forecast outdoor) - appliances and people - forecast sun
        L1 electricity = heat / COP + L1's other loads (hot water, kitchen), both from the same days

    The COP follows the forecast outdoor temperature (cop_model). Inside temperature, gains and L1's
    other loads are those days' means; no storage in floors and walls, so read the result over
    several hours, not hour by hour."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days + 1)).astimezone(LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    by_day = defaultdict(list)
    for row in hourly(db_path, since.astimezone(timezone.utc).isoformat()):
        by_day[row["when"].astimezone(LOCAL).date()].append(row)
    today = now.astimezone(LOCAL).date()
    use = [group for day, group in sorted(by_day.items())
           if day < today and len(group) >= 23 and all(row["sun"] is not None for row in group)][-days:]
    out = {key: [] for key in ("time", "temp_c", "hp_kw", "l1_kw")}
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)) as db:
        issued = rows(db, "SELECT max(issued_utc) FROM forecast")
        weather = rows(db, "SELECT ts_utc, temp_c, rad_wm2 FROM forecast WHERE issued_utc = ? AND ts_utc >= ? "
                           "ORDER BY ts_utc", issued[0][0] if issued else None,
                       (now - timedelta(hours=1)).isoformat())
        cop_at, periods = cop_model(db)
    if not use:
        out["model"] = {"why": "needs a whole day with power, rooms, outdoor and sun data"}
        return out

    hours = [row for group in use for row in group]
    def mean(key):
        return sum(key(row) for row in hours) / len(hours)
    def per_kelvin(group):                                    # kW of heat in per kelvin inside - outside
        return sum(row[k] or 0 for row in group for k in IN) / sum(row["t_in"] - row["t_out"] for row in group)
    w_per_k = per_kelvin(hours)
    gains = mean(lambda row: row["appliances"] + row["people"])
    l1_other = mean(lambda row: row["l1"] - row["heat_pump"] / row["cop"])
    t_in, cops = mean(lambda row: row["t_in"]), []
    for ts, temp, rad in weather:
        when = datetime.fromisoformat(ts)
        cop = cop_at(when, temp)[0]
        cops.append(cop)
        sun = sum(hole * on_facade(rad or 0, when - timedelta(minutes=30), facing)   # rad: the hour before ts
                  for facing, hole in house.SUN_HOLES.items()) / 1000
        heat = max(0.0, w_per_k * (t_in - temp) - gains - sun)
        out["time"].append(when.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M:%S"))
        out["temp_c"].append(temp)
        out["hp_kw"].append(round(heat / cop, 3))
        out["l1_kw"].append(round(heat / cop + l1_other, 3))
    daily_w_per_k = [1000 * per_kelvin(group) for group in use]
    out["model"] = {"w_per_k": round(1000 * w_per_k), "range": [round(min(daily_w_per_k)), round(max(daily_w_per_k))],
                    "days": len(use), "t_in": round(t_in, 1), "gains_kw": round(gains, 2),
                    "cop": [round(min(cops), 2), round(max(cops), 2)] if cops else None,
                    "cop_scale": round(periods[-1][4] if periods else house.COP_SCALE_DEFAULT, 2),
                    "l1_other_kw": round(l1_other, 2),
                    "issued": datetime.fromisoformat(issued[0][0]).astimezone(LOCAL).strftime("%Y-%m-%d %H:%M")
                    if weather else None}
    return out


def show_cop(db_path):
    """The COP model on the command line: how each counter period compares with the guide, and the COP
    at each outdoor temperature for the heat curve in force now."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)) as db:
        cop_at, periods = cop_model(db)
        _, curve, max_flow = settings(rows(db, "SELECT ts_utc, note FROM events ORDER BY ts_utc"))[-1]
    print("COP = scale x the installer guide's COP at the flow your heat curve asks for.\n")
    print("counter period        measured  guide  scale")
    for start, end, measured, guide, scale in periods:
        print(f"{start.astimezone(LOCAL):%d %b %H:%M} - {end.astimezone(LOCAL):%d %b}   {measured:5.2f}  "
              f"{'  -  ' if guide is None else f'{guide:5.2f}'}  {scale:5.2f}")
    print(f"\nNow: heat curve {curve:.2f}, max flow {max_flow:.0f} °C\noutdoor °C  flow °C   COP")
    now = datetime.now(timezone.utc)
    for t_out in range(-8, 15, 2):
        print(f"{t_out:9d}  {flow_temperature(t_out, curve, max_flow):7.1f}  {cop_at(now, t_out)[0]:5.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--db", default="p1.sqlite")
    parser.add_argument("--cop", action="store_true", help="show the COP model: each counter period, and a table")
    args = parser.parse_args()
    if args.cop:
        return show_cop(args.db)
    b = daily(args.db, 24 * args.days)
    if not b["day"]:
        raise SystemExit("No day has power, room temperatures and outdoor temperature together yet.")
    print("kWh per day. in: heat pump, appliances, people, sun.  out: the parts with a known other side.")
    print("nbNW: to the NW neighbour, an estimate.  hotw: the hot-water heater's electricity; it leaves by the")
    print("drain, so it is in neither.\n")
    print(f"{'day':10} {'h':>2} {'in°C':>5} {'out°C':>5} {'COP':>4} | {'pump':>5} {'appl':>5} {'ppl':>4} {'sun':>4} |"
          f" {'windw':>5} {'walls':>5} {'vent':>5} {'drght':>5} {'grnd':>5} {'nbNW':>5} | {'left':>5} {'expl':>5} |"
          f" {'hotw':>4}")
    for i, day in enumerate(b["day"]):
        heat_in = sum(b[k][i] or 0 for k in IN)
        explained = sum(b[k][i] for k in OUT)
        sun = "   ?" if b["sun"][i] is None else f"{b['sun'][i]:4.1f}"
        print(f"{day} {b['hours'][i]:2d} {b['t_in'][i]:5.1f} {b['t_out'][i]:5.1f} {b['cop'][i]:4.2f} |"
              f" {b['heat_pump'][i]:5.1f} {b['appliances'][i]:5.1f} {b['people'][i]:4.1f} {sun} |"
              + "".join(f" {b[k][i]:5.1f}" for k in OUT)
              + f" | {b['left_over'][i]:5.1f} {100 * explained / heat_in:4.0f}% | {b['hot_water'][i]:4.1f}")
    # One day alone misleads: floors and walls store heat on one day and give it back on the next.
    whole = [i for i, hours in enumerate(b["hours"]) if hours >= 23]
    if len(whole) > 1:
        heat_in = sum(b[k][i] or 0 for k in IN for i in whole)
        explained = sum(b[k][i] for k in OUT for i in whole)
        hours = sum(b["hours"][i] for i in whole)
        print(f"\n{len(whole)} whole days together: {heat_in:.1f} kWh in, {100 * explained / heat_in:.0f}% explained, "
              f"{heat_in - explained:.1f} kWh left over ({1000 * (heat_in - explained) / hours:.0f} W on average)")
    print("\nh = hours with data (24 = a whole day).  ? = KNMI sunshine not published yet (about 2 days late).")


if __name__ == "__main__":
    main()
