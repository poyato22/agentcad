"""Meshing (gmsh, in a child process) and face selection on the mesh."""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .environments import FACE_PRESETS
from .proc import run_sandboxed
from .types import SimError

WORKER = Path(__file__).resolve().parent / "mesh_worker.py"
FACE_ID_RE = re.compile(r"^f(\d{1,5})$")
# a named face the part script tags (FACES = {"motor_mount": fn} in part.py): re-resolved on every
# build, so unlike a face id it holds across a study's variants
FACE_TAG_RE = re.compile(r"^tag:([a-z][a-z0-9_]{0,30})$")
# an assembly study's instance face: face f3 of instance <id> ("f3@1a2b3c4d5e6f")
INSTANCE_FACE_RE = re.compile(r"^f(\d{1,5})@([A-Za-z0-9_-]{1,40})$")


def is_face_spec(v: str) -> bool:
    """A preset ("bottom", "x-max", ...), a face id ("f3"), a tag ("tag:motor_mount") or an
    assembly instance's face ("f3@<instance id>")."""
    return (v in FACE_PRESETS or bool(FACE_ID_RE.match(v)) or bool(FACE_TAG_RE.match(v))
            or bool(INSTANCE_FACE_RE.match(v)))


def is_face_id(v: str) -> bool:
    """A face id looked up in the metrics face_list: "f3", or "f3@<instance>" (assemblies)."""
    return bool(FACE_ID_RE.match(v)) or bool(INSTANCE_FACE_RE.match(v))


def tag_face_ids(spec: str, face_tags: dict | None) -> list[str]:
    """The face ids a "tag:<name>" spec names in this build's metrics (validated: only ids)."""
    name = FACE_TAG_RE.match(spec).group(1)
    ids = (face_tags or {}).get(name)
    if not isinstance(ids, list) or not ids:
        known = ", ".join(sorted(k for k in (face_tags or {}) if isinstance(k, str))) or "none"
        raise SimError("bad_setup", f"this part has no face tagged {name!r}",
                       hint=f"tag it in part.py: FACES = {{\"{name}\": lambda p: ...}}; "
                            f"tags now: {known}")
    return [i for i in ids if isinstance(i, str) and FACE_ID_RE.match(i)]
MAX_FINE_NODES = 200_000      # memory/time budget for the fine mesh on a laptop
_NODES_PER_H3 = 10.5          # measured: quadratic tets give ~10.5 nodes per h^3 of volume


@dataclass
class Surface:
    tag: int
    center: list[float]
    bbox: list[float]          # xmin, ymin, zmin, xmax, ymax, zmax
    area: float
    tris: list[list[int]]      # 6-node triangles: 3 corners then 3 midside nodes
    instance: str | None = None  # assembly studies: the instance whose solid it bounds


@dataclass
class Mesh:
    nodes: dict[int, tuple[float, float, float]]
    tets: list[list[int]]      # gmsh 10-node order (4-node for a first-order mesh)
    surfaces: list[Surface]
    size_mm: float = 0.0
    gmsh_version: str = ""
    extra: dict = field(default_factory=dict)
    order: int = 2

    def surface(self, tag: int) -> Surface:
        return next(s for s in self.surfaces if s.tag == tag)

    def nodes_of(self, tags: list[int]) -> set[int]:
        return {n for t in tags for tri in self.surface(t).tris for n in tri}


def mesh_sizes(bbox_size: list[float], volume_mm3: float) -> tuple[float, float]:
    """(coarse, fine) element sizes in mm: fine is half the coarse size, and the fine mesh is
    kept within MAX_FINE_NODES (estimated from the volume)."""
    dims = sorted(float(d) for d in bbox_size if d > 0) or [1.0]
    coarse = min(dims[-1] / 12, dims[0] / 2)
    fine = coarse / 2
    floor = (_NODES_PER_H3 * max(volume_mm3, 1e-9) / MAX_FINE_NODES) ** (1 / 3)
    fine = max(fine, floor)
    return 2 * fine, fine


def size_floor(volume_mm3: float) -> float:
    """The smallest element size whose quadratic mesh stays within MAX_FINE_NODES."""
    return (_NODES_PER_H3 * max(volume_mm3, 1e-9) / MAX_FINE_NODES) ** (1 / 3)


def load_mesh(path: Path) -> Mesh:
    d = json.loads(Path(path).read_text())
    c = d["coords"]
    nodes = {t: (c[3 * i], c[3 * i + 1], c[3 * i + 2]) for i, t in enumerate(d["node_tags"])}
    order = int(d.get("order", 2))
    nt, nf = (10, 6) if order == 2 else (4, 3)
    tt = d["tets"]
    tets = [tt[i:i + nt] for i in range(0, len(tt), nt)]
    surfaces = [Surface(tag=s["tag"], center=s["center"], bbox=s["bbox"], area=s["area"],
                        tris=[s["tris"][i:i + nf] for i in range(0, len(s["tris"]), nf)],
                        instance=s.get("instance"))
                for s in d["surfaces"]]
    mesh = Mesh(nodes=nodes, tets=tets, surfaces=surfaces, size_mm=d["size_mm"],
                gmsh_version=d.get("gmsh_version", ""), order=order)
    if d.get("tet_instances"):  # assembly: [instance id, tet count] runs in element order
        mesh.extra["tet_instances"] = [(str(i), int(n)) for i, n in d["tet_instances"]]
    return mesh


async def mesh_step(step: Path, job_dir: Path, size_mm: float, *, timeout_s: float,
                    options: dict | None = None) -> Mesh:
    """options: {"order": 1|2, "refine": [...], "instances": [...]} (see mesh_worker). With
    instances (an assembly study) `step` is only the directory anchor: each instance names its
    own STEP copy inside the job root."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    out = job_dir / "mesh.json"
    argv = [sys.executable, "-I", str(WORKER), str(Path(step).resolve()), str(out.resolve()),
            repr(size_mm)]
    if options:
        opt = job_dir / "mesh_options.json"
        opt.write_text(json.dumps(options))
        argv.append(str(opt.resolve()))
    # the model.step the mesher reads sits in the sim job root, a level above job_dir
    rc, _, err = await run_sandboxed(argv, job_dir, timeout_s=timeout_s, what="gmsh meshing",
                                     read_dirs=[Path(step).resolve().parent])
    if rc != 0 or not out.exists():
        msg = next((ln for ln in err.splitlines() if ln.startswith("MESH_ERROR")), err.strip())
        raise SimError("mesh_failed", f"meshing failed: {msg[-800:] or f'exit {rc}'}",
                       hint="the part must be a single valid solid; try mirror_measure valid=true")
    return await asyncio.to_thread(load_mesh, out)


def _tol(part_bbox: dict) -> float:
    return 1e-4 * math.dist(part_bbox["min"], part_bbox["max"]) + 1e-6


def select_faces(spec: str, surfaces: list[Surface], part_bbox: dict,
                 face_list: list[dict] | None, face_tags: dict | None = None) -> list[int]:
    """Surface tags for a preset ("bottom", "x-max", ...), a face id ("f3") from the part's
    metrics face_list (matched by centre of mass), or a named face ("tag:motor_mount") the part
    script tagged, resolved through its face ids."""
    if FACE_TAG_RE.match(spec):
        out: list[int] = []
        for fid in tag_face_ids(spec, face_tags):
            for t in select_faces(fid, surfaces, part_bbox, face_list):
                if t not in out:
                    out.append(t)
        return out
    tol = _tol(part_bbox)
    if spec in FACE_PRESETS:
        axis, side = FACE_PRESETS[spec]
        target = part_bbox[side][axis]
        tags = [s.tag for s in surfaces
                if abs(s.bbox[axis + 3] - s.bbox[axis]) <= tol
                and abs(s.bbox[axis] - target) <= tol]
        if not tags:
            raise SimError("bad_setup", f"no flat face on the {spec} side of the part",
                           hint="pick a face_id (e.g. \"f3\") from mirror_measure face_list")
        return tags
    m = FACE_ID_RE.match(spec) or INSTANCE_FACE_RE.match(spec)
    if not m:
        raise SimError("bad_setup", f"unknown face {spec!r}",
                       hint=f"use a preset ({', '.join(FACE_PRESETS)}) or a face id like \"f3\"")
    if not face_list:
        raise SimError("bad_setup", "this part has no face ids yet",
                       hint="rebuild it (mirror_part_set_vars with its current vars) so "
                            "mirror_measure lists face_list, or use a preset")
    face = next((f for f in face_list if f.get("id") == spec), None)
    if face is None:
        raise SimError("bad_setup", f"no face {spec} on this part",
                       hint=f"face ids run f0..f{len(face_list) - 1}; see mirror_measure")
    inst = INSTANCE_FACE_RE.match(spec)
    if inst is not None:  # only that instance's surfaces: touching instances share centres
        surfaces = [s for s in surfaces if s.instance == inst.group(2)]
    best = min(surfaces, key=lambda s: math.dist(s.center, face["center"]), default=None)
    match_tol = max(1e2 * tol, 1e-3)
    if best is None or math.dist(best.center, face["center"]) > match_tol:
        raise SimError("bad_setup", f"face {spec} not found in the mesh",
                       hint="rebuild the part so its face ids match the current geometry")
    return [best.tag]


def _area(p0, p1, p2) -> float:
    u = [p1[i] - p0[i] for i in range(3)]
    v = [p2[i] - p0[i] for i in range(3)]
    c = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    return 0.5 * math.sqrt(c[0] ** 2 + c[1] ** 2 + c[2] ** 2)


def consistent_loads(mesh: Mesh, tags: list[int], force: tuple[float, float, float],
                     weight=None) -> dict[int, tuple[float, float, float]]:
    """Spread a total force over the surfaces as consistent nodal loads: for a quadratic
    (6-node) triangle under uniform traction the corner nodes get 0 and each midside node A/3
    of the traction (a linear 3-node triangle: each corner A/3). `weight(tri) -> float >= 0`
    scales each triangle's share (e.g. a cosine bearing distribution); default uniform."""
    tris = [tri for t in tags for tri in mesh.surface(t).tris]
    areas = [_area(*(mesh.nodes[n] for n in tri[:3])) for tri in tris]
    if weight is not None:
        areas = [a * max(0.0, weight(tri)) for tri, a in zip(tris, areas)]
    total = sum(areas)
    if total <= 0:
        raise SimError("bad_setup", "the load face has no area in the mesh")
    loads: dict[int, list[float]] = {}
    for tri, a in zip(tris, areas):
        share = a / total / 3
        for n in (tri[3:] if len(tri) == 6 else tri[:3]):
            acc = loads.setdefault(n, [0.0, 0.0, 0.0])
            for i in range(3):
                acc[i] += force[i] * share
    return {n: (v[0], v[1], v[2]) for n, v in loads.items()}
