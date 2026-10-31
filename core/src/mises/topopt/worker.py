"""Topology-optimization worker: python -I -m mises.topopt.worker <job_dir>

Reads <job_dir>/spec.json (resolved by the core: grid size, filter radius, material numbers,
regions, boundary faces) and <job_dir>/model.step (the design-space part); writes
progress.json while it runs (with a live preview.glb of the density every few iterations) and,
at the end: density.npy, part.step, part.stl, result.glb, bc.json (the boundary faces'
triangles, for the verification study) and report.json. It runs sandboxed (writes only inside
job_dir, no network, cannot start processes) with a memory watchdog and a hard timeout; the
parent kills its process group on cancel.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import fe, geometry as geo, simp, voxelize as vx
from ..sim.environments import FACE_PRESETS

PREVIEW_SMOOTH = 3
FINAL_SMOOTH = 10
STL_TOL, STL_ANG = 0.05, 0.3


class TopoptError(Exception):
    pass


def _write(path: Path, obj) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, default=float))
    os.replace(tmp, path)


# ---- faces

def _diag(bbox: dict) -> float:
    return math.dist(bbox["min"], bbox["max"])


def resolve_faces(shape, spec: str, bbox: dict, face_list: list | None,
                  face_tags: dict | None) -> list:
    """build123d faces of a face spec: a preset (every flat face on that side of the bounding
    box), a face id (matched by centre of mass against the part's face_list), or a tag."""
    from build123d import CenterOf
    tol = 1e-4 * _diag(bbox) + 1e-6
    if spec.startswith("tag:"):
        ids = (face_tags or {}).get(spec[4:]) or []
        out = []
        for fid in ids:
            out += [f for f in resolve_faces(shape, fid, bbox, face_list, face_tags)
                    if f not in out]
        if not out:
            raise TopoptError(f"no face tagged {spec[4:]!r}")
        return out
    if spec in FACE_PRESETS:
        axis, side = FACE_PRESETS[spec]
        target = bbox[side][axis]
        out = []
        for f in shape.faces():
            bb = f.bounding_box()
            lo, hi = (bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z)
            if abs(hi[axis] - lo[axis]) <= tol and abs(lo[axis] - target) <= tol:
                out.append(f)
        if not out:
            raise TopoptError(f"no flat face on the {spec} side of the part")
        return out
    row = next((r for r in face_list or [] if r.get("id") == spec), None)
    if row is None:
        raise TopoptError(f"no face {spec} on this part")
    best = min(shape.faces(), key=lambda f: math.dist(
        (lambda c: (c.X, c.Y, c.Z))(f.center(CenterOf.MASS)), row["center"]))
    c = best.center(CenterOf.MASS)
    if math.dist((c.X, c.Y, c.Z), row["center"]) > max(1e-2 * _diag(bbox), 1e-3):
        raise TopoptError(f"face {spec} is not on the part's current geometry")
    return [best]


def face_tris(faces, tol: float) -> np.ndarray:
    return np.concatenate([vx.tris_of(f, tol) for f in faces]) if faces else np.zeros((0, 3, 3))


# ---- the problem

def _region_mask(r: dict, centres: np.ndarray, grid: vx.Grid, job: Path, shape, bbox,
                 spec: dict, tol: float) -> np.ndarray:
    k = r["kind"]
    if k == "box":
        return vx.box_mask(centres, r["box"])
    if k == "cylinder":
        return vx.cylinder_mask(centres, r["cylinder"])
    if k == "part":
        from build123d import import_step
        other = import_step(str(job / r["step"]))
        return vx.solid_mask(vx.tris_of(other, tol), grid)
    if k == "faces":
        faces = [f for fs in r["faces"]
                 for f in resolve_faces(shape, fs, bbox, spec.get("face_list"),
                                        spec.get("face_tags"))]
        fd = vx.FaceDistance(face_tris(faces, tol), max_edge=grid.h)
        return fd(centres, cutoff=r.get("thickness_mm", 2.0)) <= r.get("thickness_mm", 2.0)
    raise TopoptError(f"unknown region kind {k!r}")


def build_problem(spec: dict, job: Path, shape) -> tuple[simp.Problem, vx.Grid, dict, dict]:
    """(problem, grid, boundary triangles for verification, info)."""
    bbox = spec["bbox"]
    h = float(spec["h"])
    grid = vx.plan_grid(bbox["min"], bbox["max"], h)
    tol = max(0.02, 0.05 * h)
    centres = grid.centres()
    if spec["design_space"] == "bbox":
        space = vx.box_mask(centres, [*bbox["min"], *bbox["max"]])
    else:
        space = vx.solid_mask(vx.tris_of(shape, tol), grid)
    if not space.any():
        raise TopoptError("the design space has no elements at this resolution")
    keep_in = np.zeros(grid.ne, bool)
    for r in spec.get("keep") or []:
        keep_in |= _region_mask(r, centres, grid, job, shape, bbox, spec, tol)
    keep_out = np.zeros(grid.ne, bool)
    for r in spec.get("keep_out") or []:
        keep_out |= _region_mask(r, centres, grid, job, shape, bbox, spec, tol)
    keep_out &= space
    nodes = grid.nodes()
    nn = len(nodes)
    # nodes of at least one design-space element (BC nodes must carry material)
    on_space = np.zeros(nn, bool)
    Nx, Ny = grid.n[0] + 1, grid.n[1] + 1
    i, j, k = fe.element_grid(grid.n)
    enodes = np.stack([(i + c[0]) + Nx * ((j + c[1]) + Ny * (k + c[2])) for c in fe.CORNERS], 1)
    on_space[enodes[space & ~keep_out].ravel()] = True
    skin = max(0.75 * h, 1e-6)
    bc: dict = {"fixtures": [], "loads": []}
    fixed = np.zeros((nn, 3), bool)
    for fx in spec["fixtures"]:
        tri = face_tris(resolve_faces(shape, fx["face"], bbox, spec.get("face_list"),
                                      spec.get("face_tags")), tol)
        fd = vx.FaceDistance(tri, max_edge=h)
        sel = on_space & (fd(nodes, cutoff=skin) <= skin)
        if not sel.any():
            raise TopoptError(f"fixture face {fx['face']} touches no element at {h:g} mm")
        fixed[sel] = True
        keep_in |= space & ~keep_out & (fd(centres, cutoff=skin) <= skin)
        bc["fixtures"].append({"face": fx["face"], "tris": tri.tolist()})
    loads = []
    for case in spec["load_cases"]:
        f = np.zeros(3 * nn)
        rows = []
        for ld in case["loads"]:
            tri = face_tris(resolve_faces(shape, ld["face"], bbox, spec.get("face_list"),
                                          spec.get("face_tags")), tol)
            fd = vx.FaceDistance(tri, max_edge=h)
            sel = np.flatnonzero(on_space & (fd(nodes, cutoff=skin) <= skin) & ~fixed.any(1))
            if len(sel) == 0:
                raise TopoptError(f"load face {ld['face']} touches no free element at {h:g} mm")
            d = np.asarray(ld["direction"], float)
            d = d / np.linalg.norm(d)
            for a in range(3):
                f[3 * sel + a] += ld["newtons"] * d[a] / len(sel)
            keep_in |= space & ~keep_out & (fd(centres, cutoff=skin) <= skin)
            rows.append({"face": ld["face"], "newtons": ld["newtons"],
                         "direction": d.tolist(), "tris": tri.tolist()})
        loads.append((f, float(case.get("weight", 1.0))))
        bc["loads"].append({"name": case["name"], "weight": float(case.get("weight", 1.0)),
                            "loads": rows})
    keep_in &= ~keep_out
    mat = spec["material"]
    p = simp.Problem(n=grid.n, h=h, space=space, keep_in=keep_in, keep_out=keep_out,
                     fixed_dofs=fixed.ravel(), loads=loads, E=float(mat["E"]),
                     nu=float(mat["nu"]), origin=tuple(grid.origin))
    info = {"grid": list(grid.n), "h_mm": h, "origin": grid.origin.tolist(),
            "elements": grid.ne, "space_elements": int(space.sum()),
            "keep_in_elements": int(keep_in.sum()), "keep_out_elements": int(keep_out.sum()),
            "fixed_nodes": int(fixed.any(1).sum()), "dofs": 3 * nn}
    return p, grid, bc, info


# ---- shapes for the booleans

def space_solid(spec: dict, shape):
    if spec["design_space"] == "bbox":
        from build123d import Align, Box, Pos
        lo, hi = spec["bbox"]["min"], spec["bbox"]["max"]
        return Pos(*lo) * Box(*(b - a for a, b in zip(lo, hi)), align=(Align.MIN,) * 3)
    solids = shape.solids()
    if len(solids) != 1:
        raise TopoptError(f"the design space must be one solid (the part has {len(solids)})")
    return solids[0]


def keep_out_solids(spec: dict, job: Path) -> list:
    from build123d import import_step
    out = []
    for r in spec.get("keep_out") or []:
        if r["kind"] == "part":
            out += list(import_step(str(job / r["step"])).solids())
        else:
            out.append(geo.keep_out_solid(r))
    return out


def _preview(path: Path, x: np.ndarray, p: simp.Problem, grid: vx.Grid) -> int:
    F = geo.nodal_field(x, p.n, p.void)
    V, T = geo.surface_nets(F, p.h, grid.origin - p.h)
    if len(T) == 0:
        return 0
    V = geo.taubin(V, T, PREVIEW_SMOOTH)
    geo.write_glb(path, V, T, color=(0.36, 0.56, 0.86))
    return int(len(T))


def printability(stl_tris: np.ndarray, build_dir: str | None) -> dict:
    """Overhang area fraction: downward-facing area steeper than 45 degrees from the build
    direction, off the build plate."""
    d = build_dir or "z"
    v = np.zeros(3)
    v["xyz".index(d.lstrip("+-"))] = -1.0 if d.startswith("-") else 1.0
    a, b, c = stl_tris[:, 0], stl_tris[:, 1], stl_tris[:, 2]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1) / 2
    nu = n / (2 * area[:, None] + 1e-30)
    height = stl_tris.mean(1) @ v
    plate = height <= (stl_tris.reshape(-1, 3) @ v).min() + 0.05
    over = (nu @ v < -math.cos(math.radians(45))) & ~plate
    tot = float(area.sum())
    return {"build_dir": d, "overhang_area_pct": round(100 * float(area[over].sum()) / tot, 2)
            if tot else 0.0}


def thin_member_pct(x: np.ndarray, p: simp.Problem) -> float:
    """Share of solid voxels in members under 3 voxels thick (distance to void <= 1)."""
    from scipy.ndimage import distance_transform_edt
    solid = geo.element_grid3(x > 0.5, p.n)
    if not solid.any():
        return 0.0
    dist = distance_transform_edt(np.pad(solid, 1))[1:-1, 1:-1, 1:-1]
    return round(100 * float((dist[solid] <= 1.0).mean()), 1)


def run(job: Path, progress=None) -> dict:
    """The whole pipeline in this process (the worker's main, and tests)."""
    from build123d import Solid, export_step, export_stl, import_step
    t_all = time.perf_counter()
    times: dict[str, float] = {}
    spec = json.loads((job / "spec.json").read_text())

    def prog(**kw) -> None:
        if progress is not None:
            progress(kw)
        _write(job / "progress.json", kw)

    prog(phase="voxelize", pct=1.0)
    t = time.perf_counter()
    shape = import_step(str(job / "model.step"))
    p, grid, bc, info = build_problem(spec, job, shape)
    times["voxelize"] = time.perf_counter() - t
    _write(job / "bc.json", bc)
    max_iter = int(spec.get("max_iter", 60))
    every = max(1, int(spec.get("preview_every", 5)))
    state = {"preview": None}

    def on_iter(it: int, row: dict, x: np.ndarray) -> None:
        if it % every == every - 1:
            try:
                if _preview(job / "preview.glb", x, p, grid):
                    state["preview"] = it + 1
            except Exception:  # noqa: BLE001 - a preview is never worth failing the run
                pass
        prog(phase="optimize", it=it + 1, max_iter=max_iter, c=row["c"], vf=row["vf"],
             ch=row["ch"], cg=row["cg"], preview=state["preview"],
             pct=round(5 + 75 * (it + 1) / max_iter, 1))

    opts = simp.Options(volume_fraction=float(spec["volume_fraction"]),
                        penal=float(spec.get("penal", 3.0)),
                        filter_radius=float(spec["filter_radius"]), max_iter=max_iter,
                        mirror_axes=tuple(spec.get("mirror_axes") or ()),
                        extrusion_axis=spec.get("extrusion_axis"),
                        overhang_dir=spec.get("overhang_dir"))
    t = time.perf_counter()
    try:
        res = simp.optimize(p, opts, on_iter=on_iter)
    except simp.InfeasibleProblem as e:
        raise TopoptError(str(e)) from None
    times["optimize"] = time.perf_counter() - t
    np.save(job / "density.npy", res.x)
    prog(phase="geometry", it=res.iters, max_iter=max_iter, preview=state["preview"], pct=82.0)
    t = time.perf_counter()
    F = geo.nodal_field(res.x, p.n, p.void)
    F, filled = geo.disambiguate(F)
    V, T = geo.surface_nets(F, p.h, grid.origin - p.h)
    if len(T) == 0:
        raise TopoptError("the optimized density has no solid region")
    raw = geo.mesh_stats(V, T)
    V = geo.taubin(V, T, FINAL_SMOOTH)
    target = int(spec.get("decimate_tris", 5000))
    V, T = geo.decimate(V, T, target, max_error=0.25 * p.h)
    dec = geo.mesh_stats(V, T)
    if not dec["watertight"]:
        raise TopoptError("the organic surface is not watertight after decimation")
    times["surface"] = time.perf_counter() - t
    t = time.perf_counter()
    sol, valid = geo.mesh_to_solid(V, T)
    if not valid:
        raise TopoptError("the organic surface did not make a valid solid")
    organic = Solid(sol)
    times["brep"] = time.perf_counter() - t
    t = time.perf_counter()
    part = geo.bool_op("common", organic, [space_solid(spec, shape)])
    kos = keep_out_solids(spec, job)
    if kos:
        part = geo.bool_op("cut", part, kos)
    part, dropped = geo.largest_solid(part)
    if not part.is_valid:
        raise TopoptError("the re-imposed solid is not valid")
    times["boolean"] = time.perf_counter() - t
    prog(phase="export", it=res.iters, max_iter=max_iter, preview=state["preview"], pct=90.0)
    t = time.perf_counter()
    export_step(part, str(job / "part.step"))
    export_stl(part, str(job / "part.stl"), tolerance=STL_TOL, angular_tolerance=STL_ANG)
    stl = geo.read_stl_tris(job / "part.stl")
    Vw, Tw = geo.weld(stl)
    geo.write_glb(job / "result.glb", Vw, Tw)
    times["export"] = time.perf_counter() - t
    space_vol = float(p.space.sum()) * p.h ** 3
    report = {
        **info, "filter_radius_elements": opts.filter_radius,
        "filter_radius_mm": round(opts.filter_radius * p.h, 4),
        "volume_fraction_target": opts.volume_fraction, "iters": res.iters,
        "converged": res.converged, "levels": res.levels, "fallback_at": res.fallback_at,
        "compliance_gray": res.compliance, "compliance_bw": res.compliance_bw,
        "case_compliance_bw": res.case_compliance_bw, "deflection_bw": res.deflection_bw,
        "vf_bw": res.vf_bw,
        "history": [{k: r[k] for k in ("it", "c", "vf", "ch", "cg")} for r in res.history],
        "mesh_raw": raw, "mesh": dec, "ambiguous_cells_filled": filled,
        "faces": len(part.faces()), "volume_mm3": float(part.volume),
        "space_volume_mm3": space_vol,
        "final_vf": float(part.volume) / space_vol if space_vol else None,
        "islands_dropped_pct": round(100 * dropped, 2),
        "step_bytes": (job / "part.step").stat().st_size,
        "stl_bytes": (job / "part.stl").stat().st_size,
        "thin_member_pct": thin_member_pct(res.x, p),
        "printability": printability(stl, spec.get("overhang_dir")),
        "times_s": {k: round(v, 2) for k, v in times.items()},
        "total_s": round(time.perf_counter() - t_all, 1)}
    _write(job / "report.json", report)
    prog(phase="done", it=res.iters, max_iter=max_iter, preview=state["preview"], pct=100.0)
    return report


def main(argv: list[str]) -> int:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    run(Path(argv[1]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"TOPOPT_ERROR: {type(e).__name__}: {e}\n")
        sys.exit(1)
