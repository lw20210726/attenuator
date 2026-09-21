#!/usr/bin/env python3
"""
Lay out pi-shape resistor attenuator stages.

Each pi stage has:
  - 2 shunt resistors (vertical, upper + lower, going to GND)
  - 3 series resistors (horizontal, in the signal path)

The "neck" is the gap between the shunt pad edge and the series pad edge,
measured pad-edge-to-pad-edge = 0.8 mm.

The series resistors are placed touching (pad-to-pad, ~0 gap) in a row.

ASCII art of one pi stage (top view):

         shunt_upper        shunt_upper
            |                   |
    ──[series1][series2][series3]──
            |                   |
         shunt_lower        shunt_lower
        (left pair)         (right pair)

Run with:
    kh exec place_pi_attenuator.py

After running, press Ctrl+S in KiCad to save, then verify with:
    kh view --pcb . --refs <refs> --margin 3 --out /tmp/pi_check.png

Edit the STAGES list below to define your attenuator chain.
"""

from kicad_harness.live import Commit, footprints_by_ref
from kipy.geometry import Vector2, Angle

# ─── Geometry constants (R_0805_2012Metric) ──────────────────────────
# Pad: 1.025 × 1.4 mm, centres at ±0.9125 mm from footprint centre.
PAD_X_HALF   = 1.025 / 2          # 0.5125 mm
PAD_Y_HALF   = 1.4  / 2           # 0.7    mm
PAD_CENTRE   = 0.9125             # mm from fp centre

# Derived extents ────────────────────────────────────────────────────
# Series (0°): pads along X.  Outer pad edge = PAD_CENTRE + PAD_X_HALF
SERIES_PAD_OUTER = PAD_CENTRE + PAD_X_HALF     # 1.425 mm from fp centre

# Shunt (90°): pads rotate → the 1.4-mm dimension is now along X.
# Pad X extent from fp centre = PAD_Y_HALF = 0.7 mm
SHUNT_PAD_X_EXTENT = PAD_Y_HALF                # 0.7 mm from fp centre

# ─── Spacing rules ──────────────────────────────────────────────────
NECK_GAP     = 0.8   # mm, pad-edge to pad-edge  (the "0.8 mm neck")
SERIES_GAP   = 0.0   # mm, gap between adjacent series pads (touching)

# Centre-to-centre distances
NECK_CC  = NECK_GAP + SHUNT_PAD_X_EXTENT + SERIES_PAD_OUTER  # shunt↔series
SERIES_CC = SERIES_GAP + 2 * SERIES_PAD_OUTER                 # series↔series

# Shunt pair: upper and lower sit symmetrically about the signal line.
# The inner pad edge of each shunt touches the signal line.
# Inner pad edge = fp_centre ± (PAD_CENTRE + PAD_X_HALF) along Y at 90°/−90°.
# Actually at ±90° the 1.025-mm dimension is along Y.
# Inner pad edge Y offset from fp centre = PAD_CENTRE + PAD_X_HALF = 1.425
SHUNT_Y_OFFSET = PAD_CENTRE + PAD_X_HALF       # 1.425 mm from signal line

# ─── Stage definition ───────────────────────────────────────────────
# Each pi stage is defined as:
#   shunt_upper_left, shunt_lower_left : the left shunt pair
#   series: [R_top, R_mid, R_bot]      : the three series resistors
#   shunt_upper_right, shunt_lower_right: the right shunt pair
#
# NOTE: The first stage's left shunt pair is placed. For subsequent stages,
# the left shunt pair IS the previous stage's right shunt pair (shared),
# so it is NOT re-placed. Only define it in the first stage.
#
# Format: list of dicts, each with:
#   "shunt_l": [upper_ref, lower_ref]   — only used for first stage
#   "series":  [ref1, ref2, ref3]        — the three series resistors
#   "shunt_r": [upper_ref, lower_ref]   — right shunt pair (shared with next stage)
#
# The rotations of shunt resistors follow the existing pattern:
#   upper shunt: −90° (pad 1 toward signal line)
#   lower shunt:  90° (pad 1 toward signal line)
#   series (odd position): 0°
#   series (even position, middle): 180°

# ═══════════════════════════════════════════════════════════════════
# ▶▶▶  EDIT THIS SECTION  ◀◀◀
# ═══════════════════════════════════════════════════════════════════

# Starting position (mm) — top-left anchor of the chain
X0 = 98.2       # X of the first (left) shunt pair centre
Y0 = 65.228     # Y of the signal line (series resistors' Y centres)

# Define your pi stages here.
# Each stage's left shunt pair overlaps with the previous stage's right pair.
# Only the FIRST stage needs "shunt_l" — the rest inherit from previous "shunt_r".
STAGES = [
    {"shunt_l": ["R1",  "R2"],   "series": ["R5",  "R6",  "R7"],   "shunt_r": ["R14", "R13"]},
    {                             "series": ["R18", "R19", "R20"],  "shunt_r": ["R24", "R26"]},
    {                             "series": ["R30", "R31", "R32"],  "shunt_r": ["R38", "R36"]},
    {                             "series": ["R41", "R42", "R43"],  "shunt_r": ["R50", "R49"]},
    {                             "series": ["R53", "R54", "R55"],  "shunt_r": ["R60", "R59"]},
]

# ═══════════════════════════════════════════════════════════════════


def place_shunt_pair(fps, board_obj, upper_ref, lower_ref, x, y_signal, placed):
    """Place a shunt pair: upper at −90°, lower at +90°, centred on x."""
    fp_upper = fps[upper_ref]
    fp_upper.position = Vector2.from_xy_mm(x, y_signal - SHUNT_Y_OFFSET)
    fp_upper.orientation = Angle.from_degrees(-90)
    board_obj.update_items(fp_upper)
    placed[upper_ref] = (x, y_signal - SHUNT_Y_OFFSET, -90)

    fp_lower = fps[lower_ref]
    fp_lower.position = Vector2.from_xy_mm(x, y_signal + SHUNT_Y_OFFSET)
    fp_lower.orientation = Angle.from_degrees(90)
    board_obj.update_items(fp_lower)
    placed[lower_ref] = (x, y_signal + SHUNT_Y_OFFSET, 90)


def place_series_triplet(fps, board_obj, refs, x_start, y_signal, placed):
    """Place three series resistors left-to-right, alternating 0°/180°/0°."""
    rotations = [0, 180, 0]
    for i, (ref, rot) in enumerate(zip(refs, rotations)):
        x = x_start + i * SERIES_CC
        fp = fps[ref]
        fp.position = Vector2.from_xy_mm(x, y_signal)
        fp.orientation = Angle.from_degrees(rot)
        board_obj.update_items(fp)
        placed[ref] = (x, y_signal, rot)
    # Return X of last series resistor
    return x_start + 2 * SERIES_CC


# ─── Main placement ─────────────────────────────────────────────────
fps = footprints_by_ref(board)  # noqa: F821

# Collect all refs to check they exist on the board
all_refs = []
for i, stage in enumerate(STAGES):
    if "shunt_l" in stage:
        all_refs.extend(stage["shunt_l"])
    all_refs.extend(stage["series"])
    all_refs.extend(stage["shunt_r"])

missing = [r for r in all_refs if r not in fps]
if missing:
    raise SystemExit(f"Not on this board: {missing}")

placed = {}
shunt_x = X0  # X position for the current left shunt pair

with Commit(board, "place pi attenuator stages"):  # noqa: F821
    for i, stage in enumerate(STAGES):
        # 1. Place left shunt pair (only for first stage)
        if "shunt_l" in stage:
            place_shunt_pair(fps, board, stage["shunt_l"][0], stage["shunt_l"][1],
                             shunt_x, Y0, placed)

        # 2. Place series triplet: first series starts NECK_CC to the right of shunt
        series_x_start = shunt_x + NECK_CC
        last_series_x = place_series_triplet(fps, board, stage["series"],
                                             series_x_start, Y0, placed)

        # 3. Place right shunt pair: NECK_CC to the right of last series
        shunt_x = last_series_x + NECK_CC
        place_shunt_pair(fps, board, stage["shunt_r"][0], stage["shunt_r"][1],
                         shunt_x, Y0, placed)

# Report
print(f"Placed {len(placed)} resistors in {len(STAGES)} pi stages.")
print(f"Signal line Y = {Y0:.3f} mm")
print(f"Chain X range: {X0:.3f} to {shunt_x:.3f} mm")
print(f"Neck gap (pad edge-to-edge): {NECK_GAP} mm")
print(f"Neck centre-to-centre: {NECK_CC:.4f} mm")
print(f"Series centre-to-centre: {SERIES_CC:.4f} mm")
print(f"Stage width (shunt-to-shunt): {2*NECK_CC + 2*SERIES_CC:.4f} mm")
print()
for ref in sorted(placed, key=lambda r: int(r[1:])):
    x, y, rot = placed[ref]
    print(f"  {ref:6s}  x={x:8.3f}  y={y:8.3f}  rot={rot:6.1f}")

result = placed
