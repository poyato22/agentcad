"""Assembly (build) targets for static and wind studies: a sim_run with project_id instead of
part_id. A wind study runs on the placed parts' surfaces (assembly_tris).

The build's assembly is solved by C2 (mises.assembly.solve: every instance's world transform
from its fixed instances and joints). Each instance's part STEP is copied into the study's job
dir and the mesher places it by that transform; every instance is meshed as its own solid, so
instances only interact through the study's contact / bonded pairs (joints are kinematics, not
load paths). Faces are named per instance, "f3@<instance id>"; presets ("bottom", ...) refer to
the whole assembly's bounding box.

Records live in <home>/assemblies/<project_id>/sims/<sim_id>/ (next to <home>/parts); a record
is stale once any instance's part geometry changed since (instance moves are not tracked).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path

import numpy as np

from ..cad import safe_io
from ..types import ErrorCode, MisesError

ASSEMBLIES_DIR = "assemblies"
STUDY_TYPES = ("static", "wind")     # what a build can be studied by as a whole (POST /sims)


def assemblies_root(parts_root: Path) -> Path:
    """<home>/assemblies: the sibling of the (real) parts dir. A run's workdir reaches the
    shared parts dir through a link, so the link is resolved first."""
    return Path(parts_root).resolve().parent / ASSEMBLIES_DIR


def _apply(T: list[list[float]], p) -> list[float]:
    v = np.array(T, dtype=float) @ np.array([*p, 1.0])
    return [float(round(x, 6)) for x in v[:3]]


def _rotate(T: list[list[float]], n) -> list[float] | None:
    if n is None:
        return None
    v = np.array(T, dtype=float)[:3, :3] @ np.array(n, dtype=float)
    return [float(round(x, 6)) for x in v]


def resolve(ctx, project_id: str) -> dict:
    """{project_id, instances: [{id, part_id, name, transform}], metrics} of a build's
    assembly. The metrics merge every instance's part metrics in world coordinates: bbox,
    volume and face_list with "f<i>@<instance id>" ids. Raises MisesError."""
    from ..assembly.geometry import face_resolver
    from ..assembly.solve import solve
    from ..assembly.tools import _resolve_build, _store
    pid = _resolve_build(ctx, project_id)
    asm = _store(ctx).get(pid)
    if not asm.instances:
        raise MisesError(ErrorCode.NOT_FOUND, f"build {pid!r} has no assembly instances",
                         hint="place parts with assembly_add_instance first")
    parts_root = Path(ctx.workdir) / "parts"
    res = solve(asm, resolve_face=face_resolver(parts_root))
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    faces, instances, volume = [], [], 0.0
    for inst in asm.instances:
        d = parts_root / inst.part_id
        try:
            m = safe_io.read_json(d, "metrics.json")
            built = safe_io.exists(d, "model.step")
        except (OSError, ValueError, safe_io.UnsafePath):
            m, built = None, False
        if not isinstance(m, dict) or not built:
            raise MisesError(ErrorCode.NOT_FOUND, f"part {inst.part_id!r} of instance "
                             f"{inst.name or inst.id} is not built",
                             hint="build every part of the assembly first")
        T = res.transforms[inst.id]
        bb = m.get("bbox") or {}
        if bb.get("min") and bb.get("max"):
            for c in [[x, y, z] for x in (bb["min"][0], bb["max"][0])
                      for y in (bb["min"][1], bb["max"][1])
                      for z in (bb["min"][2], bb["max"][2])]:
                w = np.array(_apply(T, c))
                lo, hi = np.minimum(lo, w), np.maximum(hi, w)
        volume += float(m.get("volume_mm3") or 0.0)
        for f in m.get("face_list") or []:
            if not isinstance(f, dict) or not isinstance(f.get("id"), str):
                continue
            faces.append({**f, "id": f"{f['id']}@{inst.id}", "instance": inst.id,
                          "center": _apply(T, f.get("center") or [0, 0, 0]),
                          "normal": _rotate(T, f.get("normal"))})
        instances.append({"id": inst.id, "part_id": inst.part_id, "name": inst.name,
                          "transform": T})
    if not np.all(np.isfinite(lo)):
        raise MisesError(ErrorCode.NOT_FOUND, "the assembly's parts have no bounding box",
                         hint="rebuild the parts")
    bbox = {"min": lo.tolist(), "max": hi.tolist(), "size": (hi - lo).tolist()}
    return {"project_id": pid, "instances": instances,
            "metrics": {"bbox": bbox, "volume_mm3": volume, "face_list": faces}}


@contextlib.asynccontextmanager
async def locked(parts_root: Path, part_ids):
    """Every instance's part lock at once (sorted: no lock-order deadlock)."""
    from ..cad.tools import _lock
    async with contextlib.AsyncExitStack() as stack:
        for p in sorted(set(part_ids)):
            await stack.enter_async_context(_lock(Path(parts_root) / p))
        yield


def snapshot(parts_root: Path, target: dict, job_root: Path) -> dict:
    """Copy each part's model.step into job_root/inputs (read safely, links never followed);
    returns {instances (for the mesher), part_hashes, geometry_hash}. Call under locked()."""
    inputs = Path(job_root) / "inputs"
    hashes: dict[str, str] = {}
    mesh_instances = []
    for inst in target["instances"]:
        pid = inst["part_id"]
        if pid not in hashes:
            step = safe_io.copy_out(Path(parts_root) / pid, "model.step", inputs, f"{pid}.step")
            if step is None:
                raise MisesError(ErrorCode.NOT_FOUND, f"{pid} has no model.step",
                                 hint="rebuild the part")
            hashes[pid] = hashlib.sha256(step.read_bytes()).hexdigest()
            safe_io.copy_out(Path(parts_root) / pid, "model.stl", inputs, f"{pid}.stl")
        mesh_instances.append({"id": inst["id"], "step": str((inputs / f"{pid}.step").resolve()),
                               "transform": inst["transform"]})
    geometry_hash = hashlib.sha256(json.dumps(
        {"parts": hashes, "transforms": {i["id"]: i["transform"] for i in target["instances"]}},
        sort_keys=True).encode()).hexdigest()
    return {"instances": mesh_instances, "part_hashes": hashes, "geometry_hash": geometry_hash}


def assembly_tris(instances: list[dict]) -> tuple[np.ndarray, list[int]]:
    """Every instance's triangles in build coordinates (from snapshot()'s STL copies) and how
    many belong to each closed shell, for a wind study. Raises ValueError when one has no STL."""
    from .cfd.setup import read_stl
    out, counts = [], []
    for inst in instances:
        stl = Path(inst["step"]).with_suffix(".stl")
        if not stl.is_file():
            raise ValueError(f"instance {inst['id']} has no STL to voxelize")
        tris = read_stl(stl)
        T = np.array(inst["transform"], dtype=float)
        out.append(tris @ T[:3, :3].T + T[:3, 3])
        counts.append(len(tris))
    return np.concatenate(out), counts


def node_instances(mesh) -> dict[int, str]:
    """node id -> instance id of an assembly mesh (from the mesher's per-instance tet runs)."""
    out: dict[int, str] = {}
    i = 0
    for inst, n in mesh.extra.get("tet_instances") or []:
        for t in mesh.tets[i:i + n]:
            for node in t:
                out[node] = inst
        i += n
    return out


def side_instance(side) -> str | None:
    """The instance a contact side belongs to: its instance, or its "f3@<id>" face's."""
    if side.instance is not None:
        return side.instance
    return side.face.split("@", 1)[1] if side.face and "@" in side.face else None


def check_connected(mesh, setup, nl, instances: list[dict]) -> list[str]:
    """Every instance must be held: by a support on it, or through bonded / contact pairs to an
    instance that is. Raises SimError for a floating instance; returns warnings (an instance
    held only through frictionless contact can still slide)."""
    from .types import SimError
    owner = node_instances(mesh)
    held = {owner[n] for n in setup.supported if n in owner}
    edges: list[tuple[str, str, str, float]] = []
    for c in (nl.contacts if nl is not None else []):
        a, b = side_instance(c.a), side_instance(c.b)
        if a is None or b is None:
            continue
        edges.append((a, b, c.kind, c.friction))
    for rs in (nl.rigid_supports if nl is not None else []):
        # a rigid support holds the instance(s) its face belongs to
        held |= {owner[n] for n in mesh.nodes_of(setup.tags.get(rs.face, [])) if n in owner}
    reach = set(held)
    grew = True
    while grew:
        grew = False
        for a, b, *_ in edges:
            for x, y in ((a, b), (b, a)):
                if x in reach and y not in reach:
                    reach.add(y)
                    grew = True
    names = {i["id"]: i.get("name") or i["id"] for i in instances}
    floating = [i["id"] for i in instances if i["id"] not in reach]
    if floating:
        raise SimError("bad_setup", "instance(s) " + ", ".join(
            f"{names[i]} ({i})" for i in floating) + " are neither supported nor connected "
            "to a supported instance", hint="add a support on them, or a bonded / contact pair "
                                            "(nonlinear.contacts) to a supported instance")
    warn = [f"{names[b]} is held only through frictionless contact with {names[a]}; it may "
            "slide or the solve may not converge (add friction or a bonded pair)"
            for a, b, kind, mu in edges if kind == "contact" and mu == 0
            and (b not in held or a not in held)]
    return warn
