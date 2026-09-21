#!/usr/bin/env python3
"""
Place ALL pi-attenuator resistors on the 6g_att board.

Properly handles:
- Standard single-resistor shunt arms:
    -|-
- Multi-resistor stacked vertical shunt arms (e.g. R62/R64 and R73/R74):
     |
    -|-
     |
- Series resistor chains (arbitrary length: 1, 2, 3, 4, 5 resistors touching pad-to-pad)
- Exact 0.8mm pad-edge-to-pad-edge neck gap between shunt arms and series resistors.

Lays out each channel (Channel 1, Channel 2, and Channel 3) as a separate horizontal ladder.

Usage:
    python3 place_all_pi.py                   # apply placement to board
    python3 place_all_pi.py --dry-run         # preview positions only
"""

import re
import json
import subprocess
import sys
import os
from collections import defaultdict

# ── Geometry (0805 Metric) ──────────────────────────────────────────
PAD_X_HALF   = 1.025 / 2       # 0.5125 mm
PAD_Y_HALF   = 1.4  / 2        # 0.7000 mm
PAD_CENTRE   = 0.9125          # mm from fp centre

SERIES_PAD_OUTER = PAD_CENTRE + PAD_X_HALF   # 1.4250 mm
SHUNT_PAD_X      = PAD_Y_HALF                # 0.7000 mm

NECK_GAP   = 0.8                             # mm (pad edge to pad edge)
SERIES_GAP = 0.0                             # mm (touching pads)

NECK_CC           = NECK_GAP + SHUNT_PAD_X + SERIES_PAD_OUTER   # 2.925 mm
SERIES_CC         = SERIES_GAP + 2 * SERIES_PAD_OUTER           # 2.850 mm
SHUNT_Y           = PAD_CENTRE + PAD_X_HALF                     # 1.425 mm
SHUNT_STACK_PITCH = 2 * (PAD_CENTRE + PAD_X_HALF)               # 2.850 mm

# ── Geometry (0603 Metric for Tune Channel) ─────────────────────────
PAD_X_HALF_0603   = 0.8 / 2                  # 0.4000 mm
PAD_Y_HALF_0603   = 0.95 / 2                 # 0.4750 mm
PAD_CENTRE_0603   = 0.825                    # mm from fp centre

SERIES_PAD_OUTER_0603  = PAD_CENTRE_0603 + PAD_X_HALF_0603   # 1.2250 mm
SHUNT_PAD_X_0603       = PAD_Y_HALF_0603                     # 0.4750 mm

NECK_CC_0603           = NECK_GAP + SHUNT_PAD_X_0603 + SERIES_PAD_OUTER_0603 # 2.500 mm
SERIES_CC_0603         = SERIES_GAP + 2 * SERIES_PAD_OUTER_0603              # 2.450 mm
SHUNT_Y_0603           = PAD_CENTRE_0603 + PAD_X_HALF_0603                   # 1.225 mm
SHUNT_STACK_PITCH_0603 = 2 * (PAD_CENTRE_0603 + PAD_X_HALF_0603)             # 2.450 mm

# ── Parse netlist ───────────────────────────────────────────────────
NETLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "6g_att.net")
TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')

def parse_sexpr(text):
    toks = TOKEN.findall(text)
    pos = 0
    def walk():
        nonlocal pos
        out = []
        pos += 1
        while toks[pos] != ")":
            if toks[pos] == "(":
                out.append(walk())
            else:
                t = toks[pos]
                out.append(t[1:-1] if t.startswith('"') else t)
                pos += 1
        pos += 1
        return out
    return walk()

def children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]

tree = parse_sexpr(open(NETLIST, encoding="utf-8").read())
comps = {children(c, "ref")[0][1]: children(c, "value")[0][1]
         for c in children(children(tree, "components")[0], "comp")}

pin_net = {}
gnd_net = None
for n in children(children(tree, "nets")[0], "net"):
    name = children(n, "name")[0][1]
    if name.strip("/").upper() == "GND":
        gnd_net = name
    for node in children(n, "node"):
        pin_net[(children(node, "ref")[0][1], children(node, "pin")[0][1])] = name

resistors = [r for r in comps if re.match(r"^R\d+$", r)]
r_nets = {r: (pin_net.get((r, "1")), pin_net.get((r, "2"))) for r in resistors}
direct_gnd_resistors = [r for r, (n1, n2) in r_nets.items() if n1 == gnd_net or n2 == gnd_net]

net_to_r = defaultdict(list)
for r, (n1, n2) in r_nets.items():
    if n1: net_to_r[n1].append(r)
    if n2: net_to_r[n2].append(r)

# ── Extract all shunt branches (ending at GND) ──────────────────────
shunt_branches = []
visited = set()
for r in direct_gnd_resistors:
    if r in visited:
        continue
    branch = [r]
    visited.add(r)
    n1, n2 = r_nets[r]
    curr_net = n2 if n1 == gnd_net else n1
    while curr_net and curr_net != gnd_net:
        other_rs = [x for x in net_to_r[curr_net] if x not in visited]
        if len(net_to_r[curr_net]) == 2 and len(other_rs) == 1:
            next_r = other_rs[0]
            visited.add(next_r)
            branch.append(next_r)
            rn1, rn2 = r_nets[next_r]
            curr_net = rn2 if rn1 == curr_net else rn1
        else:
            break
    branch.reverse()  # now ordered [inner_resistor, ..., gnd_resistor]
    shunt_branches.append((curr_net, branch))

junction_shunts = defaultdict(list)
for jnet, br in shunt_branches:
    junction_shunts[jnet].append(br)

# ── Extract series resistors between junctions ──────────────────────
series_resistors = set(resistors) - set(r for _, br in shunt_branches for r in br)
series_net_to_r = defaultdict(list)
for r in series_resistors:
    n1, n2 = r_nets[r]
    series_net_to_r[n1].append(r)
    series_net_to_r[n2].append(r)

def trace_channel(start_jnet):
    chain = []
    cur_jnet = start_jnet
    visited_jnets = set()
    while cur_jnet:
        visited_jnets.add(cur_jnet)
        shs = junction_shunts[cur_jnet]
        next_jnet = None
        series_path = []
        for r in series_net_to_r[cur_jnet]:
            path = [r]
            n1, n2 = r_nets[r]
            curr_n = n2 if n1 == cur_jnet else n1
            while curr_n not in junction_shunts:
                cands = [x for x in series_net_to_r[curr_n] if x != path[-1] and x not in path]
                if not cands:
                    break
                path.append(cands[0])
                pn1, pn2 = r_nets[cands[0]]
                curr_n = pn2 if pn1 == curr_n else pn1
            if curr_n in junction_shunts and curr_n not in visited_jnets:
                next_jnet = curr_n
                series_path = path
                break
        chain.append((cur_jnet, shs, series_path))
        cur_jnet = next_jnet
    return chain

# ── Layout a single channel ladder ──────────────────────────────────
def layout_channel(chain, x0, y0, neck_gap=0.8, is_0603=False):
    if is_0603:
        neck_cc     = neck_gap + SHUNT_PAD_X_0603 + SERIES_PAD_OUTER_0603
        series_cc   = SERIES_CC_0603
        shunt_y     = SHUNT_Y_0603
        shunt_pitch = SHUNT_STACK_PITCH_0603
    else:
        neck_cc     = neck_gap + SHUNT_PAD_X + SERIES_PAD_OUTER
        series_cc   = SERIES_CC
        shunt_y     = SHUNT_Y
        shunt_pitch = SHUNT_STACK_PITCH

    placement = {}
    jx = x0

    for stage_idx, (jnet, shs, srs) in enumerate(chain):
        # Shunt arm UP (negative Y direction)
        if len(shs) > 0 and shs[0]:
            arm_up = shs[0]
            curr_sig_net = jnet
            for idx, r in enumerate(arm_up):
                y = y0 - shunt_y - idx * shunt_pitch
                p1_net, p2_net = r_nets[r]
                # Orient so signal-side pad points to +Y (towards signal line)
                rot = -90 if p1_net == curr_sig_net else 90
                placement[r] = (round(jx, 4), round(y, 4), rot)
                curr_sig_net = p2_net if p1_net == curr_sig_net else p1_net

        # Shunt arm DOWN (positive Y direction)
        if len(shs) > 1 and shs[1]:
            arm_down = shs[1]
            curr_sig_net = jnet
            for idx, r in enumerate(arm_down):
                y = y0 + shunt_y + idx * shunt_pitch
                p1_net, p2_net = r_nets[r]
                # Orient so signal-side pad points to -Y (towards signal line)
                rot = 90 if p1_net == curr_sig_net else -90
                placement[r] = (round(jx, 4), round(y, 4), rot)
                curr_sig_net = p2_net if p1_net == curr_sig_net else p1_net

        # Horizontal series resistors
        if srs:
            first_s_x = jx + neck_cc
            curr_sig_net = jnet
            for idx, r in enumerate(srs):
                sx = first_s_x + idx * series_cc
                p1_net, p2_net = r_nets[r]
                # Orient so incoming-side pad points to -X (left)
                rot = 0 if p1_net == curr_sig_net else 180
                placement[r] = (round(sx, 4), round(y0, 4), rot)
                curr_sig_net = p2_net if p1_net == curr_sig_net else p1_net

            last_s_x = first_s_x + (len(srs) - 1) * series_cc
            jx = last_s_x + neck_cc

    return placement

# ── Build full layout ───────────────────────────────────────────────
c1 = trace_channel("Net-(J1-In)")
c2 = trace_channel("Net-(J2-In)")
c3 = trace_channel("Net-(J4-In)")

# Channel 1: J1 -> J6 (Power board channel 1, neck = 0.8 mm)
# Channel 2: J2 -> J7 (Power board channel 2, neck = 1.2 mm)
# Channel 3: J4 -> J5 (Tune board channel, 0603, neck = 0.8 mm)
X_START = 30.0
Y_CH1   = 20.0
Y_CH2   = 38.0
Y_CH3   = 56.0

NECK_CH1 = 0.8  # mm pad-edge to pad-edge
NECK_CH2 = 1.2  # mm pad-edge to pad-edge
NECK_CH3 = 0.8  # mm pad-edge to pad-edge

p1 = layout_channel(c1, X_START, Y_CH1, neck_gap=NECK_CH1, is_0603=False)
p2 = layout_channel(c2, X_START, Y_CH2, neck_gap=NECK_CH2, is_0603=False)
p3 = layout_channel(c3, X_START, Y_CH3, neck_gap=NECK_CH3, is_0603=True)

all_placement = {**p1, **p2, **p3}

print(f"Total resistors placed: {len(all_placement)} / {len(resistors)}")
print(f"Channel 1 (J1 -> J6): {len(p1)} resistors at Y={Y_CH1} mm, neck = {NECK_CH1} mm")
print(f"Channel 2 (J2 -> J7): {len(p2)} resistors at Y={Y_CH2} mm, neck = {NECK_CH2} mm")
print(f"Channel 3 (J4 -> J5): {len(p3)} resistors at Y={Y_CH3} mm, neck = {NECK_CH3} mm")
print(f"Series gap: {SERIES_GAP} mm (touching pad to pad)")

if "--dry-run" in sys.argv:
    print("\nPositions preview:")
    for r in sorted(all_placement.keys(), key=lambda x: int(x[1:])):
        x, y, rot = all_placement[r]
        print(f"  {r:6s}  x={x:8.3f}  y={y:8.3f}  rot={rot:6.1f}")
    sys.exit(0)

# ── Apply placement via kh place ────────────────────────────────────
json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pi_all_placement.json")
with open(json_path, "w") as f:
    json.dump({k: [x, y, rot] for k, (x, y, rot) in all_placement.items()}, f, indent=2)

pcb_dir = os.path.dirname(os.path.abspath(__file__))
cmd = ["kh", "place", "--pcb", pcb_dir, "--json", json_path]
print(f"\nApplying to board...")
result = subprocess.run(cmd, capture_output=True, text=True)
if result.stdout:
    try:
        out = json.loads(result.stdout)
        print(f"Successfully moved {len(out.get('moved', {}))} footprints on {out.get('board')}")
    except:
        print(result.stdout[:500])

if result.returncode:
    print(result.stderr, file=sys.stderr)
    sys.exit(f"kh place failed with exit code {result.returncode}")

if os.path.exists(json_path):
    os.remove(json_path)

print("\nDone! In KiCad PCB Editor, press File -> Revert to reload the updated layout.")
