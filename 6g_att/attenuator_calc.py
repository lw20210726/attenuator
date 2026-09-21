#!/usr/bin/env python3
"""
Two-board RF attenuator toolkit: PA (5-10 W CW) -> POWER board (20 dB fixed) -> TUNE board (dual Pi) -> spectrum analyser.
50 ohm, DC-6 GHz, JLCPCB 2-layer 0.8 mm FR4.

TUNE board positions (matches the schematic):
    IN --+--[Rs1]--+--[Rs2]--+-- OUT
         |         |         |
      R1a||R1b  R2a||R2b  R3a||R3b      (each shunt node: main "a" + trim "b" to GND)
    Section 1 = R1, Rs1, R2      Section 2 = R2, Rs2, R3      (R2 is shared by both)

Commands
  design <dB>                pick TUNE board parts for a target attenuation
  eval   <positions...>      predict dB of what is actually soldered on the TUNE board
  table                      TUNE board quick reference, 1-30 dB
  powerboard                 POWER board parts list and watts per resistor
  chain                      level at the spectrum analyser for a given PA power
  correct <s2p> [<s2p>...]   cascade measured Touchstone files -> SA amplitude-correction CSV

Add --pin <power> to design / eval / powerboard to see watts in every resistor.
Power accepts dBm or W: 20dBm, 0.1W, 10W, 40 (plain number = dBm).

Examples
  python attenuator_calc.py design 10 --pin 21dBm
  python attenuator_calc.py design 10 --offset 0.35           # last board measured 0.35 dB high
  python attenuator_calc.py eval rs1=71.5 r1a=97.6 r1b=6.98k r2a=97.6 r2b=6.98k rs2=0 r3a=dnp r3b=dnp --pin 20dBm
  python attenuator_calc.py powerboard --pin 10W
  python attenuator_calc.py chain --pa 10W --tune 10
  python attenuator_calc.py correct power_A.s2p tune_10dB.s2p --out sa_correction.csv
"""

import argparse
import math
import sys

Z0 = 50.0
SINGLE_SECTION_MAX_DB = 15.0   # TUNE board: above this, split across both sections
TRIM_WORTH_IT = 0.003          # add a trim part only if main part alone is >0.3% off
MAX_TRIM_OHMS = 100e3
TUNE_PART_LIMIT_W = 0.15       # 0603 rated 1/4 W (e.g. ROHM ESR03), run at <=60 %
POWER_PART_LIMIT_W = 0.30      # 0805 rated >=0.4 W on a heatsink, run at <=75 %
SA_MAX_DBM = 20.0
SA_COMFORT_DBM = 10.0

E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
E96 = [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
       1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
       1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
       2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
       3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
       4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
       5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
       7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76]
SERIES = {"E24": E24, "E96": E96}

# POWER board, one channel (channels A and B are identical except the neck length).
# Cells (dB): 0.5 x6, 1.5, 2.5, 3, 5, 5 = 20 dB.  Only common E12 / current-sense values.
# kind, label, ideal ohms, parts, arrangement
#   series: parts soldered end to end along the line (values add)
#   shunt : the listed parts are stacked in series on EACH side of the line, both sides to ground
POWER_BOARD = [
    ("shunt",  "N0",  1737.66, [3300],               "1 per side"),
    ("series", "S0",     2.88, [1.0, 1.0, 1.0],      "3 in a chain"),
    ("shunt",  "N1",   868.83, [1800],               "1 per side"),
    ("series", "S1",     2.88, [1.0, 1.0, 1.0],      "3 in a chain"),
    ("shunt",  "N2",   868.83, [1800],               "1 per side"),
    ("series", "S2",     2.88, [1.0, 1.0, 1.0],      "3 in a chain"),
    ("shunt",  "N3",   868.83, [1800],               "1 per side"),
    ("series", "S3",     2.88, [1.0, 1.0, 1.0],      "3 in a chain"),
    ("shunt",  "N4",   868.83, [1800],               "1 per side"),
    ("series", "S4",     2.88, [1.0, 1.0, 1.0],      "3 in a chain"),
    ("shunt",  "N5",   868.83, [1800],               "1 per side"),
    ("series", "S5",     2.88, [1.0, 1.0, 1.0],      "3 in a chain"),
    ("shunt",  "N6",   435.13, [180, 680],           "2 stacked per side"),
    ("series", "S6",     8.68, [2.2, 2.2, 2.2, 2.2], "4 in a chain"),
    ("shunt",  "N7",   218.28, [220, 220],           "2 stacked per side"),
    ("series", "S7",    14.59, [3.3, 3.3, 3.3, 3.3, 1.0], "5 in a chain"),
    ("shunt",  "N8",   159.27, [150, 150],           "2 stacked per side"),
    ("series", "S8",    17.61, [3.3, 4.7, 4.7, 4.7], "4 in a chain"),
    ("shunt",  "N9",   110.83, [220],                "1 per side"),
    ("series", "S9",    30.40, [15, 15],             "2 in a chain"),
    ("shunt",  "N10",   89.24, [180],                "1 per side"),
    ("series", "S10",   30.40, [15, 15],             "2 in a chain"),
    ("shunt",  "N11",  178.49, [180, 180],           "2 stacked per side"),
]


# ---------------------------------------------------------------- helpers
def series_values(name, lo=1.0, hi=1e6):
    vals = []
    for dec in range(0, 7):
        for m in SERIES[name]:
            v = round(m * 10 ** dec, 6)
            if lo <= v <= hi:
                vals.append(v)
    return sorted(set(vals))


def fmt_r(r):
    if r is None:
        return "DNP"
    if r == 0:
        return "0R"
    if r >= 1e6:
        return f"{r/1e6:.3g}M"
    if r >= 1e3:
        return f"{r/1e3:.3g}k"
    return f"{r:.3g}R"


def parse_r(s):
    s = s.strip().lower()
    if s in ("dnp", "nc", "open", "-", "none"):
        return None
    if s in ("0", "0r", "short"):
        return 0.0
    mult = 1.0
    if s.endswith("r"):
        s = s[:-1]
    if s.endswith("k"):
        mult, s = 1e3, s[:-1]
    elif s.endswith("m"):
        mult, s = 1e6, s[:-1]
    return float(s) * mult


def parse_power(s):
    """'20dBm', '0.1W', '100mW', '10W', or plain number (dBm) -> watts."""
    t = str(s).strip().lower().replace(" ", "")
    if t.endswith("dbm"):
        return 1e-3 * 10 ** (float(t[:-3]) / 10)
    if t.endswith("mw"):
        return float(t[:-2]) * 1e-3
    if t.endswith("w"):
        return float(t[:-1])
    return 1e-3 * 10 ** (float(t) / 10)


def dbm(w):
    return 10 * math.log10(w / 1e-3)


def fmt_w(w):
    return f"{w*1e3:.0f} mW" if w < 1 else f"{w:.2f} W"


def par(*rs):
    """Parallel combination; None = not fitted."""
    if any(r == 0 for r in rs if r is not None):
        return 0.0
    g = sum(1.0 / r for r in rs if r is not None)
    return None if g == 0 else 1.0 / g


def pi_ideal(db):
    """Ideal matched Pi values for one section: (series, shunt)."""
    k = 10 ** (db / 20.0)
    return Z0 * (k * k - 1) / (2 * k), Z0 * (k + 1) / (k - 1)


# ---------------------------------------------------------------- network model
def ladder(elements):
    """
    elements: list of ('sh', R or None) / ('se', R), input first, 50 ohm ports.
    Returns dict with attenuation, return losses, Zin and per-element power fraction
    (fraction of the power delivered into the input).
    """
    def mul(a, b):
        return [[a[0][0]*b[0][0] + a[0][1]*b[1][0], a[0][0]*b[0][1] + a[0][1]*b[1][1]],
                [a[1][0]*b[0][0] + a[1][1]*b[1][0], a[1][0]*b[0][1] + a[1][1]*b[1][1]]]
    m = [[1, 0], [0, 1]]
    for kind, r in elements:
        blk = [[1, 0], [0 if r is None else 1.0 / r, 1]] if kind == "sh" else [[1, r], [0, 1]]
        m = mul(m, blk)
    (a, b), (c, d) = m
    den = a + b / Z0 + c * Z0 + d
    s21 = 2.0 / den
    s11 = (a + b / Z0 - c * Z0 - d) / den
    s22 = (-a + b / Z0 - c * Z0 + d) / den
    zin = (a * Z0 + b) / (c * Z0 + d)

    # backward sweep from a 1 V output into 50 ohm to get every element's dissipation
    v, i = 1.0, 1.0 / Z0
    p = []
    for kind, r in reversed(elements):
        if kind == "sh":
            pw = 0.0 if r is None else v * v / r
            if r is not None:
                i += v / r
        else:
            pw = i * i * r
            v += i * r
        p.append(pw)
    p.reverse()
    pin = v * i
    rl = lambda s: float("inf") if abs(s) < 1e-12 else -20 * math.log10(abs(s))
    return {"att": -20 * math.log10(abs(s21)), "rl_in": rl(s11), "rl_out": rl(s22),
            "zin": zin, "frac": [x / pin for x in p], "vin": v / math.sqrt(pin)}


# ---------------------------------------------------------------- TUNE board part picking
def pick_series(ideal, vals):
    if ideal < 0.05:
        return 0.0
    return min(vals, key=lambda v: abs(v - ideal))


def pick_shunt(ideal, vals):
    """Return (main, trim_or_None). Main is picked at/above ideal, trim pulls it down in parallel."""
    if ideal is None:
        return None, None
    nearest = min(vals, key=lambda v: abs(v - ideal))
    if abs(nearest - ideal) / ideal <= TRIM_WORTH_IT:
        return nearest, None
    above = [v for v in vals if v >= ideal]
    if not above:
        return nearest, None
    best = (abs(nearest - ideal), nearest, None)
    for main in above[:3]:
        need = 1.0 / (1.0 / ideal - 1.0 / main) if main > ideal else None
        if need is None or need > MAX_TRIM_OHMS:
            continue
        for trim in sorted(vals, key=lambda v: abs(v - need))[:2]:
            err = abs(par(main, trim) - ideal)
            if err < best[0]:
                best = (err, main, trim)
    return best[1], best[2]


def plan_sections(total_db, split):
    if total_db <= 0:
        return 0.0, 0.0
    if split == "one" or (split == "auto" and total_db <= SINGLE_SECTION_MAX_DB):
        return total_db, 0.0
    return total_db / 2.0, total_db / 2.0


def ideal_network(db1, db2):
    rs1, sh1 = pi_ideal(db1) if db1 > 0 else (0.0, None)
    rs2, sh2 = pi_ideal(db2) if db2 > 0 else (0.0, None)
    return {"rs1": rs1, "r1": sh1, "r2": par(sh1, sh2), "rs2": rs2, "r3": sh2}


def design(target_db, eseries, split, offset):
    vals = series_values(eseries)
    aim = target_db - offset
    db1, db2 = plan_sections(aim, split)
    ideal = ideal_network(db1, db2)
    parts = {"rs1": pick_series(ideal["rs1"], vals), "rs2": pick_series(ideal["rs2"], vals)}
    for n in ("r1", "r2", "r3"):
        parts[n + "a"], parts[n + "b"] = pick_shunt(ideal[n], vals)
    return aim, (db1, db2), ideal, parts


def tune_elements(parts):
    return [("sh", par(parts["r1a"], parts["r1b"])), ("se", parts["rs1"]),
            ("sh", par(parts["r2a"], parts["r2b"])), ("se", parts["rs2"]),
            ("sh", par(parts["r3a"], parts["r3b"]))]


def print_tune_power(parts, res, pin):
    print(f"\nPower check at {dbm(pin):.1f} dBm ({fmt_w(pin)}) into the TUNE board, limit {fmt_w(TUNE_PART_LIMIT_W)} per part:")
    frac = res["frac"]
    rows = [("R1a", "r1a", 0), ("R1b", "r1b", 0), ("Rs1", "rs1", 1), ("R2a", "r2a", 2),
            ("R2b", "r2b", 2), ("Rs2", "rs2", 3), ("R3a", "r3a", 4), ("R3b", "r3b", 4)]
    worst = 0
    for name, key, idx in rows:
        r = parts[key]
        if r is None or r == 0:
            continue
        if key.startswith("rs"):
            w = frac[idx] * pin
        else:
            other = parts[key[:-1] + ("b" if key.endswith("a") else "a")]
            w = frac[idx] * pin * (par(r, other) / r)
        worst = max(worst, w)
        flag = "  <-- OVER LIMIT" if w > TUNE_PART_LIMIT_W else ""
        print(f"  {name:<4} {fmt_r(r):>7}  {fmt_w(w):>8}{flag}")
    pout = pin * 10 ** (-res["att"] / 10)
    print(f"  Output: {dbm(pout):.1f} dBm")
    if worst > TUNE_PART_LIMIT_W:
        print("  Too hot: add more attenuation on the POWER side, or use a 2-section split (--split even).")


def print_design(target, eseries, split, offset, pin):
    aim, (d1, d2), ideal, parts = design(target, eseries, split, offset)
    res = ladder(tune_elements(parts))
    print(f"\nTUNE board, target {target:.2f} dB" + (f"  (aiming {aim:.2f} dB after {offset:+.2f} dB offset)" if offset else ""))
    print(f"Section 1 = {d1:.2f} dB, Section 2 = " + (f"{d2:.2f} dB" if d2 else "bypassed (Rs2 = 0R jumper, R3 not fitted)"))
    print(f"Resistor series: {eseries}\n")
    print(f"{'Position':<10}{'Ideal':>10}{'Main (a)':>11}{'Trim (b)':>11}{'Effective':>12}{'Error':>9}")
    print("-" * 63)
    el = tune_elements(parts)
    rows = [("Rs1", ideal["rs1"], parts["rs1"], None, el[1][1]),
            ("R1 shunt", ideal["r1"], parts["r1a"], parts["r1b"], el[0][1]),
            ("R2 shunt", ideal["r2"], parts["r2a"], parts["r2b"], el[2][1]),
            ("Rs2", ideal["rs2"], parts["rs2"], None, el[3][1]),
            ("R3 shunt", ideal["r3"], parts["r3a"], parts["r3b"], el[4][1])]
    for name, idl, main, trim, e in rows:
        trim_s = "-" if name.startswith("Rs") else fmt_r(trim)
        err = "" if not idl or e is None else f"{100*(e-idl)/idl:+.2f}%"
        print(f"{name:<10}{fmt_r(idl):>10}{fmt_r(main):>11}{trim_s:>11}{fmt_r(e):>12}{err:>9}")
    print("-" * 63)
    print(f"Predicted attenuation : {res['att']:.3f} dB   (error {res['att'] - aim:+.3f} dB vs aim)")
    print(f"Predicted return loss : in {res['rl_in']:.1f} dB, out {res['rl_out']:.1f} dB   (Zin = {res['zin']:.2f} ohm)")
    if pin:
        print_tune_power(parts, res, pin)
    print("\nResistive (DC) prediction. Measure on the VNA, then re-run with --offset <measured - target>.\n")


def print_eval(assignments, pin):
    parts = {k: None for k in ("rs1", "rs2", "r1a", "r1b", "r2a", "r2b", "r3a", "r3b")}
    parts["rs1"] = parts["rs2"] = 0.0
    for a in assignments:
        if "=" not in a:
            sys.exit(f"Bad argument '{a}', use position=value (e.g. r1a=96.2)")
        k, v = a.split("=", 1)
        k = k.strip().lower()
        if k not in parts:
            sys.exit(f"Unknown position '{k}'. Valid: {', '.join(parts)}")
        parts[k] = parse_r(v)
    if parts["rs1"] is None or parts["rs2"] is None:
        sys.exit("Series positions cannot be open (DNP); use 0 for a jumper.")
    el = tune_elements(parts)
    res = ladder(el)
    print(f"\nFitted: Rs1={fmt_r(el[1][1])}  R1={fmt_r(el[0][1])}  R2={fmt_r(el[2][1])}  "
          f"Rs2={fmt_r(el[3][1])}  R3={fmt_r(el[4][1])}")
    print(f"Predicted attenuation : {res['att']:.3f} dB")
    print(f"Predicted return loss : in {res['rl_in']:.1f} dB, out {res['rl_out']:.1f} dB   (Zin = {res['zin']:.2f} ohm)")
    if pin:
        print_tune_power(parts, res, pin)
    print()


def print_table(eseries):
    print(f"\n{'Target':>7} {'Rs1':>7} {'R1a/R1b':>14} {'R2a/R2b':>14} {'Rs2':>7} {'R3a/R3b':>14} {'Pred dB':>8} {'RL in':>7}")
    print("-" * 84)
    pair = lambda a, b: "DNP" if a is None else (fmt_r(a) if b is None else f"{fmt_r(a)}/{fmt_r(b)}")
    for db in (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 20, 25, 30):
        _, _, _, p = design(db, eseries, "auto", 0.0)
        res = ladder(tune_elements(p))
        print(f"{db:>6}  {fmt_r(p['rs1']):>7} {pair(p['r1a'], p['r1b']):>14} {pair(p['r2a'], p['r2b']):>14} "
              f"{fmt_r(p['rs2']):>7} {pair(p['r3a'], p['r3b']):>14} {res['att']:>8.3f} {res['rl_in']:>7.1f}")
    print()


# ---------------------------------------------------------------- POWER board
def power_elements():
    el = []
    for kind, _, _, vals, _ in POWER_BOARD:
        el.append(("se", sum(vals)) if kind == "series" else ("sh", sum(vals) / 2))
    return el


def power_part_watts(pin):
    """[(label, kind, [(value, watts), ...])] for every position at input power pin."""
    res = ladder(power_elements())
    out = []
    for (kind, lab, _, vals, _), f in zip(POWER_BOARD, res["frac"]):
        tot = f * pin
        if kind == "series":
            ws = [(v, tot * v / sum(vals)) for v in vals]
        else:
            ws = [(v, tot / 2 * v / sum(vals)) for v in vals]   # half per side, split by value
        out.append((lab, kind, ws))
    return res, out


def n_parts(kind, vals):
    return len(vals) * (2 if kind == "shunt" else 1)


def print_powerboard(pin):
    res, watts = power_part_watts(pin or 1.0)
    total = sum(n_parts(k, v) for k, _, _, v, _ in POWER_BOARD)
    print(f"\nPOWER board (one channel), {total} x 0805 resistors rated >= 0.4 W")
    print(f"Predicted attenuation {res['att']:.3f} dB, return loss {res['rl_in']:.1f} dB (resistive)\n")
    hdr = f"{'Pos':<5}{'Type':<8}{'Ideal':>9}{'Fitted':>9}  {'Parts':<38}"
    if pin:
        hdr += f"{'Hottest part':>13}"
    print(hdr)
    print("-" * len(hdr))
    worst = 0
    for (kind, lab, ideal, vals, arr), (_, eff), (_, _, ws) in zip(POWER_BOARD, power_elements(), watts):
        desc = " + ".join(fmt_r(v) for v in vals) + (" each side" if kind == "shunt" else "")
        line = f"{lab:<5}{kind:<8}{fmt_r(ideal):>9}{fmt_r(eff):>9}  {desc:<38}"
        if pin:
            hot = max(w for _, w in ws)
            worst = max(worst, hot)
            line += f"{fmt_w(hot):>13}" + ("  <-- OVER" if hot > POWER_PART_LIMIT_W else "")
        print(line)
    bom = {}
    for kind, _, _, vals, _ in POWER_BOARD:
        for v in vals:
            bom[v] = bom.get(v, 0) + (2 if kind == "shunt" else 1)
    print("\nBOM per channel: " + ", ".join(f"{fmt_r(v)} x{n}" for v, n in sorted(bom.items())))
    if pin:
        pout = pin * 10 ** (-res["att"] / 10)
        print(f"\nInput {dbm(pin):.1f} dBm ({fmt_w(pin)}) -> output {dbm(pout):.1f} dBm. "
              f"Board dissipates {fmt_w(pin - pout)}. Hottest part {fmt_w(worst)} "
              f"(limit {fmt_w(POWER_PART_LIMIT_W)}).")
        if worst > POWER_PART_LIMIT_W:
            print(f"OVER LIMIT: max continuous input for this board is about "
                  f"{pin * POWER_PART_LIMIT_W / worst:.1f} W.")
    print()


def print_chain(pa, power_db, tune_db):
    p_tune_in = pa * 10 ** (-power_db / 10)
    p_sa = p_tune_in * 10 ** (-tune_db / 10)
    print(f"\nPA {dbm(pa):.1f} dBm ({fmt_w(pa)})")
    print(f"  -> POWER board -{power_db:.2f} dB -> {dbm(p_tune_in):.1f} dBm ({fmt_w(p_tune_in)}) into TUNE board")
    print(f"  -> TUNE board  -{tune_db:.2f} dB -> {dbm(p_sa):.1f} dBm at the spectrum analyser")
    _, watts = power_part_watts(1.0)
    limit_w = POWER_PART_LIMIT_W / max(w for _, _, ws in watts for _, w in ws)
    if pa > limit_w:
        print(f"  !! PA power exceeds the POWER board limit (~{limit_w:.1f} W continuous).")
    if dbm(p_sa) > SA_MAX_DBM:
        need = dbm(p_sa) - SA_COMFORT_DBM
        print(f"  !! Above your SA maximum ({SA_MAX_DBM:.0f} dBm). Add about {need:.0f} dB on the TUNE board.")
    elif dbm(p_sa) > SA_COMFORT_DBM:
        print(f"  Within SA maximum, but set the SA input attenuator to >= {math.ceil(dbm(p_sa) - 0):.0f} dB "
              f"or add TUNE attenuation to stay near {SA_COMFORT_DBM:.0f} dBm.")
    else:
        print("  OK for the SA.")
    print()


# ---------------------------------------------------------------- Touchstone cascade -> SA correction
def read_s2p(path):
    fmul = {"hz": 1, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
    unit, fmt, data = "ghz", "ma", []
    with open(path) as fh:
        nums = []
        for raw in fh:
            s = raw.split("!")[0].strip()
            if not s:
                continue
            if s.startswith("#"):
                tok = s[1:].lower().split()
                for t in tok:
                    if t in fmul:
                        unit = t
                    elif t in ("ma", "db", "ri"):
                        fmt = t
                continue
            nums += [float(x) for x in s.split()]
            while len(nums) >= 9:
                data.append(nums[:9])
                nums = nums[9:]
    out = []
    for row in data:
        f = row[0] * fmul[unit]
        s = []
        for a, b in zip(row[1::2], row[2::2]):
            if fmt == "ri":
                s.append(complex(a, b))
            else:
                mag = 10 ** (a / 20) if fmt == "db" else a
                s.append(mag * complex(math.cos(math.radians(b)), math.sin(math.radians(b))))
        s11, s21, s12, s22 = s
        out.append((f, [[s11, s12], [s21, s22]]))
    return out


def s_to_t(s):
    (s11, s12), (s21, s22) = s
    return [[-(s11 * s22 - s12 * s21) / s21, s11 / s21], [-s22 / s21, 1 / s21]]


def t_to_s(t):
    (t11, t12), (t21, t22) = t
    return [[t12 / t22, (t11 * t22 - t12 * t21) / t22], [1 / t22, -t21 / t22]]


def interp_s(data, f):
    if f <= data[0][0]:
        return data[0][1]
    for (f0, s0), (f1, s1) in zip(data, data[1:]):
        if f0 <= f <= f1:
            x = (f - f0) / (f1 - f0)
            return [[s0[i][j] + x * (s1[i][j] - s0[i][j]) for j in range(2)] for i in range(2)]
    return data[-1][1]


def print_correct(files, out):
    sets = [read_s2p(p) for p in files]
    freqs = [f for f, _ in sets[0]]
    rows = []
    for f in freqs:
        t = [[1, 0], [0, 1]]
        for d in sets:
            tt = s_to_t(interp_s(d, f))
            t = [[t[0][0]*tt[0][0] + t[0][1]*tt[1][0], t[0][0]*tt[0][1] + t[0][1]*tt[1][1]],
                 [t[1][0]*tt[0][0] + t[1][1]*tt[1][0], t[1][0]*tt[0][1] + t[1][1]*tt[1][1]]]
        s = t_to_s(t)
        rows.append((f, -20 * math.log10(abs(s[1][0])), 20 * math.log10(max(abs(s[0][0]), 1e-9))))
    with open(out, "w") as fh:
        fh.write("frequency_hz,offset_db,input_return_db\n")
        for f, a, r in rows:
            fh.write(f"{f:.0f},{a:.3f},{r:.2f}\n")
    atts = [a for _, a, _ in rows]
    print(f"\nCascaded {len(files)} file(s), {len(rows)} points, {freqs[0]/1e6:.0f} MHz - {freqs[-1]/1e9:.2f} GHz")
    print(f"Total attenuation {min(atts):.2f} .. {max(atts):.2f} dB, worst input match {max(r for *_, r in rows):.1f} dB")
    print(f"Wrote {out}: enter offset_db as the SA's external gain/amplitude correction (add it to readings).\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("design", help="pick TUNE board parts for a target dB")
    d.add_argument("db", type=float)
    d.add_argument("--series", choices=SERIES, default="E96")
    d.add_argument("--split", choices=["auto", "one", "even"], default="auto",
                   help=f"auto: one section up to {SINGLE_SECTION_MAX_DB:g} dB, then split evenly")
    d.add_argument("--offset", type=float, default=0.0, help="measured minus target from a previous board, in dB")
    d.add_argument("--pin", help="input power into the TUNE board, e.g. 20dBm")

    e = sub.add_parser("eval", help="predict dB of fitted TUNE board parts")
    e.add_argument("parts", nargs="+", help="e.g. rs1=71.5 r1a=100 r1b=2.49k ... (dnp = empty, 0 = jumper)")
    e.add_argument("--pin", help="input power into the TUNE board, e.g. 20dBm")

    t = sub.add_parser("table", help="TUNE board reference table")
    t.add_argument("--series", choices=SERIES, default="E96")

    p = sub.add_parser("powerboard", help="POWER board parts and dissipation")
    p.add_argument("--pin", help="PA power, e.g. 10W")

    c = sub.add_parser("chain", help="level at the SA")
    c.add_argument("--pa", required=True, help="PA output, e.g. 10W or 40dBm")
    c.add_argument("--power-db", type=float, default=20.0, help="POWER board attenuation (measured value if you have it)")
    c.add_argument("--tune", type=float, default=10.0, help="TUNE board attenuation in dB")

    r = sub.add_parser("correct", help="cascade .s2p files into an SA correction table")
    r.add_argument("files", nargs="+")
    r.add_argument("--out", default="sa_correction.csv")

    a = ap.parse_args()
    pin = parse_power(a.pin) if getattr(a, "pin", None) else None
    if a.cmd == "design":
        print_design(a.db, a.series, a.split, a.offset, pin)
    elif a.cmd == "eval":
        print_eval(a.parts, pin)
    elif a.cmd == "table":
        print_table(a.series)
    elif a.cmd == "powerboard":
        print_powerboard(pin)
    elif a.cmd == "chain":
        print_chain(parse_power(a.pa), a.power_db, a.tune)
    else:
        print_correct(a.files, a.out)


if __name__ == "__main__":
    main()
