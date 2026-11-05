"""Verify a topology-optimization variant with gmsh + CalculiX on the same load cases.

The optimizer's numbers come from a voxel model; the card shows these instead (research §4:
the voxel model is only trustworthy when members are >= 3 voxels thick). The variant is
meshed to tet10 by gmsh in a sandboxed child: its STEP through gmsh's OCC kernel (every face
remeshed at the target size) or, when that fails, its STL welded at 1e-3 mm (HXT, falling back
to Delaunay); and every load case is solved by CalculiX in its own sandboxed run. Fixtures and
loads are re-found on the new mesh by coincidence with the ORIGINAL part's faces (bc.json, the
worker's triangles of each face spec): the functional faces come out of the booleans as exact
copies, so the user's own face picks carry over with no guesswork. Loads are consistent nodal
loads (sim/mesh.consistent_loads), so a point-load singularity does not set the peak stress.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from ..sim.ccx import run_ccx
from ..sim.fem import MAX_SOLVE_NODES
from ..sim.deck import cload_lines, model_cards, _lines16
from ..sim.materials import Material
from ..sim.mesh import Mesh, Surface, consistent_loads, load_mesh, mesh_step
from ..sim.proc import run_sandboxed
from ..sim.results import parse_frd, peaks, write_result_glb
from ..sim.types import SimError
from . import geometry as geo
from .voxelize import FaceDistance

MESH_WORKER = Path(__file__).resolve().parent / "stl_mesh_worker.py"
MESH_TIMEOUT_S = 600.0
SOLVE_TIMEOUT_S = 900.0
# the final STL is within 0.05 mm of the B-rep and the original face tessellation within
# ~0.05 mm of its surface: a boundary triangle is on an original face within this distance
MATCH_TOL_MM = 0.12
REMESH_TRIES = 3


def weld_stl(src: Path, dst: Path) -> dict:
    V, T = geo.weld(geo.read_stl_tris(src))
    geo.write_stl(dst, V, T)
    return geo.mesh_stats(V, T)


async def mesh_stl(stl: Path, d: Path, size_mm: float) -> Mesh:
    out = d / "mesh.json"
    rc, _, err = await run_sandboxed(
        [sys.executable, "-I", str(MESH_WORKER), str(stl.resolve()), str(out.resolve()),
         repr(size_mm)], d, timeout_s=MESH_TIMEOUT_S, what="gmsh meshing")
    if rc != 0 or not out.exists():
        msg = next((ln for ln in err.splitlines() if ln.startswith("MESH_ERROR")), err.strip())
        raise SimError("mesh_failed", f"meshing the variant failed: {msg[-600:] or rc}",
                       hint="the organic surface may self-intersect; try a coarser resolution "
                            "or a larger filter radius")
    return await asyncio.to_thread(load_mesh, out)


def select_boundary(mesh: Mesh, face_tris: np.ndarray, tol: float = MATCH_TOL_MM) -> list:
    """The mesh's boundary triangles (6-node) lying on a face (all three corners within tol)."""
    tris = [t for s in mesh.surfaces for t in s.tris]
    corners = np.array([[mesh.nodes[n] for n in t[:3]] for t in tris], float)
    fd = FaceDistance(np.asarray(face_tris, float), max_edge=1.0)
    d = fd(corners.reshape(-1, 3), cutoff=tol).reshape(-1, 3)
    return [t for t, ok in zip(tris, (d <= tol).all(1)) if ok]


def select_in_box(mesh: Mesh, box: list, tol: float = MATCH_TOL_MM) -> list:
    """The mesh's boundary triangles with every corner inside a box."""
    tris = [t for s in mesh.surfaces for t in s.tris]
    corners = np.array([[mesh.nodes[n] for n in t[:3]] for t in tris], float)
    b = np.asarray(box, float)
    ok = ((corners >= b[:3] - tol) & (corners <= b[3:] + tol)).all((1, 2))
    return [t for t, y in zip(tris, ok) if y]


def _select(mesh: Mesh, row: dict) -> list:
    if row.get("box"):
        return select_in_box(mesh, row["box"])
    return select_boundary(mesh, np.array(row["tris"]))


def setup(mesh: Mesh, bc: dict) -> tuple[Mesh, dict]:
    """A mesh whose surfaces are the fixture / load groups (tags 1..), and the plan."""
    surfaces: list[Surface] = []

    def group(tris: list) -> int:
        tag = len(surfaces) + 1
        surfaces.append(Surface(tag=tag, center=[0, 0, 0], bbox=[0] * 6, area=0.0, tris=tris))
        return tag

    fixed: set[int] = set()
    plan = {"fixtures": [], "cases": []}
    for fx in bc["fixtures"]:
        sel = _select(mesh, fx)
        if not sel:
            raise SimError("bad_setup", f"the fixture face {fx['face']} is not on the variant",
                           hint="keep the fixture face inside the design space")
        fixed |= {n for t in sel for n in t}
        plan["fixtures"].append({"face": fx["face"], "triangles": len(sel)})
    for case in bc["loads"]:
        rows = []
        for ld in case["loads"]:
            sel = _select(mesh, ld)
            if not sel:
                raise SimError("bad_setup", f"the load face {ld['face']} is not on the variant",
                               hint="keep the load face inside the design space")
            rows.append({**{k: ld[k] for k in ("face", "newtons", "direction")},
                         "tag": group(sel), "triangles": len(sel)})
        plan["cases"].append({"name": case["name"], "weight": case["weight"], "loads": rows})
    m = Mesh(nodes=mesh.nodes, tets=mesh.tets, surfaces=surfaces, size_mm=mesh.size_mm,
             gmsh_version=mesh.gmsh_version, order=mesh.order)
    plan["fixed_nodes"] = sorted(fixed)
    return m, plan


def write_case_deck(path: Path, mesh: Mesh, material: Material, fixed: list[int],
                    case: dict) -> dict[int, list[float]]:
    cload: dict[int, list[float]] = {}
    for ld in case["loads"]:
        F = tuple(ld["newtons"] * c for c in ld["direction"])
        for n, v in consistent_loads(mesh, [ld["tag"]], F).items():
            acc = cload.setdefault(n, [0.0, 0.0, 0.0])
            for k in range(3):
                acc[k] += v[k]
    held = set(fixed)
    cload = {n: v for n, v in cload.items() if n not in held}
    lines = ["*HEADING", "topology optimization verification (guide only)",
             *model_cards(mesh, material), "*NSET, NSET=NFIX", *_lines16(fixed),
             "*STEP", "*STATIC", "*BOUNDARY", "NFIX,1,3", "*CLOAD", *cload_lines(cload),
             "*NODE FILE", "U", "*EL FILE", "S", "*END STEP", ""]
    Path(path).write_text("\n".join(lines))
    return cload


async def verify(job: Path, material: Material, ccx: str, *, size_mm: float,
                 progress=None, threads: int = 2) -> dict:
    """Run the verification of the variant in `job` (worker outputs); returns the record."""
    t0 = time.perf_counter()
    d = job / "verify"
    d.mkdir(exist_ok=True)
    if progress:
        progress("mesh")
    surf = None
    try:
        # gmsh OCC on the STEP: every planar face meshed at the target size (a few seconds
        # for ~5k faces); the STL path below keeps OCCT's long boolean-cut slivers as tet
        # faces, which stiffens the model by ~10%
        mesh = await mesh_step(job / "part.step", d, size_mm, timeout_s=MESH_TIMEOUT_S)
        # the same cap as every study: a skeletal body's surface sets its node count, far over
        # the volume estimate, and CalculiX dies of a bus error past it (403k nodes on a frame)
        for _ in range(REMESH_TRIES):
            if len(mesh.nodes) <= MAX_SOLVE_NODES:
                break
            size_mm = round(size_mm * (len(mesh.nodes) / MAX_SOLVE_NODES) ** (1 / 3) * 1.05, 3)
            mesh = await mesh_step(job / "part.step", d, size_mm, timeout_s=MESH_TIMEOUT_S)
        mesher = "gmsh OCC (STEP)"
    except SimError:
        surf = await asyncio.to_thread(weld_stl, job / "part.stl", d / "welded.stl")
        if not surf["watertight"]:
            raise SimError("mesh_failed", f"the variant's surface has {surf['bad_edges']} "
                           "open edges after welding",
                           hint="re-run with a coarser resolution") from None
        mesh = await mesh_stl(d / "welded.stl", d, size_mm)
        mesher = "gmsh discrete (welded STL)"
    bc = json.loads((job / "bc.json").read_text())
    m, plan = await asyncio.to_thread(setup, mesh, bc)
    fixed = plan["fixed_nodes"]
    cases = []
    total_c = 0.0
    worst = None
    for i, case in enumerate(plan["cases"]):
        cd = d / f"case{i}"
        cd.mkdir(exist_ok=True)
        if progress:
            progress("solve", i, len(plan["cases"]))
        cload = await asyncio.to_thread(write_case_deck, cd / "job.inp", m, material, fixed,
                                        case)
        frd_path = await run_ccx(ccx, cd, timeout_s=SOLVE_TIMEOUT_S, threads=threads)
        frd = await asyncio.to_thread(parse_frd, frd_path)
        U = frd.get("DISP", {})
        comp = sum(sum(f[k] * U[n][k] for k in range(3)) for n, f in cload.items() if n in U)
        load_nodes = {n for ld in case["loads"] for t in m.surface(ld["tag"]).tris for n in t}
        defl = max((math.sqrt(sum(x * x for x in U[n][:3])) for n in load_nodes if n in U),
                   default=0.0)
        pk = await asyncio.to_thread(peaks, m, frd, set(fixed))
        row = {"name": case["name"], "weight": case["weight"], "compliance": comp,
               "max_deflection_mm": defl, "max_von_mises_mpa": pk["max_von_mises_mpa"],
               "max_von_mises_at": pk["max_von_mises_at"],
               "max_von_mises_at_fixture_mpa": pk["max_von_mises_at_fixture_mpa"],
               "max_displacement_mm": pk["max_displacement_mm"]}
        cases.append(row)
        total_c += case["weight"] * comp
        if worst is None or row["max_von_mises_mpa"] > worst[0]["max_von_mises_mpa"]:
            worst = (row, pk["vm"])
        for name in ("job.frd", "job.dat", "job.sta", "job.cvg", "job.inp"):
            (cd / name).unlink(missing_ok=True)
    vm_max = max(c["max_von_mises_mpa"] for c in cases)
    if worst is not None:
        await asyncio.to_thread(write_result_glb, job / "stress.glb", m, worst[1],
                                vmax=max(vm_max, 1e-9),
                                extras={"case": worst[0]["name"],
                                        "max_von_mises_mpa": vm_max})
    (d / "mesh.json").unlink(missing_ok=True)
    allow = material.allowable_mpa
    # a bracket's real peak is often at its root: the safety factor that ranks and passes is
    # the conservative one, from the peak INCLUDING the fixture zone (an ideal clamp also
    # exaggerates it, so the peak away from the fixture is reported beside it)
    vm_fix = max((c["max_von_mises_at_fixture_mpa"] or 0.0) for c in cases)
    vm_all = max(vm_max, vm_fix)
    return {"solver": "CalculiX", "element": "C3D10 (quadratic tetrahedra)",
            "mesher": f"{mesher} {mesh.gmsh_version}",
            "mesh_size_mm": size_mm, "nodes": len(mesh.nodes), "tets": len(mesh.tets),
            "surface": surf, "fixtures": plan["fixtures"],
            "cases": cases, "compliance": total_c,
            "max_deflection_mm": max(c["max_deflection_mm"] for c in cases),
            "max_von_mises_mpa": vm_max, "max_von_mises_at_fixture_mpa": vm_fix,
            "max_von_mises_incl_fixture_mpa": vm_all,
            "allowable_mpa": allow,
            "safety_factor": allow / vm_all if vm_all > 0 else None,
            "safety_factor_basis": "peak including the fixture zone (conservative)",
            "safety_factor_away_from_fixture": allow / vm_max if vm_max > 0 else None,
            "seconds": round(time.perf_counter() - t0, 1)}
