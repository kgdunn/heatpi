#!/usr/bin/env python3
"""Living room and bedroom for the coming 48 hours: the setpoint evohome will ask, and a rough forecast
of the temperature. Standard library only: the dashboard imports it.

Setpoint: the stored weekly schedule (evohome_logger.py). An override in force now holds until the
schedule's next switch. On the frost rule's zone, a slot below its floor is lifted to the floor when
the forecast falls below its frost limit during the slot, as frost_rule.py will do.

Temperature: one room, one state, fitted on the logged hours (`zones`, `street_consensus`):
    valve shut  (setpoint below the room):  dT/dt = -(T - T_out) / tau,          not below the setpoint
    heating     (setpoint above the room):  dT/dt = rate - (T - T_out) / tau,   not past the setpoint
    holding     (at the setpoint):          T stays, unless holding needs more than `rate`
tau (hours) from the cooling hours, rate (K/h) from the warming hours. No sun, no neighbours, no
separate floor mass: a first, rough model, and its error over the last day comes with it.

Usage:  python3 rooms.py [--db p1.sqlite]      (prints the fitted models)
"""
import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from math import sqrt

import house
from balance import HOURLY, LOCAL, rows

ZONES = ("Living Room", "Bedroom")
BAND = 0.25                             # K: closer to the setpoint than this counts as at the setpoint
WEEK = 7 * 24 * 60


def stored_schedule(db, zone):
    """The zone's newest stored schedule as [(minute of the week, setpoint)]; Monday 00:00 is 0."""
    found = rows(db, "SELECT day, start, setpoint_c FROM schedule WHERE zone = ? AND fetched_utc = "
                     "(SELECT max(fetched_utc) FROM schedule WHERE zone = ?) ORDER BY day, start", zone, zone)
    return [(day * 1440 + int(start[:2]) * 60 + int(start[3:5]), setpoint) for day, start, setpoint in found]


def segments(points, start, hours):
    """[(from, to, setpoint)] of the schedule from `start` (local time) for `hours`."""
    minute = start.weekday() * 1440 + start.hour * 60 + start.minute
    earlier = [setpoint for m, setpoint in points if m <= minute]
    current, since, out = earlier[-1] if earlier else points[-1][1], start, []
    for ahead, setpoint in sorted(((m - minute) % WEEK, setpoint) for m, setpoint in points):
        if 0 < ahead <= hours * 60 and setpoint != current:
            switch = start + timedelta(minutes=ahead)
            out.append((since, switch, current))
            since, current = switch, setpoint
    out.append((since, start + timedelta(hours=hours), current))
    return out


def fit(history):
    """tau (h) and rate (K/h) from [(T, setpoint lowest, setpoint highest, T_out, T an hour later)]."""
    cool = [(t - out, later - t) for t, lo, hi, out, later in history if lo == hi and hi < t - BAND]
    warm = [(t, out, later) for t, lo, hi, out, later in history if lo == hi and lo > t + BAND]
    if len(cool) < 6 or len(warm) < 3:
        return None, f"too few hours to fit: {len(cool)} cooling (need 6), {len(warm)} warming (need 3)"
    per_hour = -sum(loss * drop for loss, drop in cool) / sum(loss * loss for loss, _ in cool)
    tau = min(500.0, max(5.0, 1 / per_hour if per_hour > 0 else 500.0))
    rate = sum(later - t + (t - out) / tau for t, out, later in warm) / len(warm)
    return {"tau_h": round(tau, 1), "rate_k_per_h": round(min(3.0, max(0.05, rate)), 2),
            "hours": [len(cool), len(warm)]}, None


def step(temp, setpoint, t_out, model):
    """The room an hour later."""
    loss = (temp - t_out) / model["tau_h"]
    if temp > setpoint + BAND:
        return max(setpoint, temp - loss)
    if temp < setpoint - BAND or model["rate_k_per_h"] < loss:
        return min(setpoint, temp + model["rate_k_per_h"] - loss)
    return setpoint


def forecast(db_path, hours=48, days=7):
    """{zone: {"setpoint": {time, value}, "temp": {time, value}, "model": {...}}} for ZONES."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)) as db:
        logged = rows(db, f"SELECT {HOURLY}, zone, avg(temp_c), min(setpoint_c), max(setpoint_c) FROM zones "
                          "WHERE ts_utc >= ? GROUP BY 1, 2", since)
        outdoor = dict(rows(db, f"SELECT {HOURLY}, avg(temp_c) FROM street_consensus WHERE ts_utc >= ? GROUP BY 1",
                            since))
        issued = rows(db, "SELECT max(issued_utc) FROM forecast")
        weather = rows(db, "SELECT ts_utc, temp_c FROM forecast WHERE issued_utc = ? ORDER BY ts_utc",
                       issued[0][0] if issued else None)
        latest = {zone: rows(db, "SELECT ts_utc, temp_c, setpoint_c, zone_mode FROM zones WHERE zone = ? "
                                 "ORDER BY ts_utc DESC LIMIT 1", zone) for zone in ZONES}
        schedules = {zone: stored_schedule(db, zone) for zone in ZONES}
    weather = [(datetime.fromisoformat(ts), temp) for ts, temp in weather]

    def outdoor_at(when):                              # the forecast hour nearest to `when`
        return min(weather, key=lambda w: abs(w[0] - when))[1] if weather else None

    out = {}
    for zone in ZONES:
        result = {"setpoint": {"time": [], "value": []}, "temp": {"time": [], "value": []}, "model": {}}
        out[zone] = result
        by_hour = {hour: (t, lo, hi) for hour, z, t, lo, hi in logged if z == zone and t is not None}
        if not schedules[zone] or not latest[zone]:
            result["model"]["why"] = "no stored schedule or no room readings yet"
            continue

        # The setpoints: the schedule, an override until the next switch, the frost rule's lift.
        ts, temp_now, setpoint_now, mode = latest[zone][0]
        start = now.astimezone(LOCAL).replace(second=0, microsecond=0)
        plan = segments(schedules[zone], start, hours)
        if mode != "follow_schedule" and setpoint_now is not None:
            plan[0] = (plan[0][0], plan[0][1], setpoint_now)
        rule = house.FROST_RULE
        if zone == rule["zone"] and weather:
            plan = [(a, b, rule["floor"] if setpoint < rule["floor"] and min(
                        (t for w, t in weather if a - timedelta(hours=1) <= w <= b), default=99) < rule["frost"]
                     else setpoint) for a, b, setpoint in plan]
        for a, b, setpoint in plan:
            result["setpoint"]["time"] += [f"{a:%Y-%m-%d %H:%M:%S}", f"{b:%Y-%m-%d %H:%M:%S}"]
            result["setpoint"]["value"] += [setpoint, setpoint]

        # The temperature: fit on the logged hours, check on the last day, then run on the forecast.
        history = [(t, lo, hi, outdoor[hour], by_hour[hour + 1][0]) for hour, (t, lo, hi) in sorted(by_hour.items())
                   if hour + 1 in by_hour and hour in outdoor]
        model, why = fit(history)
        if model is None or not weather:
            result["model"]["why"] = why or "no weather forecast yet"
            continue
        last_day = sorted(h for h in by_hour if h in outdoor)[-24:]
        if len(last_day) >= 12:
            temp, errors = by_hour[last_day[0]][0], []
            for hour in last_day[1:]:
                if hour - 1 not in by_hour or hour - 1 not in outdoor:   # a gap: start again from the reading
                    temp = by_hour[hour][0]
                    continue
                temp = step(temp, by_hour[hour - 1][2], outdoor[hour - 1], model)
                errors.append(temp - by_hour[hour][0])
            model["rms_k"] = round(sqrt(sum(e * e for e in errors) / len(errors)), 2) if errors else None
        temp, when = temp_now, datetime.fromisoformat(ts).astimezone(LOCAL)
        result["temp"]["time"].append(f"{when:%Y-%m-%d %H:%M:%S}")
        result["temp"]["value"].append(temp)
        while when < start + timedelta(hours=hours):
            setpoint = next((s for a, b, s in plan if a <= when + timedelta(minutes=30) < b), plan[-1][2])
            temp = step(temp, setpoint, outdoor_at(when + timedelta(minutes=30)), model)
            when += timedelta(hours=1)
            result["temp"]["time"].append(f"{when:%Y-%m-%d %H:%M:%S}")
            result["temp"]["value"].append(round(temp, 2))
        result["model"].update(model)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()
    for zone, result in forecast(args.db).items():
        model = result["model"]
        if "tau_h" not in model:
            print(f"{zone}: no temperature model: {model.get('why')}")
            continue
        print(f"{zone}: cools toward outdoors with a time constant of {model['tau_h']} h; heats at up to "
              f"{model['rate_k_per_h']} K/h (from {model['hours'][0]} cooling and {model['hours'][1]} warming hours); "
              f"error over the last day {model.get('rms_k', '?')} K")


if __name__ == "__main__":
    main()
