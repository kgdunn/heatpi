#!/usr/bin/env python3
"""Log the home above's tado X rooms to SQLite: temperature, humidity, setpoint and heating demand.

Reads tado's cloud (hops.tado.com, the tado X API), one call per run: without Auto-Assist tado
allows 100 calls a day, so cron runs it every 20 minutes (72 a day), plus one call per room a day
for the weekly schedule (`tado_schedule`). Read only. Besides the columns, each row keeps tado's
whole answer for its room (`raw`, JSON), so a field nobody uses yet can still be read later.

Login: tado accepts no password from scripts. `--login` prints a link; open it on your phone, log
in to tado and confirm. ~/.tado (chmod 600) then holds a refresh token and the home's id, never the
password. tado replaces the refresh token at every use, so each run saves the new one; one that
lies unused for 30 days expires, and then `--login` is needed again.

Usage:  python3 tado_logger.py --login             # once
        python3 tado_logger.py --once              # print what tado returns, write nothing
        python3 tado_logger.py [--db p1.sqlite]    # cron: */20 * * * *
"""
import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATE = Path.home() / ".tado"
CLIENT = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"      # tado's public client id for scripts
LOGIN = "https://login.tado.com/oauth2"


def call(url, form=None, token=None):
    """POST `form` (or GET) and return the JSON answer."""
    data = urllib.parse.urlencode(form).encode() if form else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as answer:
        return json.load(answer)


def save(state):
    """Write ~/.tado readable by the owner only, and replace the old file in one step."""
    tmp = STATE.with_name(".tado.tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(state, f)
    tmp.replace(STATE)


def login():
    """The device login: a link to confirm on the phone, then a refresh token and the home's id."""
    start = call(f"{LOGIN}/device_authorize", {"client_id": CLIENT, "scope": "offline_access"})
    print(f"Open this link, log in to tado and confirm (within {start['expires_in'] // 60} minutes):\n"
          f"  {start['verification_uri_complete']}")
    deadline = time.time() + start["expires_in"]
    while time.time() < deadline:
        time.sleep(start.get("interval", 5))
        try:
            token = call(f"{LOGIN}/token", {"client_id": CLIENT, "device_code": start["device_code"],
                                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            break
        except urllib.error.HTTPError as err:
            try:
                error = json.load(err).get("error")
            except ValueError:
                error = f"{err.code} {err.reason}"
            if error not in ("authorization_pending", "slow_down"):
                raise SystemExit(f"tado refused the login: {error}") from None
    else:
        raise SystemExit("Not confirmed in time: run --login again")
    home = call("https://my.tado.com/api/v2/me", token=token["access_token"])["homes"][0]["id"]
    generation = call(f"https://my.tado.com/api/v2/homes/{home}", token=token["access_token"]).get("generation")
    if generation != "LINE_X":
        raise SystemExit(f"This home is not tado X (generation {generation}): this logger reads tado X only")
    save({"refresh_token": token["refresh_token"], "home": home})
    print(f"Logged in. Home {home} is tado X; the token is in {STATE}.")


def access(state):
    """A fresh access token (valid 10 minutes). The refresh token rotates: save the new one at once."""
    token = call(f"{LOGIN}/token", {"client_id": CLIENT, "grant_type": "refresh_token",
                                    "refresh_token": state["refresh_token"]})
    state["refresh_token"] = token["refresh_token"]
    save(state)
    return token["access_token"]


def pick(data, *keys):
    """data[k1][k2]..., or None where a level is missing."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def mode(room):
    """What sets the setpoint: off, holiday, away, boost, manual or schedule."""
    if pick(room, "setting", "power") != "ON":
        return "off"
    found = (name for key, name in (("holidayMode", "holiday"), ("awayMode", "away"), ("boostMode", "boost"),
                                    ("manualControlTermination", "manual")) if room.get(key))
    return next(found, "schedule")


COLUMNS = (("ts_utc", "TEXT"), ("room", "TEXT"), ("temp_c", "REAL"), ("rh", "REAL"), ("setpoint_c", "REAL"),
           ("heating_pct", "REAL"), ("mode", "TEXT"), ("window_open", "INTEGER"), ("next_change_utc", "TEXT"),
           ("next_setpoint_c", "REAL"), ("raw", "TEXT"))
SCHEDULE_EVERY = 23 * 3600                  # the weekly schedule rarely changes: one read a day per room


def utc(stamp):
    """tado's "2026-11-28T21:00:00Z" as "2026-11-28T21:00:00+00:00", like every other table."""
    return datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat() if stamp else None


def room_rows(rooms, ts):
    """One row per room, in COLUMNS order: time, name, °C, humidity %, setpoint °C (None when off),
    heating demand %, mode, open window (tado saw a sudden drop and paused heating), the next
    scheduled change (UTC) and its setpoint, and tado's whole answer for the room.
    With an on/off boiler, the heating demand is the share of time tado asks the boiler for heat."""
    for room in rooms:
        on = mode(room) != "off"
        upcoming = room.get("nextScheduleChange") or {}
        upcoming_on = pick(upcoming, "setting", "power") == "ON"
        yield (ts, (room.get("name") or "").strip(), pick(room, "sensorDataPoints", "insideTemperature", "value"),
               pick(room, "sensorDataPoints", "humidity", "percentage"),
               pick(room, "setting", "temperature", "value") if on else None,
               pick(room, "heatingPower", "percentage"), mode(room), int(bool(room.get("openWindow"))),
               utc(upcoming.get("start")),
               pick(upcoming, "setting", "temperature", "value") if upcoming_on else None,
               json.dumps(room, separators=(",", ":")))


def read_schedules(db, rooms, token, home, ts):
    """Each room's weekly schedule, as tado sends it, once a day. A failed read is stored too (as its
    error), so it is not retried every 20 minutes against the 100 calls a day."""
    last = db.execute("SELECT max(fetched_utc) FROM tado_schedule").fetchone()[0]
    if last and (datetime.fromisoformat(ts) - datetime.fromisoformat(last)).total_seconds() < SCHEDULE_EVERY:
        return
    for room in rooms:
        try:
            raw = json.dumps(call(f"https://hops.tado.com/homes/{home}/rooms/{room['id']}/schedule", token=token),
                             separators=(",", ":"))
        except urllib.error.HTTPError as err:
            raw = json.dumps({"error": f"{err.code} {err.reason}"})
            print(f"{ts} schedule of {(room.get('name') or '').strip()}: tado answered {err.code} {err.reason}")
        with db:
            db.execute("INSERT OR IGNORE INTO tado_schedule VALUES (?, ?, ?)",
                       (ts, (room.get("name") or "").strip(), raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--login", action="store_true", help="log in once, through a link on your phone")
    parser.add_argument("--once", action="store_true", help="print tado's answer, write nothing")
    parser.add_argument("--db", default="p1.sqlite")
    args = parser.parse_args()
    if args.login:
        return login()
    if not STATE.exists():
        raise SystemExit("Run  python3 tado_logger.py --login  first")
    state = json.loads(STATE.read_text())
    try:
        token = access(state)
    except urllib.error.HTTPError as err:
        raise SystemExit(f"tado refused the stored token ({err.code}): run --login again") from None
    try:
        rooms = call(f"https://hops.tado.com/homes/{state['home']}/rooms", token=token)
    except urllib.error.HTTPError as err:      # 429: the day's calls are used up; try again next run
        raise SystemExit(f"tado answered {err.code} {err.reason}") from None
    ts = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
    if args.once:
        print(json.dumps(rooms, indent=1)[:4000])
        for row in room_rows(rooms, ts):
            print(row[:-1])                         # without the raw answer, printed above
        if rooms:                                   # one more call: does the schedule endpoint answer?
            try:
                print(json.dumps(call(f"https://hops.tado.com/homes/{state['home']}/rooms/{rooms[0]['id']}/schedule",
                                      token=token), indent=1)[:3000])
            except urllib.error.HTTPError as err:
                print(f"schedule: tado answered {err.code} {err.reason}")
        return
    db = sqlite3.connect(args.db, timeout=30)
    db.execute(f"CREATE TABLE IF NOT EXISTS tado ({', '.join(f'{n} {t}' for n, t in COLUMNS)}, "
               "PRIMARY KEY (ts_utc, room))")
    db.execute("CREATE TABLE IF NOT EXISTS tado_schedule (fetched_utc TEXT, room TEXT, raw TEXT, "
               "PRIMARY KEY (fetched_utc, room))")
    have = {column[1] for column in db.execute("PRAGMA table_info(tado)")}
    with db:
        if "power" in have:     # a table from the first version (28 Nov): `power` (ON/OFF), room names with a space
            db.execute("ALTER TABLE tado RENAME COLUMN power TO mode")
            db.execute("UPDATE tado SET room = trim(room), mode = CASE mode WHEN 'OFF' THEN 'off' END")
            have.add("mode")
        for name, kind in COLUMNS:                  # columns added since the table was made
            if name not in have:
                db.execute(f"ALTER TABLE tado ADD COLUMN {name} {kind}")
    with db:
        db.executemany(f"INSERT OR IGNORE INTO tado ({', '.join(n for n, _ in COLUMNS)}) "
                       f"VALUES ({', '.join('?' * len(COLUMNS))})", room_rows(rooms, ts))
    read_schedules(db, rooms, token, state["home"], ts)


if __name__ == "__main__":
    main()
