"""Plan 9 D10: a part an agent builds lands in its build's assembly by itself.

`auto_place` adds one free instance of a newly built part, placed:

1. in the layout skeleton's frame when the part was built against the build's skeleton (it read
   a skeleton name, so its own coordinates already are the layout's: identity), or at a skeleton
   datum named after the part (`<part_id>`, `slot_<part_id>` or `<part_id>_slot`; point or plane
   origin) — the skeleton's "slot" for it;
2. else beside what the assembly holds already: past the +X side of the assembly's bounding box
   (solved poses × each part's bbox) with a gap, centred on its Y, standing on its floor (min Z),
   so it never overlaps a placed part.

The first instance of an empty assembly is grounded (`fixed`), as `assembly_add_part` does.
A part the assembly already places is left alone (None). Pure bookkeeping: never builds.
"""
from __future__ import annotations

from pathlib import Path

from ..cad import safe_io, skeleton
from .geometry import face_resolver
from .model import AssemblyStore, Instance
from .solve import solve, transform_point, translate

#: gap between the assembly's box and a part placed beside it: this share of the box's X size,
#: never less than MIN_GAP_MM
GAP_SHARE = 0.1
MIN_GAP_MM = 5.0
_SLOT_NAMES = ("{}", "slot_{}", "{}_slot")


def _bbox(parts_root: Path, part_id: str) -> tuple[list[float], list[float]] | None:
    try:
        m = safe_io.read_json(Path(parts_root) / part_id, "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        return None
    bb = (m or {}).get("bbox") if isinstance(m, dict) else None
    try:
        lo, hi = [float(x) for x in bb["min"]], [float(x) for x in bb["max"]]
    except (TypeError, KeyError, ValueError):
        return None
    return (lo, hi) if len(lo) == 3 and len(hi) == 3 else None


def _world_box(parts_root: Path, asm) -> tuple[list[float], list[float]] | None:
    """The assembly's box: every instance's part bbox through its solved pose."""
    try:
        poses = solve(asm, resolve_face=face_resolver(parts_root)).transforms
    except Exception:  # noqa: BLE001 - a broken joint never stops a placement
        poses = {}
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for inst in asm.instances:
        bb = _bbox(parts_root, inst.part_id)
        if bb is None:
            continue
        t = poses.get(inst.id) or inst.transform
        for x in (bb[0][0], bb[1][0]):
            for y in (bb[0][1], bb[1][1]):
                for z in (bb[0][2], bb[1][2]):
                    w = transform_point(t, [x, y, z])
                    lo = [min(a, b) for a, b in zip(lo, w)]
                    hi = [max(a, b) for a, b in zip(hi, w)]
    return (lo, hi) if lo[0] <= hi[0] else None


def _skeleton_slot(parts_root: Path, project_id: str, part_id: str) -> list[list[float]] | None:
    """Where the layout skeleton puts this part, or None when it says nothing about it."""
    if skeleton.uses_skeleton(skeleton.read_refs(Path(parts_root) / part_id)):
        return translate([0.0, 0.0, 0.0])        # built in the layout's own frame
    data, _ = skeleton.load(parts_root, project_id)
    if data is None:
        return None
    for pattern in _SLOT_NAMES:
        d = data.datums.get(pattern.format(part_id))
        if d is not None and d.kind in ("point", "plane"):
            return translate(d.origin)
    return None


def _beside(parts_root: Path, asm, part_id: str) -> list[list[float]]:
    box = _world_box(parts_root, asm)
    own = _bbox(parts_root, part_id)
    if box is None or own is None:
        return translate([0.0, 0.0, 0.0])
    (lo, hi), (plo, phi) = box, own
    gap = max(MIN_GAP_MM, (hi[0] - lo[0]) * GAP_SHARE)
    dx = hi[0] + gap - plo[0]
    dy = (lo[1] + hi[1]) / 2 - (plo[1] + phi[1]) / 2
    dz = lo[2] - plo[2]
    return translate([round(dx, 6), round(dy, 6), round(dz, 6)])


def _free_name(asm, base: str) -> str:
    names = {i.name for i in asm.instances}
    name, n = base, 2
    while name in names:
        name, n = f"{base} {n}", n + 1
    return name


def auto_place(assembly: AssemblyStore, parts_root: Path, project_id: str, part_id: str,
               name: str | None = None) -> Instance | None:
    """Add a placed instance of `part_id` to the build's assembly (see the module doc). None
    when the assembly places that part already."""
    asm = assembly.get(project_id)
    if any(i.part_id == part_id for i in asm.instances):
        return None
    transform = _skeleton_slot(parts_root, project_id, part_id)
    if transform is None:
        transform = _beside(parts_root, asm, part_id) if asm.instances else translate([0, 0, 0])
    inst = Instance(part_id=part_id, name=_free_name(asm, name or part_id),
                    transform=transform, fixed=not asm.instances)
    return assembly.add_instance(project_id, inst)
