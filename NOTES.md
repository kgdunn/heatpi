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
  "Bedroom: 18.5 °C [19.0]". Every hover text carries its unit.
- **Outdoor:** street dots plus a smoothed curve: local linear regression with Gaussian weights,
  σ = 0.75 h, on a 10-minute grid. KNMI Rotterdam is a light blue line (#6da7ec), smoothed the
  same way. On clear nights the airfield reads about 2 K colder than the street sensors.
- **Range selector and Refresh:** the status says "Loading" at once and the plots dim until the
  new data is drawn; the Pi needs a few seconds for several days.
- **Forecast** (28 Nov): `forecast_logger.py` stores KNMI's HARMONIE forecast every hour (48 h
  ahead). The live view draws, dashed and to the right of a line at now: the forecast outdoor
  temperature, and the heat pump's L1 kW, or its kWh since midnight carried to each midnight with
  "≈ total". The model (`balance.forecast`): heat = W/K x (inside − forecast outdoor) − gains −
  forecast sun, with W/K fitted so the last 3 whole days close at 100 % (Kevin's lumped
  parameter); electricity = heat / COP + L1's other loads. No storage term, constant COP: read
  it over hours, and expect the morning after a frosty night to need more than it says.
- **The fitted W/K** (28 Nov): 277 W/K from one whole day, the frostiest yet, at COP 2.85. Long-run
  cross-check: the Vaillant's 2026 counters (3598 kWh electricity + 3411 kWh yield = 7009 kWh of
  heat to 22 Nov) over KNMI Rotterdam's kelvin-hours below 16 °C outdoors (inside 19-19.5 °C:
  52-54 kK·h) give 130-140 W/K from the heat pump alone, 170-180 W/K with 0.45 kW of gains, a bit
  more with sun. `house.py` accounts for about 160 W/K that follows the outdoor temperature. So
  expect the fitted value to settle near 180-220 W/K; a high day mostly means a lower COP in frost
  (not yet modelled) and heat going into storage.
- **History behind the window** (28 Nov): each view loads twice its span before it (2 days for
  24 h, at most a week), so dragging the time axis back shows earlier days. A dragged or zoomed
  view stays through the 5-minute refresh; Refresh or a new range returns to the newest data.
  Plotly quirk: a range on the shared time axis switches off the y autorange of the panel anchored
  to it, so every y axis states `autorange: true` (or false, with its own range).
- **Heat pump L1:** kWh since midnight, back to 0 at 00:00, with each day's total labelled. Tapping
  the title switches to kW. In the 24 h view a pink line shows the same clock time a day earlier.
- **Technical room:** smoothed temperature (σ = 0.5 h) on the left axis, smoothed noise shaded on
  the right axis.
- **Legend:** tapping an entry hides or shows its lines, remembered per device. Room colours were
  checked for colour-blind separation: Badkamer #2a78d6, Bedroom #eb6834, Logeer #1f9e89, Living
  Room #b0389b. Neutral greys: #5b5a56, #a3a29d.
- **Schedules** (`/schedule.html`, 25 Nov): each room's weekly evohome schedule as a week of
  blocks, darker for a higher setpoint, the label inside where it fits, a line at now, and the
  switch times as text. A line per room says the temperature and setpoint now, whether the
  setpoint differs from the schedule, and the next change. The evohome logger reads the schedules
  every 6 hours and stores one only when it changed, so `schedule` keeps their history.
- **Planned, in this order (Kevin, 25 Nov):** (1) show how far and how wildly each room strays from
  its setpoint; (2) change setpoints ahead of the weather forecast (pre-heat before a cold night,
  hold back before sun). For (2): overrides that end by themselves, from a script, logged in
  `events`, never from the dashboard.
- **Top right:** the running commit's time.

## Energy balance (started 22 Nov)

Built: `house.py` (the house), `balance.py` (the calculation), `knmi_hourly.py` (sunshine, wind,
rain) and the page `/balance.html`.

    in        = heat pump + appliances + people + sun
    explained = losses to outdoors and to the ground, part by part from house.py
    left over = in − explained: the neighbours, heat stored in the building, and every error

- The page shows kWh per day as bars, a moving balance (mean power over the last 24 or 6 hours,
  one point per hour), an energy signature (average power per day against inside − outside) and
  the tables behind them.
- First real numbers (21 Nov, a whole day): 50.6 kWh from the heat pump, 5.9 from appliances, 3.0
  from people; 27.5 kWh explained, so 46 %. The left-over of 32 kWh is 1.3 kW on average. A
  neighbour and a home above that are both about 8 K colder would take about 0.9 kW of it.
- Kevin's aim: fit a baseline period, call it 100 %, then see a change to the house (draught
  seals, duct insulation) as new days that fall off the baseline line. Lump as little as possible:
  constants can be fitted, but anything that varies in time must be measured.

**The house** (areas, U-values and their sources are in `house.py`):

- Two levels of 6 × 10 m. Basement, 2.1 m high: the Bedroom zone runs along the whole NW side;
  Logeer, Badkamer and the technical room are on the SE side. Living level, 3.0 m high.
- The front faces NE (about 45°), the back SW (about 225°). From November to January only the back
  gets direct sun. The back also faces the prevailing wind and rain.
- A row house: both side walls are shared, and another home sits on top (own meters, heated only
  at times, to be renovated in 2027). We have no roof of our own.
- Renovated 2024-25: PIR in the front and back walls and in all basement walls, 80 mm PIR under
  the basement floor, vacuum glass in the old frames at the front (both levels, the arched top
  lights too), HR++ glass in new frames at the back, glass wool in the living-room part of the
  ceiling, ventilation with heat recovery.
- The basement floor is about 0.7 m below the street at the front. The two front basement windows
  have old, unsealed frames with visible gaps: the first place to try draught seals. Expect a few
  W/K at most, so a month of baseline before and after, or no visible change at all.
  Untouched: the side walls on the living level (plaster on 10 cm brick) and the kitchen ceiling.
- About 91 W/K goes to outdoors and the ground. About 193 W/K is coupling to the two neighbours
  and the home above: the model needs those three temperatures more than better weather data.
  The NW neighbour's is estimated since 23 Nov (see the infrared readings below).
- The ventilation unit sits in a cold cupboard in the home above, with bare ducts. Insulating the
  supply and extract ducts is the first fix to test: measure the recovery at a supply vent,
  (supply − outdoor) / (room − outdoor), before and after.

**Known errors in this first version:**

- COP is a default of 2.3 until counter readings are logged (`note.py "counters: heating=… yield=…"`).
  The installer guide rates the aroTHERM at COP 3.5-4.0 for 7 °C outdoors and 45 °C flow, and
  4.7-5.3 at 35 °C flow: our early-November value was about 40 % under the rating.
- Hot water: the heater is a plain resistance on L1 and switches in one step of about 2.6 kW
  within one 10 s reading (test of 22 Nov: +2578 W, +2621 W, −2400 W). `balance.py` finds its runs
  by those steps and takes them out of the heat pump and the appliances; that heat leaves by the
  drain. Limits: a run is missed when the heat pump switches in the same 10 s, and another
  2.2-2.9 kW appliance that runs 5 minutes or longer would count as hot water. A HomeWizard Energy
  Socket on the heater would remove both.
- Heat stored in the building is ignored. Fine while the setpoints are constant.
- The sun "holes" in `house.py` are estimates that include shade. Sunshine comes from KNMI's
  10-minute data (`knmi_live`, since 22 Nov) until the checked hourly values arrive two days later.
- A rough check with the early-November counters: about 1 kW more went in than outdoors and the
  ground can take. That is too much for the NW wall alone, so either the Vaillant heat counter
  reads high or there is another sink. Measuring the heat pump's output comes first.

**What the steps on L1 showed (22 Nov):**

- The short spikes to about 6 kW are the heat pump plus the hot-water heater (3.7 + 2.6 kW), not
  the Vaillant's back-up heater.
- L1 dropped by about 1 kW every 57 minutes that morning (06:22, 07:19, 08:16, 09:13 local), at
  about 2 °C outdoors. Probably defrost cycles: check with eBUS.
- The heat pump seems to draw about 3 kW at times (L1 fell from 3.65 to 0.67 kW within a minute).
  The 4 and 6 kW units stop at 2.3 kW, so it is probably the 8 or 10 kW unit: confirm on its plate.
- Kitchen-size loads (1.2-1.8 kW for under 5 minutes) are on L1 too. The heat-pump rule ignores
  them because they are short. One of 1.0-1.25 kW runs about 4 minutes at meal times (07:52,
  12:02): perhaps the combi-oven as a microwave.
- Two showers gave one heater run of 62.5 minutes (10:08:20 to 11:10:50, +2621 W and −2452 W):
  2.6 kWh, so about 1.3 kWh a shower. The heater only started 6 minutes after the first shower
  ended. `balance.py` found 3.0 kWh of hot water for that morning, as the steps predicted.
- In both heater runs the step down was about 170 W smaller than the step up.
- The Quooker tops itself up for 10 s every 38 minutes (1.6 kW on L2): about 7 W on average.
- `steps.py` lists such jumps per phase and pairs them into appliance runs.

**Next measurements, best first:**

1. An eBUS adapter with ebusd: the heat pump's own flow and return temperatures, flow rate and
   counters. Two DS18B20 probes on the flow and return pipes as a cross-check.
2. DS18B20 pairs (wall surface and room air) on the NW and SE side walls of the living level.
   Heat through a wall = 7.7 W/m²K × 30 m² × (air − surface). Tape all probes together for one
   night first, so their offsets cancel.
3. One probe in a supply vent, one in the ventilation cupboard, one for the air in the home above.
   One GPIO pin per cable run, not a star of long cables on one pin.
4. A kWh meter on the heat pump's group, and one on the hot-water heater. A second P1 meter for
   the home above: its gas use is its heat input, a clean before-and-after for the 2027 work.
5. Wind and rain are stored already. Draughts follow the wind across the facades (its NE-SW part):
   add that term once a baseline exists.

**Vaillant controller, read on 22 Nov at 12:29:**

- Environmental yield: 563 kWh this month, 311 last month, 3411 this year, 2075 last year, 5487 in
  total. Electricity for heating: 385 kWh this month, 254 last month, 3598 this year, 1860 last
  year, 5458 in total.
- **COP since the curve change of 14 Nov: 2.85** (132 kWh electricity, 244 kWh yield in 7.9 days,
  at about 5 °C outdoors). Before it was 2.26, in milder weather. Per day: 16.8 kWh of electricity
  for 47.8 kWh of heat, against 18.5 for 41.9 before. Still about 20 % under the guide's rating
  for these conditions (my estimate: 3.5). Whole months: October 2.22, November so far 2.46;
  2026 so far 1.95, 2025 2.12.
- Log both counters at every curve change, then the change itself: each setting then has its own
  measured COP.
- 22 Nov 12:57: curve 1.20 -> 1.00 (logged, with the counters just before). The target flow fell
  from 40 to 37 °C at about 8 °C outdoors. A cold week follows (nights to −2 °C): below about 1 °C
  outdoors both curves reach the 45 °C maximum, so the week tests whether 45 °C is enough in frost.
  Next: read the counters after about a week; go down by 0.2 again if every room held its setpoint.
- **Frost rule** (`frost_rule.py`, 28 Nov): on a night with frost in the forecast (Open-Meteo,
  KNMI's HARMONIE model), a living-room slot below 18 °C is lifted to 18 °C until the slot ends.
  The first writer to evohome, and the baseline an MPC will have to beat. Judge it by the living
  room's degree-hours below setpoint after the morning step, on frosty mornings with and without it.
- 27 Nov, −4 °C at dawn: the living room was at 17.0 °C at 06:30 and reached only 18.5 °C by noon
  against 19 °C. Its setpoint before 07:30 was below the chart (15 °C or lower), so it cooled for
  hours first; at −4 °C both curves ask for more than the 45 °C maximum, so the limit is the
  maximum and the parquet, not curve 1.00. A shallower early-morning setpoint helps more than a curve.
- Installation flow temperature 43 °C at 4.5 °C outdoors (24 h average). The curve of 1.20 asks for
  about 45 °C there, which is also the set maximum. Warm for floor heating: the installer guide's
  table gives about 3 % better COP for every kelvin less.
- The room at the controller read 22.0 °C against 20.0 °C desired, on a sunny day. "Room temp.
  mod." is inactive, so the controller does not use the room temperature at all.
- The heat pump's own flow was 43 °C against a target of 42 °C, so the 10 K buffer offset is not
  applied. The target fits  desired + 2.55 x (curve x (desired - outdoor))^0.78  within a degree.
- Back-up heater: allowed for heating only below −5 °C outdoors (bivalence point), so it plays no
  part in mild weather. Adaptive heat curve: off. Heating stops above 16 °C outdoors.
- Advice given: finish tuning the curve before starting a baseline for house experiments, because
  every curve change moves the heat pump's COP and so the baseline.

**Phases, from an appliance test on 22 Nov** (each run for a minute, steps read per phase): oven
L2 (about 3.7 kW), combi-oven as a microwave L3 (1.8 kW), hob left zone L2 (1.7 kW) and right
zone L3 (1.3 kW), Quooker L2 (1.6 kW, and it tops itself up in bursts of 10-20 s). None is on L1,
so none can pass for the hot-water heater. Not tested yet: dishwasher and washing machine.
Something of 1.0-1.25 kW does run on L1 for about 4 minutes at meal times. Ten-second data was
enough for all of this.

**Electricity groups (main board, list of 22 Nov):** 1 hall, toilet, front room, basement hall;
2 basement sockets, long side; 3 garden, pump, back of the basement, bathroom, shed (nothing in the
shed); 4 washing machine; 5 kitchen, wall spots, dining room; 6 Quooker; 7 oven; 8 combi-oven;
9 dishwasher; 10 hob. A three-phase sub-board in the technical room feeds the heat pump (B16) and
the hot-water heater (C16, fixed wire through an isolator switch: no plug-in meter possible).
There is no car charger, air conditioner or dehumidifier.

**Infrared readings of the side walls, living level (22 Nov 23:45 and 23 Nov, one thermometer):**

- NW wall 15 °C at eight spots, 14-14.5 °C at two near a cold bridge. SE wall 18 °C. Room 18.5-19 °C.
  Outdoors 2 °C in the evening and −1 °C in the morning (street sensors).
- Heat into the NW wall = 7.7 W/m²K x 30 m² x (room − surface) = about 0.85 kW (0.65-1.05). The
  two walls differ by 3 K on the same instrument, which alone gives 0.7 kW.
- For 10 cm of brick (U 2.4) to pass that, the house behind it is at about 7-8 °C: 0.40 of the
  way from outdoors to us, in both readings. So the wall acts like 44 W/K straight to outdoors,
  more than all the windows together (35 W/K). About €250 a heating season at COP 3.
- The SE wall reads 0.5-1 K under the room: at most 0.3 kW, within the thermometer's error.
- `house.ESTIMATED = {"neighbour NW": 0.40}` puts this into the balance as an estimated term
  ("to the NW neighbour"). A probe on the wall should replace it.

**Real numbers, 21 to 23 Nov** (COP 2.85, NW neighbour at 0.40, live sunshine):

- Explained: 67 % on the 21st, 86 % on the 22nd, 60 % in the first 7 hours of the 23rd. All 54 hours
  together: 150 kWh in, 110 kWh explained (73 %), 0.74 kW left over on average.
- The swing between days is heat stored in floors and walls. On the 22nd sun (9.4 kWh) and stored
  heat carried the house while the heat pump was off for 11 hours; in the night after, the heat
  pump refilled the store at 3.6 kW of heat. Read several days together: the page's headline and
  the last line of `balance.py` do that, and the moving balance has a 72 h window.
- From that coast-down the store is very roughly 6 kWh per kelvin of room temperature (my estimate,
  easily ±50 %). Floor temperatures are not measured, so a storage term has to wait for flow and
  return temperatures.
- The 0.7 kW still left over is the right size for the home above (37 W/K), the SE wall (at most
  0.3 kW) and the errors in U-values and COP.

**A free cooling test, 22 Nov:** after a sunny afternoon the living room stood at 22.5 °C at
15:00. With the heat pump off for 11 hours it coasted down to 18 °C by midnight, at 5 to 2 °C
outdoors. Use this decay for the building's thermal mass in the hourly model.

**Open questions for Kevin, most useful first** (remove one when it is answered):

1. Surface temperatures with the same infrared thermometer, on a cold morning: the kitchen ceiling
   and the living-room ceiling (the home above), and the NW wall in the basement (insulated: it
   should read near room temperature). The side walls of the living level are done.
2. The home above: is it heated this week, what does it fall to when empty, and on which days do
   guests come and go? And its boiler and thermostat: brand, model, OpenTherm or plain on/off
   (decides between a tado X over Matter and an OpenTherm Gateway; Kevin to check, 28 Nov).
3. Does the living room hold its day setpoint on the cold mornings of this week (curve 1.00,
   45 °C maximum)? At night it held 18 °C at −1 °C outdoors (23 Nov). evohome has night set-backs.
4. Ventilation: the air temperature in one supply vent on a cold day, with the room and outdoor
   temperature at that moment.
5. Which phase are the dishwasher and the washing machine on? Log the start of a run and look at
   the steps per phase 25 minutes later. (Oven, combi-oven, hob and Quooker are not on L1.)
6. Ventilation unit: its model, its flow setting in m3/h, the length of bare duct in the cold
   cupboard.
7. Heat pump: the size on the outdoor unit's plate (VWL 45, 65, 85 or 105), the buffer's litres,
   and whether a return temperature and a flow rate can be read anywhere (the flow temperature can).
8. Is the technical room inside the insulation?
9. The back windows and doors of the basement: glass type and sizes.
10. What stands against the NW wall on the living level (fitted cupboards, kitchen units, a
    chimney breast)? It covers part of the 30 m².
11. In winter, from when to when does direct sun reach the back windows?
12. Hot-water heater: the litres on its plate.
13. Small: what is the "pump" on group 3, and which appliance on L1 runs at about 1.1 kW for 4
    minutes at meal times?
14. Decisions: the eBUS adapter, the probes, and a cable route from the technical room to the
    living level and the home above.
15. Recurring: both Vaillant counters about once a week and before every curve change (last
    reading: 22 Nov, 385 and 563 kWh).

**KNMI hourly API:** in `start=YYYYMMDDHH&end=YYYYMMDDHH` the hour is a range within each day, not
a moment. Always ask for hours 01 to 24.

## Open items

- Heat balance: add the `knmi_hourly.py` cron line (README), and log the Vaillant "this month"
  counters about once a week.
- The ground under the basement is a constant 11 °C in `house.py`. `knmi_live.soil_c` (soil at 1 m,
  if Rotterdam reports it) could replace it: decide once a winter of it exists.
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
