"""E33: a pure bbox view of the whole assembly, so an agent sees overlaps, stray parts and
floating parts after any change. The inputs are each part's bounding box (from its metrics.json)
and the solved world transforms. The bbox overlaps are only a PREFILTER: assembly/review.py runs
the OCCT boolean common (assembly_check's worker) on the assembly when any pair's boxes overlap
and replaces them with the real interference volumes.

A health report is {ok, overlaps, stray, floating, extents, summary}:
- overlaps: pairs whose world AABBs intersect by more than a tolerance (likely collisions);
- stray:   instances whose centre is far outside the cluster and that touch no other part
  (a part left beside the model);
- floating: instances with no joint or contact connecting them to the rest (nothing holds
  them in place); two parts whose boxes touch (within CONTACT_GAP_MM) count as in contact, so a
  static assembly of parts that only rest on each other is not floating (FWC M9);
- extents: the whole assembly's world AABB.
"""
from __future__ import annotations

import statistics

# a part sits beyond the cluster when its centre is more than this many median spacings away
STRAY_K = 3.0
# AABB overlaps smaller than this (mm^3) are ignore-worthy numerical touching, not a clash
OVERLAP_TOL_MM3 = 1.0
# two world boxes this close (mm, on every axis) touch: a contact that holds a part in place
CONTACT_GAP_MM = 0.5


def _corners(bbox: dict) -> list[list[float]]:
    lo, hi = bbox["min"], bbox["max"]
    return [[lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1], lo[2] if i & 4 else hi[2]]
            for i in range(8)]


def _apply(m: list[list[float]], p: list[float]) -> list[float]:
    return [m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3] for r in range(3)]


def world_aabb(bbox: dict, transform: list[list[float]]) -> tuple[list[float], list[float]]:
    """The axis-aligned world box that contains a part's (possibly rotated) bbox."""
    pts = [_apply(transform, c) for c in _corners(bbox)]
    lo = [min(p[a] for p in pts) for a in range(3)]
    hi = [max(p[a] for p in pts) for a in range(3)]
    return lo, hi


def _overlap_volume(a: tuple[list[float], list[float]],
                    b: tuple[list[float], list[float]]) -> float:
    vol = 1.0
    for ax in range(3):
        lo = max(a[0][ax], b[0][ax])
        hi = min(a[1][ax], b[1][ax])
        if hi <= lo:
            return 0.0
        vol *= hi - lo
    return vol


def _touch(a: tuple[list[float], list[float]], b: tuple[list[float], list[float]],
           gap: float) -> bool:
    return all(a[0][ax] <= b[1][ax] + gap and b[0][ax] <= a[1][ax] + gap for ax in range(3))


def _grounded_component(instance_ids: list[str], joints: list,
                        contacts: list[tuple[str, str]] = ()) -> set[str]:
    """The ids reachable through joints or contacts from the base (the first instance):
    everything the assembly's joints and touching parts hold together."""
    if not instance_ids:
        return set()
    adj: dict[str, set[str]] = {i: set() for i in instance_ids}
    pairs = [(getattr(j.a, "instance_id", None), getattr(j.b, "instance_id", None))
             for j in joints] + list(contacts)
    for a, b in pairs:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    seen, stack = set(), [instance_ids[0]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj[cur] - seen)
    return seen


def health(instances: list, transforms: dict[str, list[list[float]]],
           bboxes: dict[str, dict], *, stray_k: float = STRAY_K,
           overlap_tol_mm3: float = OVERLAP_TOL_MM3, joints: list | None = None,
           contact_gap_mm: float = CONTACT_GAP_MM) -> dict:
    """A bbox-only health report for the placed instances. `bboxes` maps part_id -> its
    metrics bbox ({min,max,size}); instances with no bbox or no solved transform are skipped
    (not yet built), but still counted as floating when no joint holds them."""
    names = {i.id: i.name for i in instances}
    boxes: dict[str, tuple[list[float], list[float]]] = {}
    centers: dict[str, list[float]] = {}
    for inst in instances:
        bbox = bboxes.get(inst.part_id)
        tf = transforms.get(inst.id)
        if not bbox or tf is None:
            continue
        lo, hi = world_aabb(bbox, tf)
        boxes[inst.id] = (lo, hi)
        centers[inst.id] = [(lo[a] + hi[a]) / 2 for a in range(3)]

    ids = list(boxes)
    overlaps = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            vol = _overlap_volume(boxes[a], boxes[b])
            if vol > overlap_tol_mm3:
                overlaps.append({"a": names.get(a, a), "b": names.get(b, b),
                                 "a_id": a, "b_id": b, "volume_mm3": round(vol, 3)})

    contacts = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]
                if _touch(boxes[a], boxes[b], contact_gap_mm)]
    touching = {x for pair in contacts for x in pair}
    stray = []
    if len(centers) >= 3:
        cluster = [statistics.median(c[a] for c in centers.values()) for a in range(3)]
        dists = {i: _dist(c, cluster) for i, c in centers.items()}
        spacing = statistics.median([d for d in dists.values() if d > 0] or [0.0])
        if spacing > 0:
            for i, d in dists.items():
                # far from the median but touching a neighbour is a long assembly (a rocket's
                # nose 600 mm above a gimbal-heavy tail), not a part left beside the model
                if d > stray_k * spacing and i not in touching:
                    stray.append({"instance": names.get(i, i), "instance_id": i,
                                  "distance_mm": round(d, 2)})

    grounded = _grounded_component([i.id for i in instances], joints or [], contacts)
    floating = [{"instance": names.get(i.id, i.id), "instance_id": i.id}
                for i in instances[1:] if i.id not in grounded]

    extents = None
    if boxes:
        lo = [min(b[0][a] for b in boxes.values()) for a in range(3)]
        hi = [max(b[1][a] for b in boxes.values()) for a in range(3)]
        extents = {"min": [round(v, 2) for v in lo], "max": [round(v, 2) for v in hi],
                   "size": [round(hi[a] - lo[a], 2) for a in range(3)]}

    return finalize({"overlaps": overlaps, "stray": stray, "floating": floating,
                     "extents": extents, "overlap_method": "bbox"})


def finalize(report: dict) -> dict:
    """(Re)compute `ok` and `summary` from the report's lists (after OCCT replaced the bbox
    overlap candidates with real interferences, E33)."""
    overlaps, stray, floating = report["overlaps"], report["stray"], report["floating"]
    ok = not overlaps and not stray and not floating
    parts = []
    if overlaps:
        parts.append(f"{len(overlaps)} overlapping pair(s)")
    if stray:
        parts.append(f"{len(stray)} stray part(s)")
    if floating:
        parts.append(f"{len(floating)} floating part(s)")
    summary = "assembly health: clean" if ok else "assembly health: " + ", ".join(parts)
    return {**report, "ok": ok, "summary": summary}


def _dist(a: list[float], b: list[float]) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def warnings_from(report: dict) -> list[str]:
    """Short lines for a tool result's `warnings` when the assembly is not clean (E33)."""
    out = []
    for o in report["overlaps"]:
        out.append(f"{o['a']} overlaps {o['b']} (~{o['volume_mm3']} mm^3); mate or move them "
                   "apart, then assembly_check")
    for s in report["stray"]:
        out.append(f"{s['instance']} sits {s['distance_mm']} mm from the rest; mate it where it "
                   "belongs")
    for f in report["floating"]:
        out.append(f"{f['instance']} has no joint or contact holding it; add a joint")
    return out
