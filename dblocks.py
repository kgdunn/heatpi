#!/usr/bin/env python3
"""Who holds p1.sqlite's write lock, and for how long? Watches the kernel's lock table for a while.

In WAL mode SQLite keeps its locks as POSIX locks on bytes of the -shm file: a writer holds byte
120 exclusively for its whole transaction, a checkpoint holds byte 121. Every lock is listed in
/proc/locks with its process id, so sampling that file shows who blocks the others. A logger that
dies with "database is locked" waited longer than its busy timeout for byte 120.

Usage:  python3 dblocks.py [--seconds 120] [--db p1.sqlite]
"""
import argparse
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

SLOTS = {120: "write", 121: "checkpoint", 122: "recovery"}


def held_locks(inode):
    """{(pid, slot)} for every exclusive lock on the -shm file right now."""
    held = set()
    for line in Path("/proc/locks").read_text().splitlines():
        f = line.split()           # "3: POSIX  ADVISORY  WRITE 1234 b3:02:131090 120 120"
        if len(f) < 8 or f[1] != "POSIX" or f[3] != "WRITE":
            continue               # also skips "->" lines: processes waiting, not holding
        pid, dev, start = int(f[4]), f[5], f[6]
        if dev.rsplit(":", 1)[-1] == str(inode) and start.isdigit() and int(start) in SLOTS:
            held.add((pid, SLOTS[int(start)]))
    return held


def command(pid):
    try:
        return " ".join(Path(f"/proc/{pid}/cmdline").read_bytes().decode().replace("\0", " ").split())[:90]
    except OSError:
        return "(exited)"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="p1.sqlite", help="run it from ~/p1-logger")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--every", type=float, default=0.1, help="seconds between looks")
    args = parser.parse_args()

    shm = Path(args.db + "-shm")
    mode = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True).execute("PRAGMA journal_mode").fetchone()[0]
    print(f"journal_mode = {mode}")
    if mode != "wal" or not shm.exists():
        raise SystemExit("Not in WAL mode: readers block writers here, which alone explains 'database is locked'.")

    total, longest, run, names = defaultdict(float), defaultdict(float), defaultdict(float), {}
    end = time.monotonic() + args.seconds
    print(f"Watching {shm.name} for {args.seconds:.0f} s ...")
    while time.monotonic() < end:
        now = held_locks(shm.stat().st_ino)
        for key in now:
            total[key] += args.every
            run[key] += args.every
            longest[key] = max(longest[key], run[key])
            names.setdefault(key[0], command(key[0]))
        for key in set(run) - now:      # lock released: that run is over
            del run[key]
        time.sleep(args.every)

    if not total:
        print("No lock was held at any look: every transaction was shorter than "
              f"{args.every:.1f} s. Nothing blocked the loggers during this watch.")
        return
    print(f"\n{'pid':>6}  {'lock':10} {'held':>7} {'longest':>8}  command")
    for (pid, slot), t in sorted(total.items(), key=lambda kv: -longest[kv[0]]):
        print(f"{pid:>6}  {slot:10} {t:6.1f}s {longest[pid, slot]:7.1f}s  {names[pid]}")
    print("\nA 'longest' of several seconds blocks the loggers: they used to give up after 5 s, now 30 s.")


if __name__ == "__main__":
    main()
