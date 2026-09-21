#!/usr/bin/env python3
"""
Switch the POWER-board resistors in a KiCad schematic from 0603 to 0805,
leaving the tune board (and anything else) untouched.

Which resistors count as "power board" is worked out from the netlist, not from
designators, so it survives re-annotation: every resistor that belongs to a
connector-to-connector path with at least --min-parts resistors (the power
channels have 67, the tune board has 6) is changed.

Usage (close the schematic in KiCad first):
    python3 set_power_footprints.py 6g_att.kicad_sch 6g_att.net
    python3 set_power_footprints.py 6g_att.kicad_sch 6g_att.net --dry-run

Options:
    --footprint   new footprint     (default Resistor_SMD:R_0805_2012Metric)
    --min-parts   path size cut-off (default 20)
    --dry-run     only list what would change

A backup is written next to the schematic as <name>.kicad_sch.bak.
Afterwards: open the schematic, then in the PCB editor run
Tools > Update PCB from Schematic (F8) so the footprints are swapped on the board.
"""

import argparse
import re
import shutil
import sys
from collections import defaultdict

TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


# ------------------------------------------------------------------ netlist
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


def power_refs_from_netlist(path, min_parts):
    tree = parse_sexpr(open(path, encoding="utf-8").read())
    comps = {}
    for c in children(children(tree, "components")[0], "comp"):
        comps[children(c, "ref")[0][1]] = children(c, "value")[0][1]
    pin_net = {}
    gnd = None
    for n in children(children(tree, "nets")[0], "net"):
        name = children(n, "name")[0][1]
        if name.strip("/").upper() == "GND":
            gnd = name
        for node in children(n, "node"):
            pin_net[(children(node, "ref")[0][1], children(node, "pin")[0][1])] = name
    if gnd is None:
        sys.exit("No GND net found in the netlist.")

    resistors = [r for r in comps if re.match(r"R\d+$", r)]
    rnets = {r: {pin_net.get((r, "1")), pin_net.get((r, "2"))} - {None, gnd} for r in resistors}

    # connectivity over signal nets (GND excluded), through resistors
    adj = defaultdict(set)
    for r, ns in rnets.items():
        ns = list(ns)
        for a in ns:
            adj[a].update(ns)
    seen, groups = set(), []
    for start in adj:
        if start in seen:
            continue
        stack, grp = [start], set()
        while stack:
            u = stack.pop()
            if u in grp:
                continue
            grp.add(u)
            stack.extend(adj[u] - grp)
        seen |= grp
        groups.append(grp)

    connectors = {r: pin_net.get((r, "1")) for r in comps if r.startswith("J")}
    selected = set()
    report = []
    for grp in groups:
        members = sorted((r for r, ns in rnets.items() if ns & grp), key=lambda x: int(x[1:]))
        js = sorted((j for j, n in connectors.items() if n in grp), key=lambda x: int(x[1:]))
        is_power = len(members) >= min_parts
        report.append((js, len(members), is_power))
        if is_power:
            selected.update(members)
    return selected, report


# ------------------------------------------------------------------ schematic
def block_end(text, start):
    """Index just past the ')' matching the '(' at text[start], string-aware."""
    depth, i, n = 0, start, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced parentheses in schematic")


def placed_symbols(text):
    """Yield (start, end) of every placed symbol block, i.e. one containing (lib_id ...)."""
    for m in re.finditer(r"\(symbol\b", text):
        s = m.start()
        e = block_end(text, s)
        head = text[s:min(e, s + 400)]
        if "(lib_id" in head:
            yield s, e


REF_RE = re.compile(r'\(property\s+"Reference"\s+"([^"]*)"')
FP_RE = re.compile(r'(\(property\s+"Footprint"\s+)"([^"]*)"')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schematic")
    ap.add_argument("netlist")
    ap.add_argument("--footprint", default="Resistor_SMD:R_0805_2012Metric")
    ap.add_argument("--min-parts", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    refs, report = power_refs_from_netlist(a.netlist, a.min_parts)
    print("Paths found in the netlist:")
    for js, n, is_power in report:
        print(f"  {'-'.join(js) or '(no connector)':<12} {n:3d} resistors  -> {'POWER: change' if is_power else 'keep'}")
    if not refs:
        sys.exit("No power-board resistors found; nothing to do.")

    text = open(a.schematic, encoding="utf-8").read()
    out, last = [], 0
    changed, already, found = [], [], set()
    for s, e in placed_symbols(text):
        block = text[s:e]
        m = REF_RE.search(block)
        if not m or m.group(1) not in refs:
            continue
        ref = m.group(1)
        found.add(ref)
        fm = FP_RE.search(block)
        if not fm:
            print(f"  warning: {ref} has no Footprint property, skipped")
            continue
        if fm.group(2) == a.footprint:
            already.append(ref)
            continue
        block = block[:fm.start()] + fm.group(1) + f'"{a.footprint}"' + block[fm.end():]
        out.append(text[last:s])
        out.append(block)
        last = e
        changed.append((ref, fm.group(2)))
    out.append(text[last:])

    missing = sorted(refs - found, key=lambda x: int(x[1:]))
    print(f"\n{len(changed)} symbols to change, {len(already)} already {a.footprint.split(':')[-1]}.")
    if missing:
        print(f"WARNING: {len(missing)} power resistors from the netlist were not found in the schematic: "
              f"{', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}")
        print("         Is the netlist exported from this same, annotated schematic?")
    olds = sorted({o for _, o in changed})
    if olds:
        print("Old footprints replaced: " + ", ".join(olds))

    if a.dry_run:
        print("\nDry run, schematic not written.")
        return
    if not changed:
        return
    shutil.copyfile(a.schematic, a.schematic + ".bak")
    with open(a.schematic, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(out))
    print(f"\nWrote {a.schematic} (backup: {a.schematic}.bak)")
    print("Next: open it in KiCad, then in the PCB editor run Tools > Update PCB from Schematic (F8).")


if __name__ == "__main__":
    main()
