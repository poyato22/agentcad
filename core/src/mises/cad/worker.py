"""Sandboxed build worker. Self-contained: stdlib + build123d only (no mises imports).

Run as: python -I worker.py <part_dir> <vars.json>
Loads <part_dir>/part.py, calls build(vars), writes model.step/.stl/.glb and metrics.json
into <part_dir>. Outputs are written to temp names and renamed only after all succeed.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path


def _hole_groups(shape) -> list[dict]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED

    groups: dict[tuple, dict] = {}
    for face in shape.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() != GeomAbs_Cylinder:
            continue
        cyl = surf.Cylinder()
        ax = cyl.Axis()
        d, loc, r = ax.Direction(), ax.Location(), cyl.Radius()
        direction = [d.X(), d.Y(), d.Z()]
        origin = [loc.X(), loc.Y(), loc.Z()]
        # Surface normal of a cylinder points away from its axis; a reversed face
        # (or left-handed cylinder) flips it. Material-outward normal toward the axis = hole.
        flips = (face.wrapped.Orientation() == TopAbs_REVERSED) ^ (not cyl.Direct())
        if not flips:
            continue
        # Canonical axis direction (first non-zero component positive).
        for c in direction:
            if abs(c) > 1e-9:
                if c < 0:
                    direction = [-x for x in direction]
                break
        # Extent along the axis from the face's vertices-bbox.
        bb = face.bounding_box()
        corners = [(x, y, z) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y)
                   for z in (bb.min.Z, bb.max.Z)]
        ts = [sum((p[i] - origin[i]) * direction[i] for i in range(3)) for p in corners]
        # Bbox corners overshoot for curved faces; tighten with the face's actual vertices
        # plus the bbox of the surface extents in the axial direction (u-v bounds).
        u0, u1, v0, v1 = surf.FirstUParameter(), surf.LastUParameter(), \
            surf.FirstVParameter(), surf.LastVParameter()
        t0, t1 = (v0, v1) if v1 - v0 < 1e8 else (min(ts), max(ts))
        # origin of gp_Cylinder is at v=0; project to canonical direction
        sign = 1.0 if (cyl.Axis().Direction().X() * direction[0]
                       + cyl.Axis().Direction().Y() * direction[1]
                       + cyl.Axis().Direction().Z() * direction[2]) > 0 else -1.0
        a0, a1 = sorted((sign * t0, sign * t1))
        # Point on axis nearest world origin-independent reference: shift origin to v=0.
        perp = [origin[i] - direction[i] * sum(origin[j] * direction[j] for j in range(3))
                for i in range(3)]
        key = (tuple(round(x, 4) for x in direction), round(2 * r, 4),
               tuple(round(x, 3) for x in perp), round(a0 + sum(origin[j] * direction[j] for j in range(3)), 2),
               round(a1 + sum(origin[j] * direction[j] for j in range(3)), 2))
        if key in groups:
            continue
        base = sum(origin[j] * direction[j] for j in range(3))
        mid = (a0 + a1) / 2 + base
        center = [perp[i] + direction[i] * mid for i in range(3)]
        groups[key] = {"diameter_mm": 2 * r, "axis": direction, "center": center,
                       "depth_mm": a1 - a0}
    return list(groups.values())


def _metrics(shape, density: float) -> dict:
    bb = shape.bounding_box()
    vol = float(shape.volume)
    com = shape.center()
    return {
        "valid": bool(shape.is_valid),
        "volume_mm3": vol,
        "area_mm2": float(shape.area),
        "mass_g": vol / 1000.0 * density,
        "bbox": {"min": [bb.min.X, bb.min.Y, bb.min.Z], "max": [bb.max.X, bb.max.Y, bb.max.Z],
                 "size": [bb.size.X, bb.size.Y, bb.size.Z]},
        "center_of_mass": [com.X, com.Y, com.Z],
        "faces": len(shape.faces()),
        "edges": len(shape.edges()),
        "holes": _hole_groups(shape),
        "solids": len(shape.solids()),
    }


def main(argv: list[str]) -> int:
    part_dir = Path(argv[1]).resolve()
    vars_path = Path(argv[2])
    density = float(os.environ.get("MISES_DENSITY", "1.24"))
    # Read the nonce before the part script runs and keep it out of the environment.
    nonce = os.environ.pop("MISES_NONCE", "")
    if not nonce:
        raise RuntimeError("missing build nonce")
    variables = json.loads(vars_path.read_text())

    spec = importlib.util.spec_from_file_location("mises_part", part_dir / "part.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, Exception):
            raise
        raise RuntimeError(f"part.py raised {type(e).__name__}: {e}") from None
    merged = dict(getattr(mod, "VARS", {}) or {})
    merged.update(variables)
    if hasattr(mod, "VARS"):  # scripts may read module-level VARS inside build()
        mod.VARS.clear()
        mod.VARS.update(merged)
    try:
        shape = mod.build(dict(merged))
    except BaseException as e:  # noqa: BLE001  (SystemExit/KeyboardInterrupt from user code)
        if isinstance(e, Exception):
            raise
        raise RuntimeError(f"build() raised {type(e).__name__}: {e}") from None

    import build123d as b3
    if not hasattr(shape, "volume"):
        raise TypeError("build() must return a build123d Part or Compound")
    shape = shape.part if hasattr(shape, "part") else shape
    metrics = _metrics(shape, density)
    if not (math.isfinite(metrics["volume_mm3"]) and metrics["volume_mm3"] > 0):
        raise ValueError("build() produced an empty or zero-volume shape")

    tmp = part_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    outs = {"model.step": b3.export_step, "model.stl": b3.export_stl, "model.glb": b3.export_gltf}
    for name, fn in outs.items():
        fn(shape, str(tmp / ("new_" + name)))
    (tmp / "new_metrics.json").write_text(json.dumps(metrics))
    for name in list(outs) + ["metrics.json"]:
        os.replace(tmp / ("new_" + name), part_dir / name)
    # Very last step: the result marker the runner trusts (carries the nonce).
    (tmp / "new_result.json").write_text(
        json.dumps({"nonce": nonce, "metrics": metrics, "vars": merged}))
    os.replace(tmp / "new_result.json", part_dir / "result.json")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.stderr.write(f"BUILD_ERROR: {type(e).__name__}: {e}\n")
        sys.exit(1)
