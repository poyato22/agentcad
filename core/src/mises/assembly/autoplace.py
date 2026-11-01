"""Plan 9 D10: a part an agent builds lands in its build's assembly by itself.

`auto_place` adds one free instance of a newly built part, placed:

1. in the layout skeleton's frame when the part was built against the build's skeleton (it read
   a skeleton name, so its own coordinates already are the layout's: identity), or at a skeleton
   datum named after the part (`<part_id>`, `slot_<part_id>` or `<part_id>_slot`; point or plane
   origin) — the skeleton's "slot" for it;
2. else near what it belongs to (E33): the nearest free spot (no box overlap, a small fixed gap)
   around its related parts already placed (`related`: its tree parent, siblings and children —
   a mech or catalog child lands by its parent), or around the assembly's centre (the origin
   cluster) when none is placed; standing on that anchor's floor (min Z). Never an ever-growing
   +X stack: the search is outward from the anchor in rings, nearest first.

The first instance of an empty assembly is grounded (`fixed`), as `assembly_add_part` does.
A part the assembly already places is left alone (None). Pure bookkeeping: never builds.
"""
from __future__ import annotations

import math
from pathlib import Path

from ..cad import safe_io, skeleton

from .model import AssemblyStore, Instance
from .solve import transform_point, translate

#: the clearance between a placed part and every other placed box
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


#: the free-spot search: candidate centres on a grid of half the part's footprint (plus the gap),
#: up to this many steps out from the anchor's centre on each axis
SEARCH_STEPS = 24


def _instance_boxes(parts_root: Path, asm) -> dict[str, tuple[list[float], list[float]]]:
    """Every built instance's world box (part bbox through its solved pose), by instance id."""
    from .pose import world_poses                          # the one pose (P11 F4)
    poses, _err = world_poses(asm, parts_root)
    out = {}
    for inst in asm.instances:
        bb = _bbox(parts_root, inst.part_id)
        if bb is None:
            continue
        t = poses.get(inst.id) or inst.transform
        pts = [transform_point(t, [x, y, z]) for x in (bb[0][0], bb[1][0])
               for y in (bb[0][1], bb[1][1]) for z in (bb[0][2], bb[1][2])]
        out[inst.id] = ([min(p[a] for p in pts) for a in range(3)],
                        [max(p[a] for p in pts) for a in range(3)])
    return out


def _union(boxes) -> tuple[list[float], list[float]]:
    boxes = list(boxes)
    return ([min(b[0][a] for b in boxes) for a in range(3)],
            [max(b[1][a] for b in boxes) for a in range(3)])


def _clashes(lo, hi, boxes, gap: float) -> bool:
    for blo, bhi in boxes:
        if all(lo[a] < bhi[a] + gap and hi[a] > blo[a] - gap for a in range(3)):
            return True
    return False


def _near(parts_root: Path, asm, part_id: str,
          related: tuple[str, ...] | list[str] = ()) -> list[list[float]]:
    """The nearest free spot around the anchor (related parts placed, else the whole assembly):
    the part's centre on a ring search outward from the anchor's centre, standing on the
    anchor's floor, clear of every placed box by MIN_GAP_MM. Bounded: when the search finds no
    spot, it goes just past the anchor's +X side (a fixed gap, never a share of the box)."""
    boxes = _instance_boxes(parts_root, asm)
    own = _bbox(parts_root, part_id)
    if not boxes or own is None:
        return translate([0.0, 0.0, 0.0])
    rel = set(related)
    anchored = [b for i, b in boxes.items()
                if next((x.part_id for x in asm.instances if x.id == i), None) in rel]
    alo, ahi = _union(anchored or boxes.values())
    plo, phi = own
    size = [phi[a] - plo[a] for a in range(3)]
    pc = [(plo[a] + phi[a]) / 2 for a in range(2)]
    target = [(alo[a] + ahi[a]) / 2 for a in range(2)]
    dz = alo[2] - plo[2]
    step = [max(1.0, (size[a] + MIN_GAP_MM) / 2) for a in range(2)]
    cands = []
    for i in range(-SEARCH_STEPS, SEARCH_STEPS + 1):
        for j in range(-SEARCH_STEPS, SEARCH_STEPS + 1):
            x, y = target[0] + i * step[0], target[1] + j * step[1]
            ang = math.atan2(j, i) % (2 * math.pi)       # +X first on a tie, then CCW
            cands.append((round(math.hypot(x - target[0], y - target[1]), 6), ang, x, y))
    cands.sort()
    placed = list(boxes.values())
    for _d, _a, x, y in cands:
        dx, dy = x - pc[0], y - pc[1]
        lo = [plo[0] + dx, plo[1] + dy, plo[2] + dz]
        hi = [phi[0] + dx, phi[1] + dy, phi[2] + dz]
        if not _clashes(lo, hi, placed, MIN_GAP_MM - 1e-6):
            return translate([round(dx, 6), round(dy, 6), round(dz, 6)])
    lo, hi = _union(placed)
    return translate([round(hi[0] + MIN_GAP_MM - plo[0], 6),
                      round(target[1] - pc[1], 6), round(dz, 6)])


def related_parts(store, part_id: str) -> tuple[str, ...]:
    """The parts a new part belongs with (its tree parent, siblings and children), for
    auto_place's anchor. () when the store has no row for it. Never raises."""
    try:
        row = store.get_part(part_id)
        if row is None:
            return ()
        rows = store.list_part_rows(row.project_id)
    except Exception:  # noqa: BLE001
        return ()
    out = [r.id for r in rows if r.id != part_id and (
        r.id == row.parent_id or r.parent_id == part_id
        or (row.parent_id is not None and r.parent_id == row.parent_id))]
    return tuple(out)


def _free_name(asm, base: str) -> str:
    names = {i.name for i in asm.instances}
    name, n = base, 2
    while name in names:
        name, n = f"{base} {n}", n + 1
    return name


def auto_place(assembly: AssemblyStore, parts_root: Path, project_id: str, part_id: str,
               name: str | None = None, attempts: int = 3,
               related: tuple[str, ...] | list[str] = ()) -> Instance | None:
    """Add a placed instance of `part_id` to the build's assembly (see the module doc). None
    when the assembly places that part already. The placement (which solves the assembly) is
    worked out without the store's lock, on a snapshot; the write happens under the lock only if
    the assembly is still that snapshot, else it is worked out again. Call it off the event loop."""
    for _ in range(max(1, attempts)):
        asm = assembly.get(project_id)
        if any(i.part_id == part_id for i in asm.instances):
            return None
        inst = _plan(parts_root, project_id, part_id, name, asm, related)
        with assembly._lock:
            now = assembly.get(project_id)
            if any(i.part_id == part_id for i in now.instances):
                return None
            if now.model_dump() != asm.model_dump():
                continue                                  # something moved meanwhile: again
            assembly.add_instance(project_id, inst)
            assembly.mark_auto(project_id, inst.id)
            return inst
    with assembly._lock:                                  # a busy assembly: hold it this once
        asm = assembly.get(project_id)
        if any(i.part_id == part_id for i in asm.instances):
            return None
        inst = _plan(parts_root, project_id, part_id, name, asm, related)
        assembly.add_instance(project_id, inst)
        assembly.mark_auto(project_id, inst.id)
        return inst


def _plan(parts_root: Path, project_id: str, part_id: str, name: str | None, asm,
          related: tuple[str, ...] | list[str] = ()) -> Instance:
    transform = _skeleton_slot(parts_root, project_id, part_id)
    if transform is None:
        transform = (_near(parts_root, asm, part_id, related) if asm.instances
                     else translate([0, 0, 0]))
    return Instance(part_id=part_id, name=_free_name(asm, name or part_id),
                    transform=transform, fixed=not asm.instances)
