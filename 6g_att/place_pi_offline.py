#!/usr/bin/env python3
"""
Generate pi-shape resistor attenuator placement (offline — no KiCad API needed).

Writes a placement JSON file, then applies it with `kh place`.

Usage:
    python3 place_pi_offline.py                         # generate + apply
    python3 place_pi_offline.py --dry-run               # show what would be placed
    python3 place_pi_offline.py --json-only placement.json   # write JSON only

Then verify:
    kh view --pcb . --refs R1,R2,R5,R6,R7,R13,R14 --margin 3 --out /tmp/pi_check.png

Edit the STAGES list and starting position (X0, Y0) below.
"""

import argparse
import json
import subprocess
import sys
import os

# ─── Geometry constants (R_0805_2012Metric) ──────────────────────────
PAD_X_HALF   = 1.025 / 2          # 0.5125 mm
PAD_Y_HALF   = 1.4  / 2           # 0.7    mm
PAD_CENTRE   = 0.9125             # mm from fp centre

SERIES_PAD_OUTER = PAD_CENTRE + PAD_X_HALF     # 1.4250 mm
SHUNT_PAD_X_EXTENT = PAD_Y_HALF                # 0.7000 mm

# ─── Spacing rules ──────────────────────────────────────────────────
NECK_GAP     = 0.8   # mm, pad-edge to pad-edge
SERIES_GAP   = 0.0   # mm, gap between adjacent series pads

NECK_CC  = NECK_GAP + SHUNT_PAD_X_EXTENT + SERIES_PAD_OUTER
SERIES_CC = SERIES_GAP + 2 * SERIES_PAD_OUTER

SHUNT_Y_OFFSET = PAD_CENTRE + PAD_X_HALF       # 1.4250 mm

# ═══════════════════════════════════════════════════════════════════
# ▶▶▶  EDIT THIS SECTION  ◀◀◀
# ═══════════════════════════════════════════════════════════════════

X0 = 98.2       # X of the first (left) shunt pair centre
Y0 = 65.228     # Y of the signal line

# Pi stages: shunt_l only for first stage; shunt_r[0]=upper, shunt_r[1]=lower
STAGES = [
    {"shunt_l": ["R1",  "R2"],   "series": ["R5",  "R6",  "R7"],   "shunt_r": ["R14", "R13"]},
    {                             "series": ["R18", "R19", "R20"],  "shunt_r": ["R24", "R26"]},
    {                             "series": ["R30", "R31", "R32"],  "shunt_r": ["R38", "R36"]},
    {                             "series": ["R41", "R42", "R43"],  "shunt_r": ["R50", "R49"]},
    {                             "series": ["R53", "R54", "R55"],  "shunt_r": ["R60", "R59"]},
]

# ═══════════════════════════════════════════════════════════════════


def compute_placement():
    """Return {ref: [x, y, rot]} for all resistors."""
    placement = {}
    shunt_x = X0

    for i, stage in enumerate(STAGES):
        # Left shunt pair (first stage only)
        if "shunt_l" in stage:
            upper, lower = stage["shunt_l"]
            placement[upper] = [round(shunt_x, 4), round(Y0 - SHUNT_Y_OFFSET, 4), -90]
            placement[lower] = [round(shunt_x, 4), round(Y0 + SHUNT_Y_OFFSET, 4), 90]

        # Series triplet
        series_x_start = shunt_x + NECK_CC
        rotations = [0, 180, 0]
        for j, (ref, rot) in enumerate(zip(stage["series"], rotations)):
            x = series_x_start + j * SERIES_CC
            placement[ref] = [round(x, 4), round(Y0, 4), rot]

        last_series_x = series_x_start + 2 * SERIES_CC

        # Right shunt pair
        shunt_x = last_series_x + NECK_CC
        upper, lower = stage["shunt_r"]
        placement[upper] = [round(shunt_x, 4), round(Y0 - SHUNT_Y_OFFSET, 4), -90]
        placement[lower] = [round(shunt_x, 4), round(Y0 + SHUNT_Y_OFFSET, 4), 90]

    return placement


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print placement without applying")
    ap.add_argument("--json-only", metavar="FILE",
                    help="Write placement JSON only, don't apply")
    ap.add_argument("--pcb", default=".",
                    help="Board file or project dir (default: cwd)")
    a = ap.parse_args()

    placement = compute_placement()

    print(f"Pi attenuator placement: {len(placement)} resistors, {len(STAGES)} stages")
    print(f"Neck gap (pad edge-to-edge): {NECK_GAP} mm")
    print(f"Signal line Y = {Y0:.3f} mm")
    print(f"Chain X: {X0:.3f} to {max(p[0] for p in placement.values()):.3f} mm")
    print()

    for ref in sorted(placement, key=lambda r: int(r[1:])):
        x, y, rot = placement[ref]
        print(f"  {ref:6s}  x={x:8.3f}  y={y:8.3f}  rot={rot:6.1f}")

    if a.dry_run:
        print("\nDry run — nothing written.")
        return

    # Write JSON
    json_path = a.json_only or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "_pi_placement.json")
    with open(json_path, "w") as f:
        json.dump(placement, f, indent=2)
    print(f"\nWrote {json_path}")

    if a.json_only:
        return

    # Apply with kh place
    cmd = ["kh", "place", "--pcb", a.pcb, "--json", json_path]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode:
        sys.exit(f"kh place failed with exit code {result.returncode}")
    print("Done. Open the board in KiCad (or File → Revert) to see the changes.")


if __name__ == "__main__":
    main()
