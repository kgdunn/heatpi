#!/usr/bin/env python3
"""Log the Grove temperature & humidity sensor (DHT22/DHT11) once a minute to SQLite.

Uses the Linux kernel's DHT driver, so no extra Python packages are needed.
Enable it once, then reboot (N = the number on the Grove port, e.g. 5 for D5):
    echo "dtoverlay=dht11,gpiopin=N" | sudo tee -a /boot/firmware/config.txt
(The overlay is called dht11 but also reads DHT22/AM2302 sensors.)

Usage:  python3 room_logger.py [--db p1.sqlite]
"""
import argparse
import glob
import signal
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

IIO = "/sys/bus/iio/devices"


def find_sensor():
    """The kernel exposes the sensor as an IIO device whose name starts with 'dht'."""
    for dev in sorted(glob.glob(f"{IIO}/iio:device*")):
        if Path(dev, "name").read_text().strip().startswith("dht"):
            return Path(dev)
    sys.exit("No DHT sensor found: is the dtoverlay line in config.txt, and did you reboot?")


def read_once(dev):
    """One reading as (°C, %RH). Raises OSError on a failed read, which DHTs do often."""
    temp = int((dev / "in_temp_input").read_text()) / 1000
    rh = int((dev / "in_humidityrelative_input").read_text()) / 1000
    return temp, rh


def log(db_path, period=60, tries=5):
    dev = find_sensor()
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS room "
               "(ts_utc TEXT PRIMARY KEY, temp_c REAL, rh REAL, n INTEGER)")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # clean stop under systemd

    while True:
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        readings = []
        for _ in range(tries):
            try:
                readings.append(read_once(dev))
            except OSError:
                pass          # a failed read: just try again
            time.sleep(2.5)   # the sensor needs ~2 s between measurements
        if readings:          # the median also discards the odd bogus reading
            temp = statistics.median(t for t, _ in readings)
            rh = statistics.median(h for _, h in readings)
            with db:
                db.execute("INSERT OR IGNORE INTO room VALUES (?, ?, ?, ?)",
                           (start.isoformat(timespec="seconds"), round(temp, 1), round(rh, 1), len(readings)))
        else:
            print("No successful read this minute", file=sys.stderr)
        time.sleep(period - time.time() % period)  # wait for the next whole minute


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite", help="SQLite file (shared with the other loggers)")
    args = parser.parse_args()
    try:
        log(args.db)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
