#!/usr/bin/env python3
"""Log the home above's tado X rooms to SQLite: temperature, humidity, setpoint and heating demand.

Reads tado's cloud (hops.tado.com, the tado X API), one call per run: without Auto-Assist tado
allows 100 calls a day, so cron runs it every 20 minutes (72 a day). Read only.

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


def room_rows(rooms, ts):
    """One row per room: time, name, °C, humidity %, setpoint °C (None when off), heating demand %, mode.
    With an on/off boiler, the heating demand is the share of time tado asks the boiler for heat."""
    for room in rooms:
        on = mode(room) != "off"
        yield (ts, (room.get("name") or "").strip(), pick(room, "sensorDataPoints", "insideTemperature", "value"),
               pick(room, "sensorDataPoints", "humidity", "percentage"),
               pick(room, "setting", "temperature", "value") if on else None,
               pick(room, "heatingPower", "percentage"), mode(room))


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
            print(row)
        return
    db = sqlite3.connect(args.db, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS tado (ts_utc TEXT, room TEXT, temp_c REAL, rh REAL, setpoint_c REAL, "
               "heating_pct REAL, mode TEXT, PRIMARY KEY (ts_utc, room))")
    with db:
        db.executemany("INSERT OR IGNORE INTO tado VALUES (?, ?, ?, ?, ?, ?, ?)", room_rows(rooms, ts))


if __name__ == "__main__":
    main()
