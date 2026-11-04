"""A small independent CalculiX linear-static check for the Mises-Functional structural tasks.

gmsh (OCC) meshes the exported STEP into 10-node tets; we write the CalculiX deck ourselves
(C3D10, linear elastic, fixed and loaded nodes picked by coordinates), run `ccx`, and read the
.dat: max nodal displacement and von Mises stress at the integration points. Reported:
`vm_max` and `vm_p995` (99.5th percentile of integration-point von Mises, which keeps a single
re-entrant-corner singularity from deciding a pass; the checkers use p99.5 against the limit and
report both). Loads are split equally over the selected nodes (a total force, not a consistent
pressure). Units: mm, N, MPa.

Kept separate from Mises' own simulation code on purpose: the checker must not share code
with the system under test.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

CCX = shutil.which("ccx") or "ccx"
GMSH_TO_CCX_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


@dataclass
class Mesh:
    nodes: np.ndarray          # (N, 3)
    tags: np.ndarray           # (N,) gmsh node tags
    elems: np.ndarray          # (E, 10) node tags in ccx order


def mesh_step(step: Path, size_frac: float = 1 / 35, min_frac: float = 1 / 300) -> Mesh:
    import gmsh
    gmsh.initialize(["-noenv"], interruptible=False)    # no SIGINT handler: runs in threads
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("m")
        gmsh.model.occ.importShapes(str(step))
        gmsh.model.occ.synchronize()
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        diag = math.dist((xmin, ymin, zmin), (xmax, ymax, zmax))
        # thin walls need elements about as small as the wall (or curved tets invert)
        hmax = diag * size_frac
        try:
            import build123d as b3
            from . import geom as _g
            t = _g.wall_thickness_samples(b3.import_step(str(step)), 300)
            if len(t):
                hmax = min(hmax, max(2.0 * float(np.percentile(t, 10)), diag / 150))
        except Exception:  # noqa: BLE001 - fall back to the size from the bounding box
            pass
        gmsh.option.setNumber("Mesh.MeshSizeMax", hmax)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min(diag * min_frac, hmax / 2))
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 10)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.HighOrderOptimize", 1)
        gmsh.model.mesh.generate(3)
        tags, coords, _ = gmsh.model.mesh.getNodes()
        etypes, _, enodes = gmsh.model.mesh.getElements(3)
        conn = None
        for et, en in zip(etypes, enodes):
            if et == 11:                    # 10-node tetrahedron
                conn = np.array(en, dtype=np.int64).reshape(-1, 10)[:, GMSH_TO_CCX_TET10]
        if conn is None:
            raise RuntimeError("gmsh produced no 10-node tetrahedra")
        return Mesh(np.array(coords).reshape(-1, 3), np.array(tags, dtype=np.int64), conn)
    finally:
        gmsh.finalize()


@dataclass
class FeaResult:
    u_max: float
    vm_max: float
    vm_p995: float
    n_nodes: int
    n_elems: int
    n_fixed: int
    n_loaded: int
    coords: np.ndarray | None = None     # (N, 3) of the solved nodes
    disp: np.ndarray | None = None       # (N, 3) their displacements

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("coords", "disp")}


def solve(step: Path, work: Path, *, E: float, nu: float,
          fixed: Callable[[np.ndarray], np.ndarray],
          loads: Callable[[np.ndarray, np.ndarray], np.ndarray],
          exclude: Callable[[np.ndarray], np.ndarray] | None = None) -> FeaResult:
    """fixed(coords) -> bool mask; loads(coords, mask_free) -> (N, 3) nodal forces;
    exclude(element centroids) -> bool mask of elements left out of the stress statistics
    (e.g. a pin's bearing zone, where nodal point loads make a local singularity)."""
    work.mkdir(parents=True, exist_ok=True)
    m = mesh_step(step)
    fmask = fixed(m.nodes)
    forces = loads(m.nodes, ~fmask)
    lmask = np.abs(forces).sum(1) > 0
    if fmask.sum() < 3 or lmask.sum() < 1:
        raise RuntimeError(f"bad BC selection: {fmask.sum()} fixed, {lmask.sum()} loaded nodes")
    used = np.unique(m.elems)
    tag_ix = {t: i for i, t in enumerate(m.tags)}
    lines = ["*HEADING", "mises-bench check", "*NODE, NSET=NALL"]
    for t in used:
        x, y, z = m.nodes[tag_ix[t]]
        lines.append(f"{t}, {x:.9g}, {y:.9g}, {z:.9g}")
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    for i, e in enumerate(m.elems, 1):
        lines.append(f"{i}, " + ", ".join(str(int(v)) for v in e))
    usedset = set(used.tolist())
    fixed_tags = [int(t) for t, f in zip(m.tags, fmask) if f and t in usedset]
    lines.append("*NSET, NSET=NFIX")
    lines += [", ".join(map(str, fixed_tags[i:i + 12])) for i in range(0, len(fixed_tags), 12)]
    lines += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{E:.9g}, {nu:.6g}",
              "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
              "*BOUNDARY", "NFIX, 1, 3", "*STEP", "*STATIC", "*CLOAD"]
    for t, f in zip(m.tags, forces):
        if t in usedset:
            for k in range(3):
                if f[k] != 0:
                    lines.append(f"{int(t)}, {k + 1}, {f[k]:.9g}")
    lines += ["*NODE PRINT, NSET=NALL", "U", "*EL PRINT, ELSET=EALL", "S", "*END STEP"]
    (work / "job.inp").write_text("\n".join(lines) + "\n")
    p = subprocess.run([CCX, "-i", "job"], cwd=work, capture_output=True, text=True, timeout=1800)
    dat = work / "job.dat"
    if not dat.is_file() or "ERROR" in p.stdout.upper().split("JOB FINISHED")[0][-2000:]:
        raise RuntimeError(f"ccx failed: {p.stdout[-800:]}")
    ids, u, s, eids = _parse_dat(dat.read_text())
    vm = _von_mises(s)
    if exclude is not None:
        tag_xyz = {t: m.nodes[i] for t, i in tag_ix.items()}
        cent = np.array([np.mean([tag_xyz[int(t)] for t in e[:4]], axis=0) for e in m.elems])
        drop = exclude(cent)
        keep = ~drop[eids - 1]
        if keep.any():
            vm = vm[keep]
    xyz = np.array([m.nodes[tag_ix[int(t)]] for t in ids])
    return FeaResult(float(np.linalg.norm(u, axis=1).max()), float(vm.max()),
                     float(np.percentile(vm, 99.5)), len(used), len(m.elems), len(fixed_tags),
                     int(lmask.sum()), xyz, u)


def _parse_dat(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ids, u, s, eids, mode = [], [], [], [], None
    for ln in text.splitlines():
        low = ln.lower()
        if "displacements" in low:
            mode = "u"
            continue
        if "stresses" in low:
            mode = "s"
            continue
        parts = ln.split()
        if not parts or not re.match(r"^-?\d", parts[0]):
            continue
        try:
            vals = [float(v) for v in parts]
        except ValueError:
            continue
        if mode == "u" and len(vals) == 4:
            ids.append(int(vals[0]))
            u.append(vals[1:])
        elif mode == "s" and len(vals) == 8:
            eids.append(int(vals[0]))
            s.append(vals[2:])
    return np.array(ids), np.array(u), np.array(s), np.array(eids)


def _von_mises(s: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, sxz, syz = s.T
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                   + 3 * (sxy ** 2 + sxz ** 2 + syz ** 2))


# ---------------------------------------------------------------------------- BC helpers
def plane_mask(coords: np.ndarray, axis: int, value: float, tol: float) -> np.ndarray:
    return np.abs(coords[:, axis] - value) <= tol


def split_force(mask: np.ndarray, total: np.ndarray) -> np.ndarray:
    f = np.zeros((len(mask), 3))
    n = int(mask.sum())
    if n:
        f[mask] = np.asarray(total, float) / n
    return f


def torque_forces(coords: np.ndarray, mask: np.ndarray, centre, axis, torque: float) -> np.ndarray:
    """Tangential nodal forces on the masked nodes summing to `torque` about the axis."""
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    r = coords - np.asarray(centre, float)
    r = r - np.outer(r @ a, a)
    t = np.cross(a, r)
    f = np.zeros_like(coords)
    r2 = (np.linalg.norm(r, axis=1) ** 2)[mask].sum()
    if r2 > 0:
        f[mask] = t[mask] * (torque / r2)
    return f


# ---------------------------------------------------------------------------- modal / buckling
def _deck_head(m: Mesh, fmask: np.ndarray, E: float, nu: float, rho: float | None):
    used = np.unique(m.elems)
    tag_ix = {t: i for i, t in enumerate(m.tags)}
    lines = ["*HEADING", "mises-bench check", "*NODE, NSET=NALL"]
    for t in used:
        x, y, z = m.nodes[tag_ix[t]]
        lines.append(f"{t}, {x:.9g}, {y:.9g}, {z:.9g}")
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    for i, e in enumerate(m.elems, 1):
        lines.append(f"{i}, " + ", ".join(str(int(v)) for v in e))
    usedset = set(used.tolist())
    fixed_tags = [int(t) for t, f in zip(m.tags, fmask) if f and t in usedset]
    if len(fixed_tags) < 3:
        raise RuntimeError(f"bad BC selection: {len(fixed_tags)} fixed nodes")
    lines.append("*NSET, NSET=NFIX")
    lines += [", ".join(map(str, fixed_tags[i:i + 12])) for i in range(0, len(fixed_tags), 12)]
    lines += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{E:.9g}, {nu:.6g}"]
    if rho is not None:
        lines += ["*DENSITY", f"{rho:.9g}"]            # tonne/mm^3 (g/cm^3 x 1e-9)
    lines += ["*SOLID SECTION, ELSET=EALL, MATERIAL=MAT", "*BOUNDARY", "NFIX, 1, 3"]
    return lines, usedset


def _run(work: Path, lines: list[str]) -> str:
    work.mkdir(parents=True, exist_ok=True)
    (work / "job.inp").write_text("\n".join(lines) + "\n")
    p = subprocess.run([CCX, "-i", "job"], cwd=work, capture_output=True, text=True, timeout=1800)
    dat = work / "job.dat"
    if not dat.is_file():
        raise RuntimeError(f"ccx failed: {p.stdout[-800:]}")
    return dat.read_text()


def modal(step: Path, work: Path, *, E: float, nu: float, rho_g_cm3: float,
          fixed: Callable[[np.ndarray], np.ndarray], n: int = 3) -> list[float]:
    """The first n natural frequencies (Hz) with the fixed nodes clamped."""
    m = mesh_step(step)
    lines, _ = _deck_head(m, fixed(m.nodes), E, nu, rho_g_cm3 * 1e-9)
    lines += ["*STEP", "*FREQUENCY", f"{n}", "*END STEP"]
    text = _run(work, lines)
    freqs, on = [], False
    for ln in text.splitlines():
        if "E I G E N V A L U E   O U T P U T" in ln:
            on = True
            continue
        if on:
            parts = ln.split()
            if len(parts) >= 4 and parts[0].isdigit():
                freqs.append(float(parts[3]))
            elif freqs and not parts:
                break
    if not freqs:
        raise RuntimeError("no eigenvalues in the ccx output")
    return freqs[:n]


def buckle(step: Path, work: Path, *, E: float, nu: float,
           fixed: Callable[[np.ndarray], np.ndarray],
           loads: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> float:
    """The first linear buckling factor (load multiplier) of the given load case."""
    m = mesh_step(step)
    fmask = fixed(m.nodes)
    forces = loads(m.nodes, ~fmask)
    lines, usedset = _deck_head(m, fmask, E, nu, None)
    lines += ["*STEP", "*BUCKLE", "2", "*CLOAD"]
    for t, f in zip(m.tags, forces):
        if t in usedset:
            for k in range(3):
                if f[k] != 0:
                    lines.append(f"{int(t)}, {k + 1}, {f[k]:.9g}")
    lines += ["*END STEP"]
    text = _run(work, lines)
    facs, on = [], False
    for ln in text.splitlines():
        if "B U C K L I N G   F A C T O R   O U T P U T" in ln:
            on = True
            continue
        if on:
            parts = ln.split()
            if len(parts) >= 2 and parts[0].isdigit():
                facs.append(float(parts[1]))
    if not facs:
        raise RuntimeError("no buckling factor in the ccx output")
    return min(f for f in facs if f > 0)
