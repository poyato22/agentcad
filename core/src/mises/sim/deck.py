"""CalculiX input decks for every study type: the model (nodes, elements, material), supports
(fixed, pinned, roller, symmetry), loads (force, pressure, moment, gravity, bolt bearing, remote
force) and thermal boundary conditions, resolved from face picks on the mesh.

Units: mm, N, s, MPa, tonne/mm^3, mJ, mW. Thermal: conductivity W/(m K) equals mW/(mm K)
numerically; heat flux W/m^2 = 1e-3 mW/mm^2; film coefficients W/(m^2 K) = 1e-3 mW/(mm^2 K).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .materials import Material
from .mesh import Mesh, consistent_loads, select_faces
from .settings import unit_vec
from .types import SimError

# gmsh 10-node tetra -> CalculiX C3D10: the last two edge nodes are swapped
_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]
# CalculiX tetra faces (corner positions): F1 1-2-3, F2 1-4-2, F3 2-4-3, F4 3-4-1
_FACES = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))
G_MM_S2 = 9810.0


def _lines16(ids) -> list[str]:
    ids = sorted(ids)
    return [",".join(map(str, ids[i:i + 16])) for i in range(0, len(ids), 16)]


def model_cards(mesh: Mesh, material: Material, plastic: list[str] | None = None,
                moved: dict | None = None) -> list[str]:
    """*NODE, *ELEMENT and *MATERIAL for both mechanical and heat-transfer steps; `plastic`:
    the *PLASTIC card of a nonlinear study (it follows *ELASTIC); `moved`: node positions that
    differ from the mesh in this deck only (contact faces nudged into touch)."""
    moved = moved or {}
    out = ["*NODE"]
    out += [f"{n},{x:.9g},{y:.9g},{z:.9g}" for n, (x, y, z) in
            ((n, moved.get(n, p)) for n, p in mesh.nodes.items())]
    if mesh.order == 2:
        out.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
        out += [f"{i}," + ",".join(str(t[k]) for k in _TET10) for i, t in enumerate(mesh.tets, 1)]
    else:
        out.append("*ELEMENT, TYPE=C3D4, ELSET=EALL")
        out += [f"{i}," + ",".join(map(str, t[:4])) for i, t in enumerate(mesh.tets, 1)]
    out += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{material.e_eff_mpa:.9g},{material.nu:.9g}",
            *(plastic or []), "*DENSITY", f"{material.density_t_mm3:.9g}",
            "*CONDUCTIVITY", f"{material.k_w_mk:.9g}",
            "*EXPANSION", f"{material.alpha_per_k:.9g}",
            "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT"]
    return out


# ---- surface geometry

def face_index(mesh: Mesh) -> dict[frozenset, tuple[int, int, int]]:
    """corner-node triple -> (element number, CalculiX face number, opposite corner node)."""
    idx = mesh.extra.get("_faces")
    if idx is None:
        idx = {}
        for e, t in enumerate(mesh.tets, 1):
            for f, (a, b, c) in enumerate(_FACES, 1):
                opp = ({0, 1, 2, 3} - {a, b, c}).pop()
                idx[frozenset((t[a], t[b], t[c]))] = (e, f, t[opp])
        mesh.extra["_faces"] = idx
    return idx


def tri_geometry(mesh: Mesh, tri: list[int]) -> tuple[np.ndarray, float, np.ndarray]:
    """(outward unit normal, area, centroid) of a boundary triangle (corners)."""
    p = np.array([mesh.nodes[n] for n in tri[:3]], dtype=float)
    n = np.cross(p[1] - p[0], p[2] - p[0])
    area = 0.5 * float(np.linalg.norm(n))
    n = n / (2 * area) if area > 0 else n
    hit = face_index(mesh).get(frozenset(tri[:3]))
    if hit is not None and np.dot(n, np.array(mesh.nodes[hit[2]]) - p[0]) > 0:
        n = -n  # point away from the element's opposite corner: outward
    return n, area, p.mean(axis=0)


def surface_faces(mesh: Mesh, tags: list[int]) -> list[tuple[int, int, float]]:
    """(element, face number, area) of every element face on the surfaces."""
    idx = face_index(mesh)
    out = []
    for t in tags:
        for tri in mesh.surface(t).tris:
            hit = idx.get(frozenset(tri[:3]))
            if hit is not None:
                out.append((hit[0], hit[1], tri_geometry(mesh, tri)[1]))
    if not out:
        raise SimError("bad_setup", "the face has no element faces in the mesh")
    return out


def mean_normal(mesh: Mesh, tags: list[int]) -> np.ndarray:
    acc = np.zeros(3)
    for t in tags:
        for tri in mesh.surface(t).tris:
            n, a, _ = tri_geometry(mesh, tri)
            acc += a * n
    norm = np.linalg.norm(acc)
    if norm < 1e-12:
        raise SimError("bad_setup", "the face has no clear normal",
                       hint="roller and symmetry supports need a flat face")
    return acc / norm


def fit_axis(mesh: Mesh, tags: list[int], axis=None) -> tuple[np.ndarray, np.ndarray]:
    """(point on axis, unit axis) of a cylindrical face: the axis is the direction every
    normal is perpendicular to; the point is the least-squares meeting point of the normals."""
    rows, rhs, M = [], [], np.zeros((3, 3))
    for t in tags:
        for tri in mesh.surface(t).tris:
            n, a, c = tri_geometry(mesh, tri)
            M += a * np.outer(n, n)
            P = np.eye(3) - np.outer(n, n)
            rows.append(math.sqrt(a) * P)
            rhs.append(math.sqrt(a) * P @ c)
    if not rows:
        raise SimError("bad_setup", "the pinned face has no triangles in the mesh")
    if axis is not None:
        ax = np.array(unit_vec(axis))
    else:
        w, v = np.linalg.eigh(M)
        ax = v[:, 0]
        if w[0] > 0.05 * max(w[-1], 1e-12):
            raise SimError("bad_setup", "the pinned face is not cylindrical",
                           hint="pin a hole's cylindrical face, or give its axis")
    A = np.vstack(rows + [np.outer(ax, ax)])
    b = np.concatenate(rhs + [np.zeros(3)])
    pt = np.linalg.lstsq(A, b, rcond=None)[0]
    return pt, ax


# ---- supports and loads

@dataclass
class Setup:
    """Resolved boundary conditions of one load case."""
    cards: list[str] = field(default_factory=list)       # NSET / TRANSFORM definitions
    boundary: list[str] = field(default_factory=list)    # *BOUNDARY lines
    held: set[int] = field(default_factory=set)          # fully fixed nodes
    supported: set[int] = field(default_factory=set)     # every supported node (peak excl.)
    cload: dict[int, list[float]] = field(default_factory=dict)
    dload: list[str] = field(default_factory=list)
    dropped_n: float = 0.0
    total_n: float = 0.0
    tags: dict[str, list[int]] = field(default_factory=dict)


def _faces(mesh: Mesh, face: str, metrics: dict, cache: dict) -> list[int]:
    if face not in cache:
        cache[face] = select_faces(face, mesh.surfaces, metrics["bbox"], metrics.get("face_list"),
                                   metrics.get("face_tags"))
    return cache[face]


def resolve_supports(mesh: Mesh, metrics: dict, supports: list, setup: Setup) -> None:
    taken: set[int] = set()
    for i, s in enumerate(sorted(supports, key=lambda s: s.kind != "fixed"), 1):
        tags = _faces(mesh, s.face, metrics, setup.tags)
        nodes = mesh.nodes_of(tags)
        setup.supported |= nodes
        name = f"NSUP{i}"
        if s.kind == "fixed":
            setup.held |= nodes
            setup.cards += [f"*NSET, NSET={name}", *_lines16(nodes)]
            setup.boundary.append(f"{name},1,3")
            taken |= nodes
            continue
        free = nodes - taken - setup.held
        if not free:
            continue
        taken |= free
        setup.cards += [f"*NSET, NSET={name}", *_lines16(free)]
        if s.kind == "pinned":
            pt, ax = fit_axis(mesh, tags, s.axis)
            a, b = pt, pt + 10.0 * ax
            setup.cards += [f"*TRANSFORM, NSET={name}, TYPE=C",
                            ",".join(f"{v:.9g}" for v in (*a, *b))]
            setup.boundary += [f"{name},1,1", f"{name},3,3"]  # radial + axial held
        else:  # roller / symmetry: the normal displacement is held
            n = mean_normal(mesh, tags)
            k = int(np.argmax(np.abs(n)))
            if abs(n[k]) > 0.9999:
                setup.boundary.append(f"{name},{k + 1},{k + 1}")
            else:
                t = np.cross(n, np.eye(3)[int(np.argmin(np.abs(n)))])
                setup.cards += [f"*TRANSFORM, NSET={name}, TYPE=R",
                                ",".join(f"{v:.9g}" for v in (*n, *t))]
                setup.boundary.append(f"{name},1,1")
    if not setup.boundary:
        raise SimError("bad_setup", "no support holds the part")


def _add(setup: Setup, loads: dict) -> None:
    for n, v in loads.items():
        acc = setup.cload.setdefault(n, [0.0, 0.0, 0.0])
        for k in range(3):
            acc[k] += v[k]


def _weights(mesh: Mesh, tags: list[int]) -> dict[int, float]:
    return {n: v[0] for n, v in consistent_loads(mesh, tags, (1.0, 0.0, 0.0)).items()}


def _moment_loads(mesh: Mesh, tags: list[int], M: np.ndarray, about=None) -> dict:
    """Nodal forces f_i = w_i (omega x r_i) with zero resultant and moment M about the face's
    weighted centroid (J omega = M, J = sum w (|r|^2 I - r r^T))."""
    w = _weights(mesh, tags)
    ids = list(w)
    W = np.array([w[n] for n in ids])
    X = np.array([mesh.nodes[n] for n in ids], dtype=float)
    c = (W[:, None] * X).sum(0) / W.sum()
    R = X - c
    J = (W[:, None, None] * ((R * R).sum(1)[:, None, None] * np.eye(3)
                             - R[:, :, None] * R[:, None, :])).sum(0)
    try:
        om = np.linalg.solve(J, M)
    except np.linalg.LinAlgError:
        raise SimError("bad_setup", "a moment needs a face with some extent") from None
    F = W[:, None] * np.cross(om, R)
    return {n: tuple(F[i]) for i, n in enumerate(ids)}


def resolve_loads(mesh: Mesh, metrics: dict, loads: list, setup: Setup) -> None:
    for ld in loads:
        if ld.kind == "gravity":
            d = unit_vec(ld.direction)
            setup.dload.append(f"EALL,GRAV,{G_MM_S2 * ld.g:.9g},{d[0]:.9g},{d[1]:.9g},{d[2]:.9g}")
            continue
        tags = _faces(mesh, ld.face, metrics, setup.tags)
        if ld.kind == "force":
            d = unit_vec(ld.direction)
            _add(setup, consistent_loads(mesh, tags, tuple(ld.newtons * x for x in d)))
            setup.total_n += ld.newtons
        elif ld.kind == "pressure":
            for e, f, _ in surface_faces(mesh, tags):
                setup.dload.append(f"{e},P{f},{ld.mpa:.9g}")
        elif ld.kind == "moment":
            _add(setup, _moment_loads(mesh, tags, ld.n_mm * np.array(unit_vec(ld.axis))))
        elif ld.kind == "bearing":
            d = np.array(unit_vec(ld.direction))
            # the bolt presses on the half of the hole whose outward normal faces against d
            _add(setup, consistent_loads(mesh, tags, tuple(ld.newtons * d),
                                         weight=lambda tri: -float(
                                             np.dot(tri_geometry(mesh, tri)[0], d))))
            setup.total_n += ld.newtons
        elif ld.kind == "remote_force":
            d = np.array(unit_vec(ld.direction))
            F = ld.newtons * d
            _add(setup, consistent_loads(mesh, tags, tuple(F)))
            w = _weights(mesh, tags)
            c = sum(np.array(mesh.nodes[n]) * v for n, v in w.items()) / sum(w.values())
            M = np.cross(np.array(ld.point, dtype=float) - c, F)
            if np.linalg.norm(M) > 1e-12:
                _add(setup, _moment_loads(mesh, tags, M))
            setup.total_n += ld.newtons
    for n in setup.held:  # a load on a clamped node goes straight into the support
        v = setup.cload.pop(n, None)
        if v is not None:
            setup.dropped_n += math.sqrt(sum(x * x for x in v))


def cload_lines(cload: dict[int, list[float]]) -> list[str]:
    out = []
    for n, vec in sorted(cload.items()):
        for dof, v in enumerate(vec, 1):
            if v != 0.0:
                out.append(f"{n},{dof},{v:.9g}")
    return out


def write_deck(path: Path, mesh: Mesh, material: Material, setup_cards: list[str],
               step: list[str], heading: str, plastic: list[str] | None = None,
               moved: dict | None = None) -> None:
    lines = ["*HEADING", f"{heading} (guide only)",
             *model_cards(mesh, material, plastic, moved), *setup_cards, *step, ""]
    Path(path).write_text("\n".join(lines))


def static_step(setup: Setup) -> list[str]:
    out = ["*STEP", "*STATIC", "*BOUNDARY", *setup.boundary]
    if setup.cload:
        out += ["*CLOAD", *cload_lines(setup.cload)]
    if setup.dload:
        out += ["*DLOAD", *setup.dload]
    return out + ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP"]


def frequency_step(setup: Setup | None, n_modes: int) -> list[str]:
    out = ["*STEP", "*FREQUENCY", str(n_modes)]
    if setup is not None and setup.boundary:
        out += ["*BOUNDARY", *setup.boundary]
    return out + ["*NODE FILE", "U", "*END STEP"]


def _scaled_dload(line: str, scale: float) -> str:
    """One *DLOAD line with its magnitude times scale: GRAV carries it third, pressure last."""
    parts = line.split(",")
    i = 2 if parts[1].strip().upper() == "GRAV" else len(parts) - 1
    parts[i] = f"{float(parts[i]) * scale:.9g}"
    return ",".join(parts)


def buckle_step(setup: Setup, n_modes: int, scale: float = 1.0) -> list[str]:
    """The *BUCKLE step with every load times `scale` (a reference load: buckling factors are
    linear in the load, so the caller multiplies them back by `scale`)."""
    out = ["*STEP", "*BUCKLE", str(n_modes), "*BOUNDARY", *setup.boundary]
    if setup.cload:
        cload = setup.cload if scale == 1.0 else {
            n: [v * scale for v in vec] for n, vec in setup.cload.items()}
        out += ["*CLOAD", *cload_lines(cload)]
    if setup.dload:
        out += ["*DLOAD", *(setup.dload if scale == 1.0
                            else [_scaled_dload(x, scale) for x in setup.dload])]
    return out + ["*NODE FILE", "U", "*END STEP"]


def thermal_step(mesh: Mesh, metrics: dict, s) -> tuple[list[str], list[str], set[int]]:
    """(set cards, step cards, nodes held at a temperature) of a steady heat-transfer step."""
    cache: dict = {}
    cards, bound, dflux, film, held = [], [], [], [], set()
    for i, t in enumerate(s.temperatures, 1):
        nodes = mesh.nodes_of(_faces(mesh, t.face, metrics, cache))
        held |= nodes
        cards += [f"*NSET, NSET=NT{i}", *_lines16(nodes)]
        bound.append(f"NT{i},11,11,{t.temp_c:.9g}")
    for h in s.heat:
        faces = surface_faces(mesh, _faces(mesh, h.face, metrics, cache))
        area_m2 = sum(a for *_, a in faces) * 1e-6
        q = (h.w_m2 if h.w_m2 is not None else h.watts / area_m2) * 1e-3  # mW/mm^2
        dflux += [f"{e},S{f},{q:.9g}" for e, f, _ in faces]
    for c in s.convection:
        faces = surface_faces(mesh, _faces(mesh, c.face, metrics, cache))
        film += [f"{e},F{f},{c.ambient_c:.9g},{c.h_w_m2k * 1e-3:.9g}" for e, f, _ in faces]
    # initial temperatures: a guess for the solver
    t0 = (s.temperatures[0].temp_c if s.temperatures else s.convection[0].ambient_c)
    cards += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"EALL_NODES,{t0:.9g}"]
    step = ["*STEP", "*HEAT TRANSFER, STEADY STATE", "1.,1."]
    if bound:
        step += ["*BOUNDARY", *bound]
    if dflux:
        step += ["*DFLUX", *dflux]
    if film:
        step += ["*FILM", *film]
    step += ["*NODE FILE", "NT", "*EL FILE", "HFL", "*END STEP"]
    all_nodes = ["*NSET, NSET=EALL_NODES", *_lines16(mesh.nodes)]
    return all_nodes + cards, step, held
