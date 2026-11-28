# heatpi: session notes (November 2026)

What we learned, decided and built, so a new session doesn't have to start over. Exact times of
every settings change are in the `events` table (`python3 note.py` lists them).

## The heating system

- Vaillant aroTHERM pure VWL 105/7.2 AS 230V S3 (10 kW, made 06/2023, R32 1.60 kg, one phase, 22 A
  maximum; plate read 28 Nov). Plate: A7/W35 9.85 kW at COP 4.68, A7/W55 7.40 kW at COP 3.19.
  Guide: A−7/W35 7.12 kW at COP 2.86, A2/W35 7.67 kW at 3.38. With a hydraulic station, a buffer tank and underfloor
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
- **Technical room, photos of 28 Nov:** reached through a low arched hatch (two thin doors with open
  gaps, no seal) from a room with an oak floor. Inside: bare old masonry with damp marks and flaking
  plaster, a boarded ceiling (plywood and insulation boards), loose boards on a rough floor. It holds
  the buffer tank, the hot-water vat, the hydraulic station, the floor-heating manifold and pump.
  Partly insulated, less than the rest of the house. The tanks' and pipes' standing losses keep it
  near 21 °C; through the hatch's gaps part of that reaches the room in front, the rest leaves
  through the bare walls. Insulating damp masonry from inside needs a capillary-active board
  (calcium silicate, lime-based), not foil-faced PIR: PIR would put the dew point in the wall.
- **Badkamer overshoot on 21 Nov at ~09:30** (19 → 20.5 °C in about 30 minutes) was a shower, not
  the floor: floor heating warms a room at about 0.5 K per hour.

## Analysis tools

- `hp_check.py`: heat-pump runs, starts and kWh per day.
- `recent.py`: the last hours in steps.
- `insights.py`: loudness vs the heat pump, and energy before and after a logged change, using an
  energy-signature baseline from whole days with more than 95% data coverage.
- `deviation.py`: room temperature minus setpoint per zone.
- `leadtime.py`: warm-up time per setpoint step-up.
- `signature.py`: the house's W/K and the sun's weight from every whole day (robust fit).
- `rooms.py`: the living room and bedroom 48 h ahead (setpoints and temperature).
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
- **BnB panel** (28 Nov): the home above's tado temperature and setpoint, bottom left on its own
  scale; the technical room moved to the bottom right. The header no longer shows the time range
  ("27 Nov at 11:00 to now ..."): only "Loading" or an error, to save a line on the phone.
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
- **COP model** (28 Nov): COP = scale x the installer guide's COP at the outdoor temperature and
  at the flow temperature the heat curve asks for (`balance.cop_model`). The guide's table for
  the VWL 105 gives the shape: at 35 °C flow 2.86 / 3.38 / 4.68 at −7 / +2 / +7 °C
  (+2 includes defrost), and 76 % / 68 % of that at 45 / 55 °C flow. (Until 28 Nov: the mean of
  the 8 and 10 kW units, within 1.2 % of these.) The flow follows
  `20 + 4.55 (curve x (20 − outdoor))^0.54`, capped at the maximum, fitted to the controller's
  readings within about 1 K. The curve and maximum come from the `events` notes. Each counter period
  gets its own scale, so its heat-weighted COP is what was measured:
  1-14 Nov (curve 1.85, max 55): guide 3.08, measured 2.26, scale 0.73;
  14-22 Nov (curve 1.20, max 45): guide 3.38, measured 2.85, scale 0.84.
  The guide credits the curve change with +10 %, the counters showed +26 %: the rest is outside
  the table (fewer starts and stops, perhaps the flat controller batteries early in November).
  With curve 1.00 and scale 0.84 the COP runs from about 3.7 at +8 °C to 2.4 at +2 °C and 1.96
  at −4 °C. On the frosty 27 Nov that brings the fitted W/K from 277 down to roughly 200.
  `python3 balance.py --cop` prints the periods and the curve.
  Check on 28 Nov 11:18: the controller showed a flow of 43.0 °C at −0.5 °C (its own outdoor
  sensor); the model gives 43.2 °C.
- **Long-run W/K** (`signature.py`, 28 Nov): every whole day on the Pi, in Siviour's form (as for
  co-heating tests): heating / ΔT = W/K − weight x sun / ΔT, where heating is the heat pump (COP
  model) plus gains and sun is house.py's window holes on KNMI's sunshine. Intercept = W/K, slope =
  minus the sun's weight (1: house.py's holes are right). Fitted with the repeated median from
  process-improve, 95 % intervals by bootstrap. Days with under 23 h, no sun data, a mean above
  12 °C outdoors or a heat-curve change are left out; before 20 Nov 19.5 °C stands in for the rooms.
  First run (28 Nov, least squares, sun fixed): 178 ± 11 W/K over 17 days, R² 0.37. On synthetic
  days the robust fit finds W/K within its interval either way, but November's sun (sun / ΔT
  3-21 W/K) cannot fix the weight (interval about −1 to 3); sunny cold days in February and March
  will. Then put the weight into `house.SUN_HOLES`: the forecast, which already uses the forecast
  sunshine, improves with it.
  process-improve 2.6.5: `robust_regression(fit_intercept=False)` keeps the slope of the line with
  an intercept and only drops the intercept, so it is not a fit through the origin; Siviour's form
  needs the intercept, so it is not affected.
- **Room forecasts** (`rooms.py`, 28 Nov): the live view draws, for the living room and the bedroom,
  the setpoint evohome will ask (the stored schedule; an override until the next switch; the frost
  rule's lift on frosty nights) dotted, and a forecast of the room dashed. The room model is one
  state: it cools toward outdoors with a time constant tau and heats at up to `rate` K/h, both fitted
  on the last 7 days. No sun, no neighbours, no floor mass: the hover gives its error over the last
  day. This is the first plant model for an MPC.
  First fit on the Pi (28 Nov): living room tau 111 h, up to 0.48 K/h; at −4 °C that is a net
  0.28 K/h, as seen on 27 Nov (17.0 → 18.5 °C in 5.5 h). From 19 °C on a frosty night the room
  drops only to about 17.4 °C, and climbing back takes about 6 h: heating must start near 01:30
  for 19 °C at 07:30. The bedroom gave no fit: it reads 18.0 or 18.5 all day whatever its
  setpoint (true temperature about 18.2 °C), its 2-3 h windows at 19 °C add a few tenths at most,
  and it hardly cools when its setpoint is low. Its change, about 0.1 K/h, is below the 0.5 °C
  steps: hence the v1 readout (`temp_fine_c`, from 28 Nov). Until a fit holds, a room is held at
  its last reading.
  What keeps the bedroom at 18 °C with its own heating off? It loses roughly 0.4-0.6 kW then (about
  50 W/K: the basement NW wall alone is 31.5 W/K, plus floor, windows and air). The technical room
  (about 21 °C) cannot supply that through a shut hatch: 3.8 m² of wall at 3 K is about 20 W, the
  hatch's gaps a few W. With the hatch wide open, air exchange could carry about 0.2 kW. The more
  likely source is the living room's floor heating above: its pipes sit in the floor over the
  bedroom, and whatever goes down through 33 m² of ceiling heats the bedroom. Tests: (1) the hatch
  wide open for a day, then shut, each noted with `note.py`, against the bedroom and `room.temp_c`;
  (2) with the 0.1 °C readings, does the bedroom warm a few hours after the living room heats?
  The room model then needs the living room (and the technical room) as neighbours.
- **v1 readout** (28 Nov): v1 gives 0.1 °C, not the 0.01 °C expected; still 5x finer than v2.
  First reading: Bedroom v2 18.5 / v1 18.1, Logeer 17.0 / 17.4, Badkamer 18.0 / 17.7, Living
  Room 18.0 / 17.7. All four differ by 0.3-0.4 K, more than rounding to 0.5 °C allows (0.25 K),
  and in both directions, so the two APIs do not report the same moment or the same sensor value. Check over a day: if v2
  were v1 rounded, |v2 − v1| would never exceed 0.25 K.
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
- The technical room (basement) is the street-side corner of the SE side. Its walls: the front
  facade (NE, street side, plywood over masonry, the manifold on it), the SE party wall (bare
  masonry), the Badkamer (towards the back) and the Bedroom, which has the hatch. Kevin calls
  the SE side "SW".
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
- **Ventilation unit, infrared 28 Nov about 11:45** (Itho Daalderop HRU ECO 350): the cupboard's
  surfaces 8 °C; back-left duct (to our rooms, after the heat exchanger) 16 °C; front-right duct
  (from our rooms) 21.5 °C; back-right fully insulated, to outdoors. Outdoors about −0.5 °C.
  Recovery = (16 + 0.5) / (21.5 + 0.5) = 75 %. But 21.5 °C is warmer than any room (17.7-19 °C):
  the extract carries bathroom or kitchen air, or the reading on bare metal is off. With the rooms'
  18.5 °C it is 87 %.
  Two more readings: the extract where it enters the unit 22 °C, so the extract air really is warm;
  the supply on its way down 14.5 °C, 1.5 K under its 16 °C at the unit. So recovery at the unit
  (16 + 0.5) / (22 + 0.5) = 73 %, and what reaches the rooms (14.5 + 0.5) / 22.5 = 67 %. The
  supply duct alone loses 1200 J/m³K x 150 m³/h x 1.5 K = about 75 W in the cupboard (if the flow
  is 150 m³/h). `house.py` now has 75 % (12.5 W/K, was 7.7 at 85 %). An Itho HRU ECO 350 should do
  about 90 %: worth checking the filters (the unit carries a "1+1 gratis filterset" sticker),
  whether its summer bypass is open, and whether supply and extract flows are balanced.
- Bare ducts in the 8 °C cupboard: with about 8 W/m²K on a 160 mm duct (0.5 m² per metre), the
  extract loses about 55 W and the supply about 30 W per metre. 85 % of the extract's loss would
  have come back through the exchanger, so the house loses about 80 W per metre of both ducts, or
  very roughly 400 kWh of heat a season (€30-50 at COP 3). The bare length decides the total.

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
  The 4 and 6 kW units stop at 2.3 kW, so it is probably the 8 or 10 kW unit. Confirmed on 28 Nov:
  the 10 kW VWL 105.
- **The home above** (28 Nov): its tado read 13 °C in the morning, before guests arrived. Through
  the ceiling (37 W/K) that takes about 0.24 kW from us at 19.5 °C inside: a third of the 0.7 kW
  left over. Heated for guests to about 20 °C it takes nothing. A natural experiment: the
  left-over should drop by about 0.2 kW on occupied days (`bookings`), small against the daily noise.
- **Sizing** (28 Nov): the house needs about 180-200 W/K x ΔT (inside 19.5 °C), less about 0.5 kW
  of gains: 2.2-2.5 kW at +7 °C, 4.2-4.7 kW at −4 °C and 4.8-5.3 kW at −7 °C. The VWL 105 gives 9.85 kW at A7/W35 and 7.12 kW at A−7/W35, so it is
  about twice the size the house needs, and in mild weather it can only meet 2 kW by starting and
  stopping (the buffer tank takes the surplus). Cycling costs COP and is a likely part of the
  measured COP sitting 16-27 % under the guide. In frost the unit has spare capacity: the slow
  morning recovery of 27 Nov is the 45 °C flow limit and the floor, not the heat pump. Check:
  starts per day against outdoor temperature (`hp_check.py`).
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

**Living-room front windows, 28 Nov midday:** draught strips going in (foam and E/P-profile rubber).
Before: the old wooden frame read 8 °C on its room side, with the room at about 18 °C and outdoors
about 0 °C. A solid wooden frame (U about 2) would read about 13 °C there, so cold air runs through
the frame or the joint between frame and wall. At 8 °C the wood sits at the dew point of 18 °C air
at 50-55 % humidity: condensation and the black spots on the foam. Check after: the same spot
should read warmer once the draught stops; the `events` note marks the change for the balance.
Before, on the window's frame at the sides: left 12 °C, right 14 °C (the 8 °C was deeper in the
reveal). After: the same three spots, at the same time of day, with no sun on them.

**Infrared, NW wall in the basement (28 Nov, late morning, same thermometer):** 16 °C on average at
4 spots, room 18 °C. The documents give 80-100 mm PIR between studs (U 0.28): then the wall would
read within about 0.3 K of the room.

- Heat into the wall = 7.7 W/m²K x 2 K = 15 W/m², x 21 m² = about 0.32 kW (±0.08 kW per 0.5 K of
  reading error).
- With the NW house at 0.40 of the way from outdoors to us (as on the living level; outdoors about
  0-2 °C, so 7-8 °C), that is U 1.4-1.6: an uninsulated or badly bridged wall, not PIR.
  `house.py` now has U 1.5 ("est"): +26 W/K, of which about 15 W/K follows the outdoor temperature.
  It brings house.py closer to the 178 W/K that `signature.py` found.
- The NW house starts higher (Kevin, 28 Nov): the lowest 40 % of this wall (8.4 m²) has soil
  behind it, the upper 60 % (12.6 m²) the NW house. `house.py` splits the wall that way, with the
  soil at `GROUND_C` (11 °C). shortcut: soil this shallow is colder in winter than deep ground;
  `knmi_live.soil_c` (1 m deep) could replace it. Splitting the 2 K the same way gives U 1.5-1.7, so
  U 1.5 stays. The SE neighbour's house mirrors ours: its basement is at our level.
- Top against bottom (Kevin, 28 Nov): the wall reads about 1 K warmer at the top than at the
  bottom. Part of that is the room's air, warmer under the ceiling. The rest says the soil behind
  the lower part is no warmer than the NW house behind the upper part: with U 1.5 and the room's
  air at 18 °C throughout, 15.5 and 16.5 °C at the surface mean about 5 and 10 °C behind the wall.
  So the 11 °C of `GROUND_C` is too warm for this shallow soil; it underestimates that part's loss
  by about 0.05 kW. To split air from wall: the same two readings on an inner wall of the bedroom
  (nothing cold behind it) give the air's share alone.
- **Living-room ceiling, back (glass-wool part), 28 Nov late morning:** 17.8 °C, with the home above
  at 13 °C (tado, holiday mode). Through 120 mm glass wool (U 0.37) the ceiling should sit about
  0.2 K under the air next to it (5 K x 0.37 W/m²K / 10 W/m²K at a ceiling); uninsulated (U 1.3)
  about 0.7 K. With the room near 18 °C it fits the insulated case, but the gap is near the
  thermometer's error, and the air under a ceiling is warmer than at the thermostat. A reference
  makes it decisive: the top of an inner wall, next to the ceiling, at the same moment.
- To tell missing insulation from bridging: a line of readings every 15 cm across 1.2 m at
  shoulder height. Cold stripes about 60 cm apart are studs (insulation there, but bridged); an
  even 2 K under the room is no insulation.

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

1. The home above has a wired tado Smart Thermostat X with a Bridge X from 28 Nov, no Auto-Assist,
   logged by `tado_logger.py` every 20 minutes. It measures the other side of the ceiling (37 W/K),
   one of the three unknown temperatures in the balance: use it in the balance once a few days
   are logged. Guest stays come from the booking calendar (`bnb_calendar.py`, `bookings`); the
   dates stay on the Pi, not in this repo. Its boiler (photo 28 Nov): a Remeha Tzerra gas combi,
   heating knob at 70 °C, hot water at 65 °C; service stickers for 2017 and 2018 only. Is the tado
   wired to it on/off or OpenTherm (the tado app's thermostat settings, or the terminals it uses)?
2. From 29 Nov: v1 against v2 over a day. If v2 were v1 rounded to 0.5 °C, no gap would pass 0.25 K:
   `python3 -c "import sqlite3; db = sqlite3.connect('file:p1.sqlite?mode=ro', uri=True); [print(*r) for r in db.execute('SELECT zone, count(*), round(avg(temp_c - temp_fine_c), 2), round(min(temp_c - temp_fine_c), 2), round(max(temp_c - temp_fine_c), 2), round(avg(abs(temp_c - temp_fine_c) > 0.25), 2) FROM zones WHERE temp_fine_c IS NOT NULL GROUP BY 1')]"`
   (zone, readings, mean v2 − v1, min, max, share beyond 0.25 K).
3. Overdue: both Vaillant counters (`python3 note.py --counters`), about once a week and before
   every curve change (last: 22 Nov, 385 and 563 kWh).
4. To do, ventilation (Itho Daalderop HRU ECO 350, heat recovery measured at about 73 %): replace or
   clean the filters; check that the summer bypass is closed and that supply and extract flows
   are balanced (the unit's settings); insulate the two bare ducts to and from our rooms in the
   cold cupboard (about 80 W per metre of both; measure their length and diameter first); note
   the flow setting in m3/h. Log each step with `note.py`.
5. To do, the home above: a HomeWizard P1 on its own smart meter, reachable from the Pi. Its gas
   is the boiler's heat input, so with the tado temperature the home above gets its own heat
   balance, and the ceiling between us can be measured both ways. `p1_logger.py` then needs a
   second meter (host and token).
6. Surface temperatures with the same infrared thermometer, on a cold morning, best while the home
   above is cold: the kitchen ceiling, with the top of an inner wall as reference (the living-room
   ceiling read 17.8 °C on 28 Nov), the SE wall in the basement (4 spots, like the NW
   one), the technical room's front and party walls (to replace their guessed U in `house.py`),
   a line across the basement NW wall every 15 cm for 1.2 m (studs or no insulation?), and the
   top and bottom of an inner wall of the bedroom (the room air's own top-to-bottom difference).
   Note the room temperature each time. The basement NW wall is done (28 Nov: 16 °C, room 18 °C).
7. Ventilation: the air temperature in the air stream at one supply vent and one extract valve
   (bathroom or kitchen), with a thermometer, not infrared, on a cold day, with the time.
8. Is there insulation between the living level's floor-heating pipes and the bedroom ceiling
   below them? (The technical room is 2.10 x 1.80 m: split out in `house.py`, 28 Nov.)
9. Does the myVAILLANT app show the heat pump (an internet gateway, sensoNET or VR 921)? Then its
   daily electricity and environmental yield can be read by a script, instead of the counters by
   hand (question 3), and the eBUS adapter may not be needed for them.
10. Which phase are the dishwasher and the washing machine on? Log the start of a run and look at
    the steps per phase 25 minutes later. (Oven, combi-oven, hob and Quooker are not on L1.)
11. Heat pump (the plate says VWL 105, 10 kW): the buffer's litres, and whether a return
    temperature and a flow rate can be read anywhere (the flow temperature can).
12. The back windows and doors of the basement: glass type and sizes.
13. What stands against the NW wall on the living level (fitted cupboards, kitchen units, a
    chimney breast)? It covers part of the 30 m².
14. In winter, from when to when does direct sun reach the back windows?
15. Hot-water heater: the litres on its plate.
16. Small: what is the "pump" on group 3, and which appliance on L1 runs at about 1.1 kW for 4
    minutes at meal times?
17. Decisions: the eBUS adapter, the probes (the home above's air probe may no longer be needed
    with the tado), and a cable route from the technical room to the living level.

**KNMI hourly API:** in `start=YYYYMMDDHH&end=YYYYMMDDHH` the hour is a range within each day, not
a moment. Always ask for hours 01 to 24.

## Open items

- Heat balance: add the `knmi_hourly.py` cron line (README), and log the Vaillant "this month"
  counters about once a week.
- The ground under the basement is a constant 11 °C in `house.py`. `knmi_live.soil_c` (soil at 1 m,
  if Rotterdam reports it) could replace it: decide once a winter of it exists.
- Noise sensor: turn up its gain knob and test by clapping. Kevin can't reach the room easily at
  the moment.
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
