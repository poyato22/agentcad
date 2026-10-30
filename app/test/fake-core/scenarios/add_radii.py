"""Adds Plan 7 U2a's metrics `radius` to the fake scenarios (python3 add_radii.py): a hole-wall
cylinder face gets its hole's radius (the `holes` row with the same centre, else the one whose
wall area pi d depth matches), a circle edge length / (2 pi). Real cores write the exact value;
a cylinder with no hole to read it from (a boss) is left without, as an older core's would be."""
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fix(node) -> int:
    n = 0
    if isinstance(node, dict):
        holes = node.get("holes") or []
        for f in node.get("face_list") or []:
            if isinstance(f, dict) and f.get("type") == "cylinder" and "radius" not in f:
                h = next((h for h in holes if math.dist(h["center"], f["center"]) < 1e-6), None) or \
                    next((h for h in holes if abs(math.pi * h["diameter_mm"] * h["depth_mm"] - f["area"]) < 1e-2), None)
                if h:
                    f["radius"] = round(h["diameter_mm"] / 2, 4)
                    n += 1
        for e in node.get("edge_list") or []:
            if isinstance(e, dict) and e.get("type") == "circle" and "radius" not in e and e.get("length"):
                e["radius"] = round(e["length"] / (2 * math.pi), 4)
                n += 1
        for v in node.values():
            n += fix(v)
    elif isinstance(node, list):
        for v in node:
            n += fix(v)
    return n


for p in sorted(HERE.glob("*.json")):
    raw = p.read_text()
    d = json.loads(raw)
    n = fix(d)
    if n:
        indent = 2 if raw.startswith('{\n  "') else 1 if raw.startswith('{\n "') else None
        p.write_text(json.dumps(d, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))
        print(p.name, n)
