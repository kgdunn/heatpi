#!/usr/bin/env python3
"""Log a Grove Loudness Sensor (on the Grove Base Hat) to SQLite, one row per minute.

The sensor outputs an envelope of the sound level (not calibrated dB).
We sample it 10x a second and store the minute's mean, 95th percentile and max, as raw 12-bit
readings (0-4095). The hat can also give 0.1% of supply (0-1000, register 0x30), but that is
4x coarser, and this signal moves only a few counts.

Usage:  python3 loudness_logger.py [--channel 0] [--db p1.sqlite]
Needs:  I2C enabled (sudo raspi-config nonint do_i2c 0) and smbus2.
"""
import argparse
import signal
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone

from smbus2 import SMBus

HAT_ADDRESS = 0x04  # Grove Base Hat ADC; check with `i2cdetect -y -a 1`
RAW_REG = 0x10      # + channel: raw 12-bit reading, 0-4095 (what this logger has always stored)


def read_level(bus, channel):
    """Current sound envelope on analog slot A<channel>, 0-4095."""
    reg = RAW_REG + channel
    bus.write_byte(HAT_ADDRESS, reg)
    return bus.read_word_data(HAT_ADDRESS, reg)


def minute_stats(samples):
    """Mean, 95th percentile and max: the p95 shows sustained noise, the max short bangs."""
    p95 = statistics.quantiles(samples, n=20, method="inclusive")[-1] if len(samples) > 1 else samples[0]
    return round(statistics.fmean(samples), 1), round(p95, 1), max(samples)


def save(db, sql, rows):
    """Write rows; if the database stays busy, keep them for the next minute instead of crashing."""
    try:
        with db:
            db.executemany(sql, rows)
        return []
    except sqlite3.OperationalError as e:  # "database is locked": another process held it > 30 s
        print(f"Not saved yet ({e}): keeping {len(rows)} minute(s) for the next try", file=sys.stderr)
        return rows


def log(channel, db_path, hz=10, period=60):
    db = sqlite3.connect(db_path, timeout=30)  # wait up to 30 s for another writer (default: 5 s)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS loudness "
               "(ts_utc TEXT PRIMARY KEY, mean REAL, p95 REAL, max REAL, n INTEGER)")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # clean stop under systemd
    pending = []                                # minutes not yet written

    with SMBus(1) as bus:
        while True:
            start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            samples, end = [], time.monotonic() + period - time.time() % period
            while time.monotonic() < end:  # collect until the minute is over
                try:
                    samples.append(read_level(bus, channel))
                except OSError as e:  # I2C glitch: skip this sample
                    print(f"I2C read failed: {e}", file=sys.stderr)
                time.sleep(1 / hz)
            if samples:
                pending.append((start.isoformat(timespec="seconds"), *minute_stats(samples), len(samples)))
            if pending:
                pending = save(db, "INSERT OR IGNORE INTO loudness VALUES (?, ?, ?, ?, ?)", pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channel", type=int, default=0, help="analog slot: 0 for A0, 2 for A2, ...")
    parser.add_argument("--db", default="p1.sqlite", help="SQLite file (shared with the P1 logger)")
    args = parser.parse_args()
    try:
        log(args.channel, args.db)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
