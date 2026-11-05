"""Shared machinery of the CalculiX study types: mesh levels (coarse + fine, or a convergence
loop), meshing options from the settings, running ccx, result GLBs and probe fields."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from .ccx import run_ccx
from .environments import FACE_PRESETS
from .fields import save_fem
from .materials import Material
from .mesh import (FACE_TAG_RE, Mesh, is_face_id, mesh_sizes, mesh_step, size_floor,
                   tag_face_ids)
from .results import boundary_triangles, error_band, write_glb
from .types import SimError

MESH_TIMEOUT_S = 180.0
REFINE_RATIO = 0.7          # convergence loop: each level's element size / the previous one
MAX_GLB_MODES = 12


@dataclass
class FemJob:
    root: Path                       # the study's temp dir
    step: Path                       # model.step snapshot
    metrics: dict
    material: Material
    ccx: str
    mesh_settings: object            # settings.MeshSettings
    solver_settings: object          # settings.SolverSettings
    progress: Callable[..., None]
    convergence: object | None = None
    extra: dict = field(default_factory=dict)

    @property
    def threads(self) -> int:
        t = getattr(self.solver_settings, "threads", None)
        return t or max(1, min(4, os.cpu_count() or 1))

    @property
    def solve_timeout(self) -> float:
        return float(self.extra.get("solve_timeout_s")
                     or getattr(self.solver_settings, "timeout_s", 300.0))


def level_sizes(job: FemJob) -> tuple[list[float], float]:
    """(element sizes to solve, smallest size the node budget allows)."""
    coarse, fine = mesh_sizes(job.metrics["bbox"]["size"], job.metrics.get("volume_mm3", 0))
    floor = size_floor(job.metrics.get("volume_mm3", 0))
    user = getattr(job.mesh_settings, "size_mm", None)
    if user:
        fine = max(user, floor)
        coarse = 2 * fine
    conv = job.convergence
    if conv is not None and conv.enabled:
        sizes = [coarse * REFINE_RATIO ** i for i in range(conv.max_levels)]
        sizes = [s for s in sizes if s >= floor] or [coarse]
        return sizes, floor
    if job.extra.get("coarse_only"):  # a screening solve (sim_static_run fine=false)
        return [coarse], floor
    return [coarse, fine], floor


def mesh_options(job: FemJob, size: float) -> dict | None:
    ms = job.mesh_settings
    opts: dict = {}
    if not getattr(ms, "second_order", True):
        opts["order"] = 1
    refine = []
    bbox = job.metrics["bbox"]
    for f in getattr(ms, "refine_faces", []) or []:
        local = size * ms.refine_factor
        if f in FACE_PRESETS:
            axis, side = FACE_PRESETS[f]
            refine.append({"size": local, "axis": axis, "value": bbox[side][axis]})
        else:
            ids = (tag_face_ids(f, job.metrics.get("face_tags")) if FACE_TAG_RE.match(f)
                   else [f] if is_face_id(f) else [])
            for fid in ids:
                face = next((x for x in job.metrics.get("face_list") or []
                             if x.get("id") == fid), None)
                if face is None:
                    raise SimError("bad_setup", f"no face {fid} on this part to refine",
                                   hint="pick a face id from mirror_measure face_list")
                refine.append({"size": local, "center": face["center"]})
    for rp in getattr(ms, "refine_points", []) or []:
        refine.append({"size": rp.size_mm, "point": list(rp.point), "radius": rp.radius_mm})
    if refine:
        opts["refine"] = refine
    if job.extra.get("instances"):  # an assembly study: each instance's STEP and transform
        opts["instances"] = job.extra["instances"]
    return opts or None


#: hard cap on a solved mesh. The volume-based estimate (mesh.MAX_FINE_NODES) undercounts
#: thin, face-rich bodies (a topopt plate: 379k nodes against 200k estimated), and CalculiX's
#: SPOOLES solver segfaults past ~1.1M DOF on this class of laptop (seen at 379k nodes).
MAX_SOLVE_NODES = 300_000
_REMESH_TRIES = 3


async def mesh_level(job: FemJob, d: Path, size: float, label: str, pct: float) -> Mesh:
    """Mesh one level; a mesh over MAX_SOLVE_NODES is re-meshed coarser (recorded in
    mesh.extra["coarsened"]) rather than handed to a solver that would crash on it."""
    job.progress(f"mesh_{label}", phase="mesh", pct=pct, detail=f"meshing {label} "
                 f"({size:.2g} mm)", size_mm=size)
    mesh = await mesh_step(job.step, d, size, timeout_s=MESH_TIMEOUT_S,
                           options=mesh_options(job, size))
    first, n0, cur = size, len(mesh.nodes), size
    for _ in range(_REMESH_TRIES):
        n = len(mesh.nodes)
        if n <= MAX_SOLVE_NODES:
            break
        cur = cur * (n / MAX_SOLVE_NODES) ** (1 / 3) * 1.05
        job.progress(f"mesh_{label}", phase="mesh", pct=pct, detail=f"re-meshing {label} "
                     f"coarser ({cur:.2g} mm): {n} nodes is over the solver's "
                     f"{MAX_SOLVE_NODES}", size_mm=cur)
        mesh = await mesh_step(job.step, d, cur, timeout_s=MESH_TIMEOUT_S,
                               options=mesh_options(job, cur))
    if cur != first:
        mesh.size_mm = cur
        mesh.extra["coarsened"] = {"from_mm": round(first, 4), "to_mm": round(cur, 4),
                                   "nodes_before": n0, "nodes": len(mesh.nodes),
                                   "cap": MAX_SOLVE_NODES}
    return mesh


def mesh_note(mesh: Mesh) -> dict:
    """A level's info entry for a mesh that mesh_level had to coarsen (else nothing)."""
    c = mesh.extra.get("coarsened")
    return {"coarsened": c} if c else {}


async def solve_deck(job: FemJob, d: Path, write: Callable[[Path], None], label: str,
                     pct: float, allow_partial: bool = False, **data) -> tuple[Path, Path]:
    """Write the deck (off the event loop) and run ccx in d; returns (.frd, .dat)."""
    await asyncio.to_thread(write, d / "job.inp")
    job.progress(f"solve_{label}", phase="solve", pct=pct, detail=f"solving {label}", **data)
    frd = await run_ccx(job.ccx, d, timeout_s=job.solve_timeout, threads=job.threads,
                        allow_partial=allow_partial)
    return frd, d / "job.dat"


Level = Callable[[float, Path, str, float, float], Awaitable[tuple[float, dict]]]


async def run_levels(job: FemJob, solve_at: Level) -> dict:
    """Solve each mesh level; with convergence on, stop once the key metric changes by less
    than tol_pct. Returns {history, fine, coarse, band, converged}."""
    sizes, _ = level_sizes(job)
    conv = job.convergence
    loop = conv is not None and conv.enabled
    hist: list[dict] = []
    payloads: list[dict] = []
    converged = None
    n = len(sizes)
    for i, size in enumerate(sizes):
        label = (f"L{i}" if loop else ("coarse" if i == 0 else "fine"))
        d = job.root / label
        d.mkdir(parents=True, exist_ok=True)
        lo, hi = 90.0 * i / n, 90.0 * (i + 1) / n
        metric, payload = await solve_at(size, d, label, lo, hi)
        payloads.append(payload)
        hist.append({"level": i, "label": label,
                     "size_mm": round(payload["info"].get("size_mm", size), 4),
                     "nodes": payload["info"]["nodes"], "elements": payload["info"]["elements"],
                     "metric": metric})
        if loop and i > 0:
            change = error_band(coarse=hist[-2]["metric"], fine=metric)
            hist[-1]["change"] = change
            if change is not None and change * 100 < conv.tol_pct:
                converged = True
                break
    if loop and converged is None:
        converged = False
    fine = payloads[-1]
    coarse = payloads[-2] if len(payloads) > 1 else payloads[-1]
    band = (error_band(coarse=hist[-2]["metric"], fine=hist[-1]["metric"])
            if len(hist) > 1 else None)
    return {"history": hist, "fine": fine, "coarse": coarse, "band": band,
            "converged": converged}


def node_arrays(mesh: Mesh) -> tuple[list[int], dict[int, int], np.ndarray, np.ndarray]:
    ids = list(mesh.nodes)
    pos = {n: i for i, n in enumerate(ids)}
    coords = np.array([mesh.nodes[n] for n in ids], dtype=np.float64)
    tets = np.array([[pos[n] for n in t[:4]] for t in mesh.tets], dtype=np.int64)
    return ids, pos, coords, tets


def save_fields(path: Path, mesh: Mesh, channels: dict[str, dict], units: dict[str, str]):
    """channels: name -> {node id: value or [vx, vy, vz]} (missing nodes = 0)."""
    ids, _, coords, tets = node_arrays(mesh)
    arrs = {}
    for name, vals in channels.items():
        sample = next(iter(vals.values()), 0.0)
        width = len(sample) if isinstance(sample, (list, tuple, np.ndarray)) else 0
        if width:
            a = np.zeros((len(ids), width))
            for i, n in enumerate(ids):
                if n in vals:
                    a[i] = vals[n][:width]
        else:
            a = np.array([vals.get(n, 0.0) for n in ids], dtype=float)
        arrs[name] = a
    save_fem(path, coords, tets, arrs, units)


def result_glb(path: Path, mesh: Mesh, color: dict[int, float], *, vmin: float, vmax: float,
               field_name: str, units: str, extras: dict, channels: list[dict]) -> None:
    """Boundary GLB coloured by `color`; channels: [{name, attribute, units, kind,
    values: {node: value | vec}}] -> per-vertex custom attributes."""
    node_ids, tris = boundary_triangles(mesh)
    pos = [c for n in node_ids for c in mesh.nodes[n]]
    chans = []
    for ch in channels:
        vals = ch["values"]
        if ch.get("kind") == "vector":
            flat = [float(x) for n in node_ids for x in (list(vals.get(n, (0, 0, 0)))[:3])]
            mags = [float(np.linalg.norm(vals.get(n, (0, 0, 0))[:3])) for n in node_ids]
        else:
            flat = [float(vals.get(n, 0.0)) for n in node_ids]
            mags = flat
        chans.append({**{k: v for k, v in ch.items() if k != "values"}, "values": flat,
                      "min": min(mags, default=0.0), "max": max(mags, default=0.0)})
    write_glb(path, pos, tris, color=[color.get(n, 0.0) for n in node_ids], vmin=vmin,
              vmax=vmax, field=field_name, units=units, extras=extras, channels=chans)
