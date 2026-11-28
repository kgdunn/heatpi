#!/usr/bin/env python3
"""The house as the heat balance sees it: every part that loses heat, and where that heat goes.

Two levels of 6 m x 10 m: a basement (2.1 m high) and a living level (3.0 m). The front faces
north-east, the back south-west. Both side walls are shared with neighbours and another home sits
on top, so most of the surface faces other buildings, not the weather.

Each part loses  W/K x (inside - other side)  watts, with W/K = area x U.
Source "doc": from the insulation documents and glass labels. Source "est": read off photos and the
floor plan, or a textbook value. Measure the "est" numbers and replace them.

Usage:  python3 house.py        (prints the table)
"""
LAT, LON = 52.01, 4.37          # city level is enough for the sun's position
GROUND_C = 11.0                 # deep ground: close to the yearly mean air temperature
PEOPLE_W = 130                  # body heat of the people at home, averaged over day and night
# The Vaillant's settings in force. Logged changes ("heat curve 1.20 -> 1.00", "max target flow 55 -> 45")
# give the history before them; these hold when there is none.
HEAT_CURVE, MAX_FLOW_C = 1.00, 45.0
HEATING_BELOW_C = 16.0          # the Vaillant heats only below this outdoor temperature ("OT switch-off")
COP_SCALE_DEFAULT = 0.8         # real COP / guide COP before any counters are logged (Nov 2026 gave 0.73-0.84)
# frost_rule.py: on a night with frost in the forecast, lift this zone's setpoint slots below the floor.
FROST_RULE = {"zone": "Living Room", "floor": 18.0, "frost": 0.0}

# evohome zone -> (level, floor area in m2 read off the floor plan). Weights the room temperatures.
ZONES = {"Living Room": ("living", 55), "Bedroom": ("basement", 33),
         "Logeer": ("basement", 8), "Badkamer": ("basement", 5)}

# Sun: facade direction (degrees from north) -> m2 of "open hole" that lets sunshine in.
# Hole = window area x glass share x solar factor of the glass x shade. All "est".
#   back:  living level 9.5 x 0.65 x 0.60 x 0.7 (pergola, trees) + basement 8.0 x 0.65 x 0.60 x 0.4 (balcony above)
#   front: living level 5.8 x 0.70 x 0.55 x 0.8 (trees)          + basement 2.0 x 0.55 x 0.55 x 0.8
SUN_HOLES = {225: 3.8, 45: 2.3}

MEASURED = ("outdoors", "ground")   # other sides with a known temperature
# Other sides without a sensor yet: where their temperature sits on the way from outdoors (0) to our
# living level (1). Infrared readings of 22 and 23 Nov 2026 gave the NW wall 3 K (evening, 2 °C outdoors)
# and 3.75 K (morning, -1 °C outdoors) under the room. With 7.7 W/m2K into the wall and U 2.4 through
# it, the house behind it sat at 8.4 and 6.7 °C: 0.40 and 0.39 of the way. Replace by a probe on the
# wall. The SE neighbour and the home above stay unknown.
ESTIMATED = {"neighbour NW": 0.40}


def part(name, group, inside, to, source, note, area=None, u=None, wk=None):
    """One heat-losing part. Give area (m2) and U (W/m2K), or W/K directly for an air flow."""
    return {"name": name, "group": group, "inside": inside, "to": to, "source": source, "note": note,
            "area": area, "u": u, "wk": round(area * u if wk is None else wk, 1)}


PARTS = [
    part("Front windows, living level", "windows", "living", "outdoors", "est",
         "vacuum glass (U 0.4, doc) in old frames; 2 x about 1.15 x 2.5 m, from a photo", area=5.8, u=1.1),
    part("Back windows, living level", "windows", "living", "outdoors", "est",
         "HR++ glass (U 1.1, doc), aluminium spacers, new frames; area from a photo", area=9.5, u=1.5),
    part("Front windows, basement", "windows", "basement", "outdoors", "est",
         "vacuum glass, also in the arched top lights; old unsealed frames; 2 x about 1.0 x 1.0 m, from a photo",
         area=2.0, u=1.4),
    part("Back windows and doors, basement", "windows", "basement", "outdoors", "est",
         "glass type assumed as on the living level (HR++); area from the plan", area=8.0, u=1.4),
    part("Front and back walls, living level", "walls", "living", "outdoors", "est",
         "about 100 mm PIR between studs (front: doc)", area=20.7, u=0.28),
    part("Front and back walls, basement", "walls", "basement", "outdoors", "doc",
         "80-100 mm PIR between studs; the lowest 0.7 m at the front is below the street", area=15.2, u=0.28),
    part("Ventilation unit", "ventilation", "house", "outdoors", "est",
         "150 m3/h at 85 % heat recovery, both assumed", wk=7.7),
    part("Ventilation ducts, cold cupboard", "ventilation", "house", "outdoors", "est",
         "bare ducts; drops once they are insulated", wk=12.0),
    part("Draughts", "draughts", "house", "outdoors", "est",
         "0.15 air changes per hour of 306 m3", wk=15.0),
    part("Basement floor", "ground", "basement", "ground", "doc",
         "80 mm PIR under the slab; the ground itself insulates too", area=60.0, u=0.20),
    part("NW side wall, living level", "neighbour_nw", "living", "neighbour NW", "est",
         "plaster on 10 cm brick, bare", area=30.0, u=2.4),
    # The NW house starts higher: the lowest 40 % of this wall has soil behind it. Infrared on 28 Nov:
    # 2 K under the room at 4 spots, not the 80-100 mm PIR of the documents (NOTES.md).
    part("NW side wall, basement, above soil", "neighbour_nw", "basement", "neighbour NW", "est",
         "upper 60 %: the NW house behind it; bare or badly bridged", area=12.6, u=1.5),
    part("NW side wall, basement, in soil", "ground", "basement", "ground", "est",
         "lower 40 %: soil under the NW house; same wall, so the same U", area=8.4, u=1.5),
    part("SE side wall, living level", "neighbours", "living", "neighbour SE", "est",
         "plaster on 10 cm brick, bare", area=30.0, u=2.4),
    part("SE side wall, basement", "neighbours", "basement", "neighbour SE", "doc",
         "80-100 mm PIR between studs", area=21.0, u=0.28),
    part("Ceiling, living room part", "neighbours", "living", "home above", "doc",
         "120 mm glass wool between the joists", area=44.0, u=0.37),
    part("Ceiling, kitchen part", "neighbours", "living", "home above", "est",
         "old timber floor, untouched", area=16.0, u=1.3),
]

if __name__ == "__main__":
    total = sum(p["wk"] for p in PARTS)
    print(f"{'Part':36} {'m2':>5} {'U':>5} {'W/K':>6} {'share':>6}  {'loses to':13} src  note")
    for p in PARTS:
        size = f"{p['area']:5.1f} {p['u']:5.2f}" if p["area"] else " " * 11
        share = float(f"{100 * p['wk'] / total:.2g}")                   # at most 2 significant figures
        print(f"{p['name']:36} {size} {p['wk']:6.1f} {share:5g}%  {p['to']:13} {p['source']}  {p['note']}")
    known = sum(p["wk"] for p in PARTS if p["to"] in MEASURED)
    guessed = sum(p["wk"] for p in PARTS if p["to"] in ESTIMATED)
    print(f"\n{known:.0f} W/K to outdoors and ground (computed in the balance), {guessed:.0f} W/K to "
          f"{' and '.join(ESTIMATED)} (estimated), {total - known - guessed:.0f} W/K not measured yet")
