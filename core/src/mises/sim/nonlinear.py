"""Nonlinear static studies through CalculiX: large deflection (NLGEOM), bilinear plasticity
(*PLASTIC) and contact (*CONTACT PAIR, surface-to-surface penalty; bonded pairs as *TIE),
between faces of a part, between assembly instances, or against a rigid flat support.

CalculiX 2.23 manual (https://www.dhondt.de/ccx_2.23.pdf): *STEP NLGEOM / INC (7.125),
*STATIC increments (7.123), *PLASTIC (7.106), *CONTACT PAIR (7.22), *SURFACE INTERACTION
(7.131), *SURFACE BEHAVIOR (7.130), *FRICTION (7.64), *TIE (7.133), *CONTACT FILE (7.20).

A rigid support is a slab of C3D8 elements whose nodes are all held: its top face is the
master surface the part's face (the slave) presses on. The step is load controlled; when
CalculiX cannot find equilibrium any more (plastic collapse, buckling, a contact that lets go)
it cuts the increment down to `min_increment` and stops: the load fraction reached is read
from the .sta file and reported, with the fields of the last converged increment.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .deck import Setup, _faces, _lines16, surface_faces
from .materials import Material
from .mesh import Mesh
from .settings import unit_vec
from .types import SimError

# Validated model error of each nonlinear effect: the worst measured benchmark error, rounded
# up to 5% (tests/sim/test_nonlinear.py; the rows are in VALIDATION). A result's
# model_error_band is the largest band of the effects it uses.
MODEL_ERROR_BAND = {"large_deflection": 0.05, "plasticity": 0.10, "contact": 0.15}
VALIDATION = [
    # (effect, benchmark, mesh, measured, reference, signed error)
    ("large_deflection", "cantilever tip load PL^2/EI = 2, tip deflection vs elastica",
     "C3D10, 5 mm on 100x10x10", 0.4938, 0.4935, +0.001),
    ("plasticity", "cantilever plastic hinge load P = sy b h^2 / (4 L), Poisson-free root",
     "C3D10, 2 mm on 100x10x10", 106.8, 100.0, +0.068),
    ("contact", "Hertz sphere on a rigid flat, peak contact pressure",
     "C3D10, 0.15 mm at the contact (quarter model)", None, None, None),
]
OUTPUT_BLOCKS = 10          # field output stored about this many times per step
SLAB_DIV = 4                # rigid support slab: elements per side
# A rigid support with no gap starts this far into the face (x the face's span): face-to-face
# penalty contact only acts where it penetrates, so a part that just touches the support would
# otherwise have no stiffness against it in the first iteration (a rigid-body mode).
INITIAL_OVERLAP = 1e-4


def model_error_band(nl) -> tuple[float, list[str]]:
    effects = [k for k, on in (("large_deflection", nl.large_deflection),
                               ("plasticity", nl.plasticity),
                               ("contact", bool(nl.contacts or nl.rigid_supports))) if on]
    return max((MODEL_ERROR_BAND[k] for k in effects), default=0.0), effects


def describe(nl) -> str:
    """'large deflection + plasticity + contact'."""
    _, effects = model_error_band(nl)
    return " + ".join(e.replace("_", " ") for e in effects)


def plastic_cards(m: Material, tangent_mpa: float | None = None) -> list[str]:
    """Bilinear isotropic hardening: yield at the allowable stress (yield x knock-down, the
    conservative choice) then the tangent modulus Et; CalculiX wants (von Mises stress,
    equivalent plastic strain) pairs, so the plastic slope is H = E Et / (E - Et)."""
    e = m.e_eff_mpa
    et = tangent_used(m, tangent_mpa)
    h = e * et / (e - et)
    sy = m.allowable_mpa
    return ["*PLASTIC", f"{sy:.9g},0", f"{sy + h:.9g},1"]


def tangent_used(m: Material, tangent_mpa: float | None = None) -> float:
    """The post-yield slope a study uses: the setting, else the material's (or the estimate),
    scaled like E by infill / walls, at most half of E."""
    if tangent_mpa is None:
        return m.tangent_eff_mpa
    return min(tangent_mpa * m.stiffness_factor, 0.5 * m.e_eff_mpa)


def validation_rows(effects: list[str]) -> list[dict]:
    return [{"effect": e, "benchmark": b, "mesh": mesh, "measured": got, "reference": ref,
             "error": err, "band": MODEL_ERROR_BAND[e]}
            for e, b, mesh, got, ref, err in VALIDATION if e in effects]


# ---- contact surfaces

@dataclass
class NlModel:
    cards: list[str] = field(default_factory=list)   # model-definition cards
    rigid_nodes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    slave_nodes: set[int] = field(default_factory=set)
    contact: bool = False                            # any *CONTACT PAIR (needs CONTACT FILE)
    pairs: list[dict] = field(default_factory=list)  # what was resolved, for the record


def _surface(mesh: Mesh, name: str, tags: list[int]) -> list[str]:
    faces = surface_faces(mesh, tags)
    return [f"*SURFACE, NAME={name}, TYPE=ELEMENT", *(f"{e},S{f}" for e, f, _ in faces)]


def _side_tags(mesh: Mesh, metrics: dict, side, other, search: float, cache: dict) -> list[int]:
    if side.face is not None:
        tags = _faces(mesh, side.face, metrics, cache)
        if side.instance is not None:
            tags = [t for t in tags if mesh.surface(t).instance == side.instance]
        if not tags:
            raise SimError("bad_setup", f"no face {side.face} on instance {side.instance}")
        return tags
    return touching(mesh, side.instance, other.instance, search)


def _instance_check(mesh: Mesh, side) -> None:
    if side.instance is None:
        return
    known = {s.instance for s in mesh.surfaces}
    if side.instance not in known:
        raise SimError("bad_setup", f"no instance {side.instance} in this assembly study",
                       hint="use instance ids from assembly_get")


def touching(mesh: Mesh, a: str | None, b: str | None, search: float) -> list[int]:
    """Surfaces of instance a that lie within `search` mm of instance b's surfaces (their
    nodes sit on b's triangles' planes, inside the triangles' reach)."""
    if a is None or b is None or a == b:
        raise SimError("bad_setup", "an automatic contact needs two different instances",
                       hint="give each side an instance, or name the faces")
    sa = [s for s in mesh.surfaces if s.instance == a]
    sb = [s for s in mesh.surfaces if s.instance == b]

    def near(x, y) -> bool:
        return all(x.bbox[k] <= y.bbox[k + 3] + search and y.bbox[k] <= x.bbox[k + 3] + search
                   for k in range(3))
    cands = [s for s in sa if any(near(s, t) for t in sb)]
    if not cands:
        return []
    tris = [tri for t in sb if any(near(t, s) for s in cands) for tri in t.tris]
    P = np.array([[mesh.nodes[n] for n in tri[:3]] for tri in tris], dtype=float)
    C = P.mean(1)
    N = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-30)
    R = np.linalg.norm(P - C[:, None], axis=2).max(1)          # triangle reach
    out = []
    for s in cands:
        nodes = np.array([mesh.nodes[n] for n in {n for tri in s.tris for n in tri[:3]}])
        hits = 0
        for chunk in np.array_split(nodes, max(1, len(nodes) // 256 + 1)):
            d = chunk[:, None, :] - C[None]                     # (n, m, 3)
            normal = np.abs((d * N[None]).sum(2))
            inplane = np.sqrt(np.maximum((d * d).sum(2) - normal ** 2, 0.0))
            ok = (normal <= search) & (inplane <= R[None] * 1.05 + search)
            hits += int(ok.any(1).sum())
        if hits >= max(3, 0.1 * len(nodes)):
            out.append(s.tag)
    return out


def _slab(mesh: Mesh, tags: list[int], rs, start_node: int, start_el: int):
    """(nodes {id: xyz}, C3D8 elements [(id, 8 node ids)], top face number) of a rigid slab
    under the face, normal rs.normal pointing into the part."""
    n = np.array(unit_vec(rs.normal))
    t1 = np.cross(n, np.eye(3)[int(np.argmin(np.abs(n)))])
    t1 /= np.linalg.norm(t1)
    t2 = np.cross(n, t1)
    X = np.array([mesh.nodes[k] for k in mesh.nodes_of(tags)], dtype=float)
    h = X @ n
    u, v = X @ t1, X @ t2
    span = max(u.max() - u.min(), v.max() - v.min(), 1e-3)
    m = 0.25 * span + 2 * max(mesh.size_mm, 1e-3)
    top = h.min() - rs.gap_mm + (INITIAL_OVERLAP * span if rs.gap_mm == 0 else 0.0)
    thick = 0.1 * span + mesh.size_mm
    us = np.linspace(u.min() - m, u.max() + m, SLAB_DIV + 1)
    vs = np.linspace(v.min() - m, v.max() + m, SLAB_DIV + 1)
    nodes, ids = {}, {}
    k = start_node
    for layer, hh in ((0, top - thick), (1, top)):
        for i, uu in enumerate(us):
            for j, vv in enumerate(vs):
                nodes[k] = tuple(float(c) for c in uu * t1 + vv * t2 + hh * n)
                ids[(layer, i, j)] = k
                k += 1
    els = []
    e = start_el
    for i in range(SLAB_DIV):
        for j in range(SLAB_DIV):
            q = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]   # counter-clockwise about n
            els.append((e, [ids[(0, *p)] for p in q] + [ids[(1, *p)] for p in q]))
            e += 1
    return nodes, els, 2   # C3D8 face S2 is 5-6-7-8: the top, facing the part


def resolve_nonlinear(mesh: Mesh, metrics: dict, nl, material: Material,
                      setup: Setup) -> NlModel:
    """Contact surfaces, interactions, pairs / ties and rigid slabs of one load case; rigid
    slab nodes are held in setup.boundary."""
    out = NlModel()
    cache = setup.tags
    k_pen = nl.penalty * material.e_eff_mpa          # MPa / mm
    next_node = max(mesh.nodes) + 1
    next_el = len(mesh.tets) + 1
    for i, c in enumerate(nl.contacts, 1):
        for side in (c.a, c.b):
            _instance_check(mesh, side)
        ta = _side_tags(mesh, metrics, c.a, c.b, c.search_mm, cache)
        tb = _side_tags(mesh, metrics, c.b, c.a, c.search_mm, cache)
        if not ta or not tb:
            raise SimError("bad_setup", f"contact {i}: the two instances do not touch (no faces "
                           f"within {c.search_mm:g} mm)",
                           hint="name the faces, or raise search_mm")
        if set(ta) & set(tb):
            raise SimError("bad_setup", f"contact {i}: both sides name the same face")
        out.cards += _surface(mesh, f"SA{i}", ta) + _surface(mesh, f"SB{i}", tb)
        out.slave_nodes |= mesh.nodes_of(ta)
        if c.kind == "bonded":
            out.cards += [f"*TIE, NAME=T{i}, POSITION TOLERANCE={c.search_mm:.9g}",
                          f"SA{i},SB{i}"]
        else:
            out.contact = True
            out.cards += _interaction(f"SI{i}", k_pen, c.friction)
            out.cards += [f"*CONTACT PAIR, INTERACTION=SI{i}, TYPE=SURFACE TO SURFACE, "
                          "ADJUST=0", f"SA{i},SB{i}"]
        out.pairs.append({"pair": i, "kind": c.kind, "friction": c.friction,
                          "a_surfaces": len(ta), "b_surfaces": len(tb)})
    for i, rs in enumerate(nl.rigid_supports, 1):
        tags = _faces(mesh, rs.face, metrics, cache)
        nodes, els, top = _slab(mesh, tags, rs, next_node, next_el)
        next_node += len(nodes)
        next_el += len(els)
        out.rigid_nodes.update(nodes)
        out.cards += ["*NODE", *(f"{k},{x:.9g},{y:.9g},{z:.9g}" for k, (x, y, z) in nodes.items()),
                      f"*ELEMENT, TYPE=C3D8, ELSET=ERIGID{i}",
                      *(f"{e}," + ",".join(map(str, ns)) for e, ns in els),
                      f"*SOLID SECTION, ELSET=ERIGID{i}, MATERIAL=MAT",
                      f"*NSET, NSET=NRIGID{i}", *_lines16(nodes),
                      f"*SURFACE, NAME=SR{i}, TYPE=ELEMENT", *(f"{e},S{top}" for e, _ in els)]
        out.cards += _surface(mesh, f"SP{i}", tags)
        out.cards += _interaction(f"SIR{i}", k_pen, rs.friction)
        out.cards += [f"*CONTACT PAIR, INTERACTION=SIR{i}, TYPE=SURFACE TO SURFACE",
                      f"SP{i},SR{i}"]
        setup.boundary.append(f"NRIGID{i},1,3")
        out.slave_nodes |= mesh.nodes_of(tags)
        out.contact = True
        out.pairs.append({"rigid_support": i, "face": rs.face, "friction": rs.friction})
    return out


def _interaction(name: str, k_pen: float, mu: float) -> list[str]:
    out = [f"*SURFACE INTERACTION, NAME={name}", "*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR",
           f"{k_pen:.9g}"]
    if mu > 0:  # stick slope ~100x below the contact stiffness (manual, *FRICTION)
        out += ["*FRICTION", f"{mu:.9g},{k_pen / 100:.9g}"]
    return out


def nonlinear_step(setup: Setup, nl, model: NlModel) -> list[str]:
    from .deck import cload_lines
    dt = 1.0 / nl.increments
    head = "*STEP, NLGEOM" if nl.large_deflection else "*STEP"
    freq = max(1, math.ceil(nl.increments / OUTPUT_BLOCKS))
    out = [f"{head}, INC={max(nl.max_increments, nl.increments)}", "*STATIC",
           f"{dt:.9g},1.0,{min(nl.min_increment, dt):.9g},{dt:.9g}",
           "*BOUNDARY", *setup.boundary]
    if setup.cload:
        out += ["*CLOAD", *cload_lines(setup.cload)]
    if setup.dload:
        out += ["*DLOAD", *setup.dload]
    out += [f"*NODE FILE, FREQUENCY={freq}", "U", "*EL FILE", "S,PEEQ" if nl.plasticity else "S"]
    if model.contact:
        out += ["*CONTACT FILE", "CDIS,CSTR"]
    return out + ["*END STEP"]


# ---- results

_STA_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)(U?)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def parse_sta(path: Path) -> list[dict]:
    """Increments of a CalculiX .sta: [{step, inc, attempt, converged, iterations, step_time,
    total_time, inc_size}]; an attempt marked U did not converge (the increment was cut)."""
    rows = []
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        m = _STA_ROW.match(line)
        if not m:
            continue
        try:
            rows.append({"step": int(m[1]), "inc": int(m[2]), "attempt": int(m[3]),
                         "converged": not m[4], "iterations": int(m[5]),
                         "step_time": float(m[6]), "total_time": float(m[7]),
                         "inc_size": float(m[8])})
        except ValueError:
            continue
    return rows


def load_reached(rows: list[dict]) -> float:
    """The load fraction of the last converged increment (0 if none converged)."""
    return max((r["step_time"] for r in rows if r["converged"]), default=0.0)


def parse_frd_increments(path: Path, t_max: float) -> tuple[float | None, dict, list]:
    """(time, {block: {node: values}}, [(time, max |U|)]) of the last stored increment at a
    load fraction <= t_max: blocks a failed increment writes after the last converged one are
    dropped. One pass, keeping only the newest increment's blocks."""
    cur_t: float | None = None
    keep_t: float | None = None
    keep: dict = {}
    pending: dict = {}
    path_pts: list[tuple[float, float]] = []
    block: dict | None = None
    name = None

    def flush():
        nonlocal keep, keep_t
        if cur_t is not None and pending and cur_t <= t_max + 1e-9:
            keep, keep_t = pending, cur_t
            d = pending.get("DISP")
            if d:
                path_pts.append((cur_t, max(math.sqrt(sum(x * x for x in v[:3]))
                                            for v in d.values())))
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith("  100C"):
                try:
                    t = float(line[12:24])
                except ValueError:
                    t = None
                if t != cur_t:
                    flush()
                    pending, cur_t = {}, t
            elif line.startswith(" -4"):
                name = line.split()[1]
                block = pending.setdefault(name, {})
            elif line.startswith(" -3"):
                block = None
            elif block is not None and line.startswith(" -1"):
                body = line.rstrip("\n")
                n = (len(body) - 13) // 12
                block[int(body[3:13])] = [float(body[13 + 12 * k:25 + 12 * k])
                                          for k in range(n)]
    flush()
    dedup: dict[float, float] = {}
    for t, u in path_pts:
        dedup[t] = u
    return keep_t, keep, sorted(dedup.items())


def ccx_errors(d: Path) -> list[str]:
    try:
        return [ln.strip() for ln in (Path(d) / "ccx_errors.txt").read_text().splitlines()
                if ln.strip()]
    except OSError:
        return []
