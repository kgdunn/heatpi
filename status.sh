#!/usr/bin/env bash
# Quick check: loggers running, data fresh, recent changes, heat pump days, last hours.
# Usage:  ./status.sh   (from anywhere)
cd "$(dirname "$0")"

echo "== Loggers"
for s in p1-logger loudness-logger room-logger evohome-logger; do
    printf '  %-16s %s\n' "$s" "$(systemctl is-active "$s")"
done

echo; echo "== Newest data"
python3 - <<'PY'
import sqlite3
from datetime import datetime, timezone

# table: how old (minutes) the newest row may be before it counts as stale
LIMITS = {"p1": 5, "loudness": 5, "room": 5, "street_temp": 240, "street_consensus": 240, "zones": 25}
db, now = sqlite3.connect("p1.sqlite"), datetime.now(timezone.utc)
for table, limit in LIMITS.items():
    try:
        n, last = db.execute(f"SELECT count(*), max(ts_utc) FROM {table}").fetchone()
        age = (now - datetime.fromisoformat(last)).total_seconds() / 60
        print(f"  {table:17} {n:>7} rows, newest {age:4.0f} min ago  {'ok' if age <= limit else 'STALE'}")
    except (sqlite3.OperationalError, TypeError):  # no table yet, or empty
        print(f"  {table:17} no data")
PY

echo; echo "== Last changes";        python3 note.py | tail -5
echo; echo "== Heat pump, 3 days";   python3 hp_check.py --days 3
echo; echo "== Last 6 hours";        python3 recent.py --hours 6 --step 30
