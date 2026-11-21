# heatpi: notes for Claude

This repo is the code running on Kevin's Raspberry Pi logger ("heatpi"). It logs home energy and
heating data into one SQLite file and serves a dashboard for his phone and iPad. The history of how
it got here, the heating system, findings and open items are in @NOTES.md. Read both before working.

## Where things live

- **Pi:** `heatpi` (192.168.86.42 wired, .43 Wi-Fi), user `kevindunn`. `~/p1-logger` is a git
  checkout of this repo (github.com/kgdunn/heatpi, public).
- **Data:** `~/p1-logger/p1.sqlite`, WAL mode, all times in UTC (`2026-11-21T09:23:00+00:00`).
  Never committed: `.gitignore` covers `*.sqlite`, `*.log`, `data/`, `.venv/`.
- **Secrets** live outside the repo: `~/.p1_token` (HomeWizard P1), `~/.evohome` (TCC e-mail and
  password, chmod 600), `~/.knmi` (KNMI EDR key, not set up yet). Never print, log or commit them.
  The repo is public: keep personal details (address, e-mail, routines) out of code and notes.
- **Dashboard:** http://192.168.86.42:8080 on the home network only. Never expose it to the internet.
- **P1 meter:** HomeWizard P1 at 192.168.86.38 (DHCP reservation), local API v2.

## Working with Kevin

- He reaches the Pi from a phone or tablet over SSH, so he can't `scp` from there and editing is
  awkward. Give short commands he can paste as they are, each in its own code block.
- After pushing a change, always give the update command in its own code block:
  ```bash
  ~/p1-logger/update.sh
  ```
  It pulls, lists the new commits, and restarts only the services whose code changed (`web/`
  needs no restart). `sudo` asks for his password.
- The dashboard shows the running commit's time top right ("Code update / 21 Nov 12:48 UTC"). Tell
  him which stamp to expect, so he can see the update arrived.
- Style: short, clear replies. Python first (also JavaScript; Neo4j where it fits). Explain how you
  got to an answer, not just the answer. He is a data scientist: units, assumptions and
  uncertainty matter.

## Analysis on the Mac

Take a consistent snapshot through SQLite's backup API, not a plain copy of the live file. A plain
copy can miss the last minutes still in the `-wal` file. On the Mac:

```bash
ssh kevindunn@heatpi.lan "python3 -c \"import sqlite3; sqlite3.connect('file:p1-logger/p1.sqlite?mode=ro', uri=True).backup(sqlite3.connect('/tmp/heatpi.sqlite'))\""
scp kevindunn@heatpi.lan:/tmp/heatpi.sqlite data/p1.sqlite
```

On the Pi itself, read only, so the loggers are never blocked:
`sqlite3.connect("file:p1.sqlite?mode=ro", uri=True)`.

## Services and jobs on the Pi

| What | Runs as | Writes |
|---|---|---|
| `p1_logger.py` | service `p1-logger`, every 10 s, flushed once a minute | `p1` |
| `evohome_logger.py` | service `evohome-logger`, every 10 min, `.venv` (Python 3.13, evohome-async 3.2.2) | `zones` |
| `room_logger.py` | service `room-logger`, DHT22 on GPIO 12, 1/min | `room` |
| `loudness_logger.py` | service `loudness-logger`, Grove Base Hat A0 (I2C 0x04), 10 Hz → 1/min | `loudness` |
| `dashboard.py` + `web/` | service `dashboard`, port 8080, read-only | none |
| `outdoor_temp.py && street_consensus.py` | cron `15 * * * *` | `street_temp`, `street_air`, `street_consensus` |
| `knmi_temp.py` | cron every 10 min, once a key exists | `knmi_temp` |
| `note.py "text"` | by hand; `python3 note.py` lists | `events` |

`update.sh` maps logger files to service names: add new services there. `status.sh` is the
all-in-one health check. `dblocks.py` shows who holds the database's write lock.

## Tables in p1.sqlite

| Table | Rows | Columns and notes |
|---|---|---|
| `p1` | every 10 s | `power_w`, `power_l1_w`, `power_l2_w`, `power_l3_w`, `energy_import_kwh`, `energy_export_kwh`, `average_power_15m_w`, `gas_m3`, `gas_ts`, `meter_ts`. Three-phase house; the heat pump is on L1. |
| `zones` | every 10 min, since 20 Nov 2026 ~22:00 local | `zone`, `temp_c`, `setpoint_c`, `zone_mode`, `system_mode`. Zones: Badkamer, Bedroom, Logeer (downstairs), Living Room (upstairs). |
| `room` | 1/min | technical room (basement): `temp_c`, `rh`, `n` reads |
| `loudness` | 1/min | technical room: `mean`, `p95`, `max` in raw 12-bit units (0-4095), `n` samples |
| `street_temp` | hourly | Luchtwachters sensors via the RIVM API: `sensor`, `temp_c` (`street_air`: PM and more) |
| `street_consensus` | hourly | `temp_c` (sun-robust consensus), `n_sensors`, `sun_elev`, `facade_excess` (our sensor de-4190 minus consensus: sun on our facade). Rewritten whole each hour. |
| `knmi_temp` | 10 min | KNMI station `0-20000-0-06344` (Rotterdam; confirm with `--find rotterdam`), empty until a key exists |
| `events` | by hand | `ts_utc`, `note`: settings changes and happenings. The source of truth for "when did X change". |

## Data caveats: read before analysing

- **L1 isn't only the heat pump.** Other appliances share L1 (ironing showed up there). The heat
  pump idles at 60-90 W and runs above 1.2 kW. `recent.py` counts L1 > 500 W as running;
  `hp_check.py` needs 400 W above the quiet level for at least 10 minutes. Short spikes to ~6 kW are
  probably something else on L1. Find out what shares L1 before using it as the heat pump's input.
- **Hot water** comes from a separate, purely electric heater, not the heat pump. The heat pump
  only heats the underfloor-heating buffer tank.
- **Outdoor temperature:** use `street_consensus.temp_c`. The Vaillant's own outdoor sensor reads
  about 1 K warm (sun, window frame). de-1093 and de-4064 sit against warm walls: stored, left out.
- **Zone temperatures** come in 0.5 °C steps (TCC cloud API rounding): use means over an hour or
  longer. The v1 API gives 0.01 °C, not wired in yet.
- **Room and loudness data from before the Pi moved** to the technical room were measured
  elsewhere. They were moved to `archive.sqlite`, if Kevin ran that step: check. `room` data
  between the move and the note "Room sensor lifted off modem vents" is modem exhaust (28-30 °C).
- **Loudness barely responds to sound:** it stays flat around 886-892 raw, probably because the
  sensor's gain knob is too low. Don't read meaning into it yet. On 21 Nov about 10:30-12:50 UTC
  the logger wrote a 0-1000 scale; those rows were converted ×4.095.
- **Small gaps:** before 21 Nov ~11:20 UTC the loggers crashed on "database is locked" (1-2
  minutes lost each time). Fixed: 30 s busy timeout, unsaved rows kept for the next minute.

## Code conventions

- Loggers use only the standard library (system `python3` on Raspberry Pi OS). Analysis scripts may
  use pandas (`python3-pandas` from apt). Keep scripts short and readable, with a docstring and
  `--help`.
- SQLite: connect with `timeout=30`, keep write transactions short (`with db:`), and read through
  `mode=ro` URIs.
- `dashboard.py` uses only the standard library and runs only whitelisted queries (`QUERIES`),
  computed series (`COMPUTED`, e.g. `energy`) and `/api/version`. `web/index.html` is one file with
  Plotly 2.35.2 from jsDelivr.
- **Testing the dashboard:** build a mock `p1.sqlite`, run `dashboard.py --port 8092`, and check
  with Playwright at Pixel 7 (412 px) and 1180×820 that there are no page errors, panel titles
  fit, and tick labels don't overlap. Every series is optional: an empty panel says why in its
  title.
- Commit messages: a short subject, then why.
