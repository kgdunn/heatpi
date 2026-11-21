# heatpi: session notes (November 2026)

What we learned, decided and built, so a new session doesn't have to start over. Exact times of
every settings change are in the `events` table (`python3 note.py` lists them).

## The heating system

- Vaillant aroTHERM air-to-water heat pump, with a hydraulic station, a buffer tank and underfloor
  heating in 4 zones: Badkamer, Bedroom and Logeer downstairs, Living Room upstairs. The house is
  old. The basement technical room (heat pump indoor unit, buffer tank, the Pi) has little
  insulation and is damp.
- Hot water comes from a separate, purely electric heater. The heat pump only heats the buffer
  tank for the floor.
- **Two separate controls.** The Vaillant sensoCOMFORT VRC 720f/3 decides when the heat pump runs,
  from its heating curve and its own outdoor sensor. It is wireless (4×AA LR06; wake button on the
  top edge; installer code 00) and talks to the heat pump through Base Station D (0020328845,
  powered by the eBUS). Honeywell evohome (ATC938G display, HCC100 floor-heating controller,
  4 DT4R relays) only opens and closes the zone valves.
  - Proven by a test (13:12-13:28, in `events`): with evohome set to "Heating Off", the heat pump
    kept running.
  - "Heating activated by control" on the Vaillant means the VRC 720f, not evohome.
- **Why the heat pump ran while the rooms were warm enough:** a heating curve of 1.85, giving 44-46 °C
  flow water at 9-10 °C outdoors, plus flat batteries in the VRC 720f.
- **Vaillant settings now:** heating curve 1.20, max flow 45 °C (was 55), mode Manual with 20 °C
  desired (was time-controlled with a 19 °C set-back). Energy integral: start −60 °min, stop at 0.
  Buffer cylinder offset 10 K: not active. Back-up heater: DHW+heat, immersion 0 kW.
  - Expected flow is about 38 °C at 10 °C outdoors.
  - Readings of the curve are logged as events like
    `curve point: out=10.0 target=37.5 curve=1.20 room=20 (street ~8)`.
- **Vaillant counters:** "Heating" = electricity used; "Environmental yield" = heat taken from
  the air. COP = (Heating + Yield) / Heating.
  - November up to Sat 14 Nov 15:45: 253 kWh electricity, 319 kWh yield, so COP 2.26.
  - Year so far: COP 1.9.
- **Heat carried by the water:** Q [W] = flow [l/h] × 1.163 × ΔT [K]. At 860 l/h that is about 1.0 kW
  per kelvin between flow and return.

## Comfort findings

- **Long warm-up after a set-back.** Floor heating gives off heat in proportion to (mean water
  temperature − room temperature). The surplus that warms a room is that output minus the losses,
  and it shrinks disproportionately when the flow water is cooler.
  - Levers: shallower set-backs, evohome's optimum start ("Optimisation"), a Vaillant morning
    boost, and checking the manifold's mixing valve.
  - `leadtime.py` measures it once warm-up mornings accumulate.
- **Technical room** is about 21 °C, with humidity rising from 58% to 68%. The dew point is about
  15 °C, so walls colder than about 18 °C sit above 80% humidity at the surface, a mould risk.
  Kevin wants to watch this over time.
- **Badkamer overshoot on 21 Nov at ~09:30** (19 → 20.5 °C in about 30 minutes) was a shower, not
  the floor: floor heating warms a room at about 0.5 K per hour.

## Analysis tools

- `hp_check.py`: heat-pump runs, starts and kWh per day.
- `recent.py`: the last hours in steps.
- `insights.py`: loudness vs the heat pump, and energy before and after a logged change, using an
  energy-signature baseline from whole days with more than 95% data coverage.
- `deviation.py`: room temperature minus setpoint per zone.
- `leadtime.py`: warm-up time per setpoint step-up.
- `compare_temps.py`: the street sensor vs KNMI.
- `dblocks.py`: who holds the database's write lock.
- `status.sh`: all-in-one health check.

## Operations log, 21 Nov

- **Moved to GitHub.** The Pi folder became a git checkout (pre-git backup:
  `~/p1-logger-before-git.tgz`). Updates now run with `~/p1-logger/update.sh`, which restarts the
  running services whose code changed.
- **"database is locked" crashes.** Before the fix the loggers had restarted p1 14, room 35 and
  loudness 26 times.
  - Cause: the hourly `street_consensus.py` holds the write lock for about 2.6 s because it
    rewrites its whole table, on a slow SD card.
  - Fixed with 30 s timeouts and retrying the next minute.
  - Still open: make `street_consensus.py` write only the recent hours.
- **Loudness scale.** The Pi's own copy of the logger read register 0x10 (raw 0-4095); the repo had
  0x30 (0-1000). It is back to 0x10, because the raw scale is 4× finer. Today's 0-1000 rows were
  converted.
- **Pre-move data.** Room and loudness data from before the move to the technical room went to
  `archive.sqlite` (if that step was run: check).

## Dashboard

- **Rooms:** a downstairs panel and a living room panel. Dotted lines are setpoints; hovering shows
  "Bedroom: 18.5 [19.0]".
- **Outdoor:** street dots plus a smoothed curve: local linear regression with Gaussian weights,
  σ = 0.75 h, on a 10-minute grid. KNMI is a dashed line once logged.
- **Heat pump L1:** kWh since midnight, back to 0 at 00:00, with each day's total labelled. Tapping
  the title switches to kW. In the 24 h view a pink line shows the same clock time a day earlier.
- **Technical room:** smoothed temperature (σ = 0.5 h) on the left axis, smoothed noise shaded on
  the right axis.
- **Legend:** tapping an entry hides or shows its lines, remembered per device. Room colours were
  checked for colour-blind separation: Badkamer #2a78d6, Bedroom #eb6834, Logeer #1f9e89, Living
  Room #b0389b. Neutral greys: #5b5a56, #a3a29d.
- **Top right:** the running commit's time.

## Energy balance: the next task

Aim: a whole-house heat balance per day, later per hour.

    heat in                      = heat lost                 + heat stored
    Q_hp + Q_internal + Q_solar  ≈ H · (T_in − T_out)        + C · dT_in/dt

- **Q_hp** (heat from the heat pump) = COP × E_hp.
  - E_hp: the heat pump's electricity from `power_l1_w`, once the other L1 loads are separated out
    (by their pattern, or by finding out what shares L1).
  - COP: from the Vaillant counters (monthly; with several readings, fit COP against outdoor
    temperature). Better: measure the heat directly (see below).
- **Q_internal**: electricity that ends up as heat inside the house. That's the total minus E_hp,
  minus any export once solar panels arrive, minus the hot water that leaves through the drain.
  Add about 80-100 W per person at home.
- **Q_solar**: KNMI 10-minute global radiation (probably parameter `qg`, W/m²; check the names in
  the collection) times a fitted aperture. This needs the EDR key. `facade_excess` is a free proxy
  meanwhile.
- **T_in**: the zone temperatures weighted by floor area (ask Kevin for the areas). Treat the
  basement / technical room separately. **T_out**: `street_consensus.temp_c`.
- **Fit**: H (heat loss in W/K), the solar aperture and maybe a COP scale, by least squares on daily
  data (an energy signature).
  - Leave out days with settings changes (`events`), days away, and days with under 95% data
    coverage.
  - Check: the COP-implied heat against the Vaillant counters, and H against a rough estimate from
    the house's walls, roof and windows.
- **Better measurements, best first:**
  1. An eBUS adapter with ebusd, to read the Vaillant's own flow and return temperatures, energy
     yields and live COP.
  2. DS18B20 temperature probes on the flow and return pipes, with the known flow of 860 l/h.
  3. The KNMI key, for solar radiation.
  4. Notes for showers and days away (`python3 note.py`).
- **How far back the data goes:** street temperatures since Sep 2025, zones since 20 Nov 2026, room
  and loudness since the move. For P1, check `min(ts_utc)` in `p1`.

## Open items

- KNMI EDR key into `~/.knmi`, then `--find rotterdam`, `--show`, and the cron line (README).
- Noise sensor: turn up its gain knob and test by clapping. Kevin can't reach the room easily at
  the moment.
- evohome high-precision readout (v1 API, 0.01 °C) instead of 0.5 °C steps.
- `street_consensus.py`: write only the recent hours, for a shorter lock.
- Heating curve: step down to 1.0 if the rooms stay comfortable for 2-3 weekday mornings.
- `leadtime.py` once morning warm-ups accumulate; a set-back depth experiment.
- Move the Vaillant outdoor sensor to the north wall; give the Pi (.42) a DHCP reservation.
- Show `events` notes as markers on the dashboard.
- **Solar panels:** Kevin wants data he can log and act on openly. Discussed: a Fronius GEN24
  inverter (local JSON Solar API and Modbus) plus a HomeWizard kWh meter (local API v2). Dutch net
  metering ("saldering") ends on 1 Jan 2027.
- Check that both one-off data fixes on 21 Nov were actually run: the archive of pre-move data, and
  the loudness conversion after the update.
