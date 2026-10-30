"""Regenerates the G4 fixture GLBs (a vented Pi case with internals) and prints their metrics.

    cd core && uv run python ../app/test/fake-core/scenarios/glb/make_enclosure.py

Parts sit where they are assembled (the GLBs carry the placement), so the build view shows the
case with the board, battery and fan inside, and the lid on top.
"""
import json
import sys
from pathlib import Path

import build123d as b3

OUT = Path(__file__).resolve().parent


def case():
    outer = b3.Box(90, 62, 32)
    inner = b3.Pos(0, 0, 2) * b3.Box(86, 58, 32)
    return outer - inner


def lid():
    return b3.Pos(0, 0, 17) * b3.Box(90, 62, 2)


def board():
    return b3.Pos(-12, 0, -12) * b3.Box(56, 40, 2)


def battery():
    return b3.Pos(-12, 0, -4) * b3.Box(40, 26, 12)


def fan():
    ring = b3.Cylinder(15, 8) - b3.Cylinder(13, 8)
    hub = b3.Cylinder(6, 8)
    return b3.Pos(28, 0, 2) * b3.Rot(0, 90, 0) * (ring + hub)


PARTS = {"pi_case": case, "pi_lid": lid, "pi_board": board, "pi_battery": battery, "pi_fan": fan}


def main() -> None:
    metrics = {}
    for name, make in PARTS.items():
        s = make()
        b3.export_gltf(s, str(OUT / f"{name}.glb"), binary=True)
        bb = s.bounding_box()
        metrics[name] = {
            "valid": s.is_valid, "volume_mm3": s.volume, "area_mm2": s.area,
            "bbox": {"min": [bb.min.X, bb.min.Y, bb.min.Z], "max": [bb.max.X, bb.max.Y, bb.max.Z],
                     "size": [bb.size.X, bb.size.Y, bb.size.Z]},
            "center_of_mass": list(s.center()), "holes": [],
            "faces": len(s.faces()), "edges": len(s.edges()), "solids": len(s.solids()),
        }
    json.dump(metrics, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
