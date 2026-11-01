"""Assembly kinematics: a tree solve from fixed instances through joints.

Pure Python 4x4 row-major matrix math (no build123d), so it is cheap to import and easy to
test. A joint couples a frame on instance ``a`` with a frame on instance ``b``; the solve places
``b`` relative to ``a`` (or the reverse, whichever endpoint is already placed) as

    T_b = T_a . F_a . M(value) . F_b^-1

where ``F_x`` is the frame transform in that instance's part coordinates and ``M`` is the joint
motion in the shared frame (rotation about local Z for revolute, translation along local Z for
slider, both for cylindrical, a 3-angle rotation for ball, identity for fixed).

Loops in the joint graph are over-constrained for this closed-form solve and are rejected.
Closed chains (four-bars, legs, grippers) use loop constraints instead (Plan 10): the tree
places every body, then a scipy least-squares solve adjusts the free joints on each loop's path
until the loop-closing frames meet. Coupled joints (gears, belts, racks, screws) take their
partner's value x ratio (+ offset) before the tree solve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from ..types import ErrorCode, MisesError

Mat = list[list[float]]
Vec = list[float]

# degrees of freedom each joint kind removes from the pair it connects
CONSTRAINTS = {"fixed": 6, "revolute": 5, "slider": 5, "cylindrical": 4, "ball": 3}
# ... and each loop-closure type (a coupling removes 1)
LOOP_CONSTRAINTS = {"revolute": 5, "ball": 3, "fixed": 6}


def identity() -> Mat:
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def matmul(a: Mat, b: Mat) -> Mat:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def translate(v: Vec) -> Mat:
    m = identity()
    m[0][3], m[1][3], m[2][3] = float(v[0]), float(v[1]), float(v[2])
    return m


def _normalize(v: Vec) -> Vec:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        raise MisesError(ErrorCode.INVALID_INPUT, "frame axis is zero-length",
                         hint="give a non-zero axis direction")
    return [c / n for c in v]


def _cross(a: Vec, b: Vec) -> Vec:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def rot_axis(axis: Vec, angle_deg: float) -> Mat:
    """Rotation by ``angle_deg`` about ``axis`` (through the origin), Rodrigues' formula."""
    x, y, z = _normalize(axis)
    t = math.radians(angle_deg)
    c, s, c1 = math.cos(t), math.sin(t), 1.0 - math.cos(t)
    return [
        [c + x * x * c1, x * y * c1 - z * s, x * z * c1 + y * s, 0.0],
        [y * x * c1 + z * s, c + y * y * c1, y * z * c1 - x * s, 0.0],
        [z * x * c1 - y * s, z * y * c1 + x * s, c + z * z * c1, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def frame_matrix(origin: Vec, axis: Vec) -> Mat:
    """A rigid transform whose local Z is ``axis`` and whose translation is ``origin``.

    The local X is chosen deterministically from the axis so two frames built from the same
    (origin, axis) have the same orientation — joints referencing identical frames then line up
    exactly at value 0.
    """
    z = _normalize(axis)
    up = [0.0, 0.0, 1.0] if abs(z[2]) < 0.9 else [1.0, 0.0, 0.0]
    x = _normalize(_cross(up, z))
    y = _cross(z, x)
    return [[x[0], y[0], z[0], float(origin[0])],
            [x[1], y[1], z[1], float(origin[1])],
            [x[2], y[2], z[2], float(origin[2])],
            [0.0, 0.0, 0.0, 1.0]]


def inverse_rigid(m: Mat) -> Mat:
    """Inverse of a rigid (rotation+translation) transform: R^T, -R^T t."""
    r = [[m[i][j] for j in range(3)] for i in range(3)]
    t = [m[0][3], m[1][3], m[2][3]]
    rt = [[r[j][i] for j in range(3)] for i in range(3)]
    nt = [-sum(rt[i][k] * t[k] for k in range(3)) for i in range(3)]
    return [[rt[0][0], rt[0][1], rt[0][2], nt[0]],
            [rt[1][0], rt[1][1], rt[1][2], nt[1]],
            [rt[2][0], rt[2][1], rt[2][2], nt[2]],
            [0.0, 0.0, 0.0, 1.0]]


def transform_point(m: Mat, p: Vec) -> Vec:
    return [sum(m[i][j] * (p[j] if j < 3 else 1.0) for j in range(4)) for i in range(3)]


def clamp_value(value, limits):
    """Clamp a scalar or per-component against ``limits`` = [lo, hi] (or None)."""
    if limits is None:
        return value
    lo, hi = float(limits[0]), float(limits[1])
    if isinstance(value, (list, tuple)):
        return [min(max(float(v), lo), hi) for v in value]
    return min(max(float(value), lo), hi)


def joint_motion(kind: str, value) -> Mat:
    """The motion transform ``M(value)`` in the joint frame.

    revolute: ``value`` deg about local Z. slider: ``value`` mm along local Z.
    cylindrical: ``value`` = [deg, mm]. ball: ``value`` = [rx, ry, rz] deg (intrinsic X then Y
    then Z). fixed: identity.
    """
    z = [0.0, 0.0, 1.0]
    if kind in ("cylindrical", "ball") and not isinstance(value, (list, tuple)):
        value = [float(value)]        # a bare number (the default 0) is the first component
    if kind == "fixed":
        return identity()
    if kind == "revolute":
        return rot_axis(z, float(value))
    if kind == "slider":
        return translate([0.0, 0.0, float(value)])
    if kind == "cylindrical":
        ang, dist = (list(value) + [0.0, 0.0])[:2]
        return matmul(translate([0.0, 0.0, float(dist)]), rot_axis(z, float(ang)))
    if kind == "ball":
        rx, ry, rz = (list(value) + [0.0, 0.0, 0.0])[:3]
        return matmul(matmul(rot_axis([1.0, 0.0, 0.0], float(rx)),
                             rot_axis([0.0, 1.0, 0.0], float(ry))),
                      rot_axis(z, float(rz)))
    raise MisesError(ErrorCode.INVALID_INPUT, f"unknown joint kind {kind!r}")


@dataclass
class SolveResult:
    transforms: dict[str, Mat]
    dof_remaining: int
    # the joint value actually applied after clamping, per joint id
    applied: dict[str, object] = field(default_factory=dict)
    # joint-connected groups holding more than one fixed instance. Rule: a fixed instance keeps
    # its stored transform, so two fixed instances linked by any joint path over-constrain that
    # path; the solve never moves a fixed instance, and the joint closing the path is not
    # applied. Fixed instances with NO joint path between them are independent and fine.
    overconstrained: list[list[str]] = field(default_factory=list)
    # Plan 10: loop id -> how far its two frames still miss (mm); loops that cannot close
    loop_residuals: dict[str, float] = field(default_factory=dict)
    loop_errors: list[dict] = field(default_factory=list)
    # joint values the loop solve or a coupling changed from the stored ones (to store back)
    solved: dict[str, object] = field(default_factory=dict)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        """Join a and b; return False if they were already in the same component (a cycle)."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[ra] = rb
        return True


FaceResolver = Callable[[str, str], tuple[Vec, Vec]]


def _resolve_frame(part_id: str, frame, resolve_face: FaceResolver | None) -> Mat:
    origin = getattr(frame, "origin", None) if not isinstance(frame, dict) else frame.get("origin")
    axis = getattr(frame, "axis", None) if not isinstance(frame, dict) else frame.get("axis")
    face_id = getattr(frame, "face_id", None) if not isinstance(frame, dict) else frame.get("face_id")
    if face_id is not None and (origin is None or axis is None):
        if resolve_face is None:
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"frame references face {face_id!r} but no geometry is available",
                             hint="build the part first, or give origin+axis")
        origin, axis = resolve_face(part_id, face_id)
    if origin is None or axis is None:
        raise MisesError(ErrorCode.INVALID_INPUT, "frame needs origin+axis or a face_id")
    return frame_matrix(list(origin), list(axis))


def _scalar(v) -> float:
    if isinstance(v, (list, tuple)):
        return float(v[0]) if v else 0.0
    return float(v)


def coupling_order(joints) -> list:
    """The coupled joints in an order where every partner comes before its follower. A
    coupling whose partner is missing is skipped (a removed joint); a cycle is an error."""
    by_id = {j.id: j for j in joints}
    out, state = [], {}

    def visit(j) -> None:
        st = state.get(j.id)
        if st == 2:
            return
        if st == 1:
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"joint couplings form a cycle through {j.id}",
                             hint="couple each follower to one driver chain, never back")
        state[j.id] = 1
        partner = by_id.get(j.coupling.partner_joint)
        if partner is not None and getattr(partner, "coupling", None) is not None:
            visit(partner)
        state[j.id] = 2
        if partner is not None:
            out.append(j)

    for j in joints:
        if getattr(j, "coupling", None) is not None:
            visit(j)
    return out


def apply_couplings(order, values: dict[str, object]) -> None:
    """value = offset + ratio x partner value, for every coupled joint (in `order`)."""
    for j in order:
        c = j.coupling
        values[j.id] = float(c.offset) + float(c.ratio) * _scalar(values[c.partner_joint])


# a loop that still misses by more than this (mm, or mm-equivalent for an axis) cannot close
LOOP_TOL_MM = 1e-3
_AXIS_MM = 10.0          # an axis error (radians) counts like this many mm in the residual
_NCOMP = {"revolute": 1, "slider": 1, "cylindrical": 2, "ball": 3, "fixed": 0}


class _Tree:
    """An assembly prepared for repeated tree solves: frames resolved once, so the loop solve
    can re-place every body for each trial set of joint values cheaply."""

    def __init__(self, assembly, resolve_face: FaceResolver | None):
        self.instances = {inst.id: inst for inst in assembly.instances}
        self.joints = list(assembly.joints)
        self.loops = list(getattr(assembly, "loops", None) or [])
        uf = _UnionFind()
        for iid in self.instances:
            uf.add(iid)
        for j in self.joints:
            for ref in (j.a, j.b):
                if ref.instance_id not in self.instances:
                    raise MisesError(ErrorCode.INVALID_INPUT,
                                     f"joint {j.id} references unknown instance "
                                     f"{ref.instance_id!r}")
            if not uf.union(j.a.instance_id, j.b.instance_id):
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"joint {j.id} closes a loop; this solve needs an open joint "
                                 "tree", hint="close the loop with a loop constraint "
                                 "(assembly_add_loop) instead of a joint, remove the redundant "
                                 "joint, or ground one side")
        for lp in self.loops:
            for iid in (lp.joint_a, lp.joint_b):
                if iid not in self.instances:
                    raise MisesError(ErrorCode.INVALID_INPUT,
                                     f"loop {lp.id} references unknown instance {iid!r}")
        self.uf = uf
        self._resolve_face = resolve_face
        self._frames: dict[str, tuple[Mat, Mat]] = {}
        self.loop_frames = {lp.id: (_resolve_frame(self.instances[lp.joint_a].part_id,
                                                   lp.frame_a, resolve_face),
                                    _resolve_frame(self.instances[lp.joint_b].part_id,
                                                   lp.frame_b, resolve_face))
                            for lp in self.loops}
        self.order = coupling_order(self.joints)
        comp: dict[str, list[str]] = {}
        for iid in self.instances:
            comp.setdefault(uf.find(iid), []).append(iid)
        self.components = comp

    def frames(self, j) -> tuple[Mat, Mat]:
        """A joint's two frame matrices, resolved the first time the solve reaches it (a joint
        it never applies, e.g. between two fixed instances, is never resolved)."""
        got = self._frames.get(j.id)
        if got is None:
            got = (_resolve_frame(self.instances[j.a.instance_id].part_id, j.a.frame,
                                  self._resolve_face),
                   _resolve_frame(self.instances[j.b.instance_id].part_id, j.b.frame,
                                  self._resolve_face))
            self._frames[j.id] = got
        return got

    def place(self, values: dict[str, object]) -> tuple[dict[str, Mat], dict[str, object],
                                                         dict[str, str]]:
        """(transforms, applied values, instance -> the joint that placed it)."""
        instances = self.instances
        transforms: dict[str, Mat] = {}
        applied: dict[str, object] = {}
        via: dict[str, str] = {}
        for inst in instances.values():
            if inst.fixed:
                transforms[inst.id] = [row[:] for row in inst.transform]
        for members in self.components.values():
            if not any(instances[m].fixed for m in members):
                anchor = members[0]
                transforms[anchor] = [row[:] for row in instances[anchor].transform]
        pending = list(self.joints)
        progress = True
        while pending and progress:
            progress = False
            still: list = []
            for j in pending:
                a, b = j.a.instance_id, j.b.instance_id
                ta, tb = transforms.get(a), transforms.get(b)
                if ta is None and tb is None:
                    still.append(j)
                    continue
                if ta is not None and tb is not None:
                    # both ends already placed: only possible between two fixed roots (the joint
                    # graph is a tree), i.e. over-constrained. Never move a fixed instance.
                    continue
                value = clamp_value(values.get(j.id, j.value), j.limits)
                applied[j.id] = value
                fa, fb = self.frames(j)
                m = joint_motion(j.kind, value)
                if ta is not None:
                    transforms[b] = matmul(matmul(matmul(ta, fa), m), inverse_rigid(fb))
                    via[b] = j.id
                else:
                    transforms[a] = matmul(matmul(matmul(tb, fb), inverse_rigid(m)),
                                           inverse_rigid(fa))
                    via[a] = j.id
                progress = True
            pending = still
        for inst in instances.values():
            transforms.setdefault(inst.id, [row[:] for row in inst.transform])
        return transforms, applied, via

    def path(self, a: str, b: str) -> list[str]:
        """The tree joints between instances a and b (empty when they are not connected)."""
        adj: dict[str, list[tuple[str, str]]] = {}
        for j in self.joints:
            adj.setdefault(j.a.instance_id, []).append((j.b.instance_id, j.id))
            adj.setdefault(j.b.instance_id, []).append((j.a.instance_id, j.id))
        prev: dict[str, tuple[str, str] | None] = {a: None}
        queue = [a]
        while queue:
            cur = queue.pop(0)
            if cur == b:
                break
            for nxt, jid in adj.get(cur, []):
                if nxt not in prev:
                    prev[nxt] = (cur, jid)
                    queue.append(nxt)
        if b not in prev:
            return []
        out, cur = [], b
        while prev[cur] is not None:
            cur, jid = prev[cur]
            out.append(jid)
        return out[::-1]


def _loop_residual(tree: _Tree, lp, transforms: dict[str, Mat]) -> list[float]:
    fa, fb = tree.loop_frames[lp.id]
    pa = matmul(transforms[lp.joint_a], fa)
    pb = matmul(transforms[lp.joint_b], fb)
    r = [pa[i][3] - pb[i][3] for i in range(3)]
    if lp.type in ("revolute", "fixed"):
        za = [pa[i][2] for i in range(3)]
        zb = [pb[i][2] for i in range(3)]
        r += [c * _AXIS_MM for c in _cross(za, zb)]
    if lp.type == "fixed":
        xa = [pa[i][0] for i in range(3)]
        xb = [pb[i][0] for i in range(3)]
        r += [c * _AXIS_MM for c in _cross(xa, xb)]
    return r


def _solve_loops(tree: _Tree, values: dict[str, object], result: "SolveResult") -> list[str]:
    """Adjust the non-driver joints on each loop's path (scipy least squares, from the current
    values so a mechanism stays on its branch) until every loop closes."""
    by_id = {j.id: j for j in tree.joints}
    coupled = {j.id for j in tree.order}
    held: set[str] = set(coupled)
    free: list[str] = []
    _t, _a, via = tree.place(values)
    depth: dict[str, int] = {}

    def level(iid: str) -> int:
        """How many joints from its anchor the solve places an instance."""
        if iid not in depth:
            jid = via.get(iid)
            if jid is None:
                depth[iid] = 0
            else:
                j = by_id[jid]
                other = j.a.instance_id if j.b.instance_id == iid else j.b.instance_id
                depth[iid] = level(other) + 1
        return depth[iid]

    for lp in tree.loops:
        path = [jid for jid in tree.path(lp.joint_a, lp.joint_b)
                if by_id[jid].kind != "fixed"]
        if not path:
            result.loop_errors.append({"loop_id": lp.id, "residual_mm": None,
                                       "reason": "its two bodies are not joined by a joint "
                                                 "path, so the loop has nothing to adjust"})
            continue
        # default driver: the movable joint on the path nearest the ground (its anchor)
        drivers = lp.drivers if lp.drivers is not None else [min(
            (jid for jid in path if jid not in coupled),
            key=lambda jid: min(level(by_id[jid].a.instance_id), level(by_id[jid].b.instance_id)),
            default=path[0])]
        held.update(drivers)
        free.extend(jid for jid in path if jid not in free)
    # a joint whose limits pin it (lo == hi) is locked: held, never a variable
    held.update(jid for jid in free if by_id[jid].limits is not None
                and by_id[jid].limits[0] >= by_id[jid].limits[1])
    free = [jid for jid in free if jid not in held]
    if not free:
        return []
    import numpy as np
    from scipy.optimize import least_squares

    slots: list[tuple[str, int]] = []
    x0, lo, hi = [], [], []
    for jid in free:
        j = by_id[jid]
        n = _NCOMP[j.kind]
        cur = values.get(jid, j.value)
        comps = list(cur) if isinstance(cur, (list, tuple)) else [float(cur)]
        comps = [float(c) for c in (comps + [0.0] * n)[:n]]
        for k in range(n):
            slots.append((jid, k))
            a, b = (j.limits if j.limits is not None else (-np.inf, np.inf))
            x0.append(min(max(comps[k], a), b))
            lo.append(a)
            hi.append(b)

    def unpack(x) -> dict[str, object]:
        out = dict(values)
        groups: dict[str, list[float]] = {}
        for (jid, _k), v in zip(slots, x):
            groups.setdefault(jid, []).append(float(v))
        for jid, comps in groups.items():
            out[jid] = comps[0] if len(comps) == 1 else comps
        apply_couplings(tree.order, out)
        return out

    def residual(x):
        tr, _applied, _via = tree.place(unpack(x))
        r: list[float] = []
        for lp in tree.loops:
            r += _loop_residual(tree, lp, tr)
        return np.array(r)

    sol = least_squares(residual, np.array(x0, dtype=float), bounds=(lo, hi), method="trf",
                        xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=100 * (len(x0) + 1))
    solved = unpack(sol.x)
    for jid in free:
        values[jid] = solved[jid]
    for j in tree.order:
        values[j.id] = solved[j.id]
    return free


CONTINUATION_DEG = 5.0       # a loop solve follows a driver in steps no larger than this
CONTINUATION_MM = 2.0


def solve_from(assembly, start: dict[str, object], *,
               resolve_face: FaceResolver | None = None) -> SolveResult:
    """Solve `assembly` (its joints carry the NEW values) arriving from the values in `start`
    (the pose it was in). With loops, the drivers move there in small steps, each solve starting
    from the last one's answer, so a linkage stays on its assembly branch instead of jumping to
    the mirror pose a single big move can land on. Without loops it is solve()."""
    loops = list(getattr(assembly, "loops", None) or [])
    if not loops:
        return solve(assembly, resolve_face=resolve_face)
    target = {j.id: j.value for j in assembly.joints}
    n = 1
    for j in assembly.joints:
        a, b = start.get(j.id, j.value), j.value
        if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
            continue
        step = CONTINUATION_MM if j.kind == "slider" else CONTINUATION_DEG
        n = max(n, math.ceil(abs(float(b) - float(a)) / step))
    n = min(n, 400)
    cur = dict(start)
    res = None
    for k in range(1, n + 1):
        f = k / n
        vals = {}
        for j in assembly.joints:
            a, b = start.get(j.id, j.value), target[j.id]
            if a == b or j.coupling is not None:
                vals[j.id] = cur.get(j.id, b)        # not moved by the caller: last answer
            elif isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
                vals[j.id] = b
            else:                                    # a joint the caller moved: step it
                vals[j.id] = float(a) + (float(b) - float(a)) * f
        trial = assembly.model_copy(update={"joints": [
            j.model_copy(update={"value": vals[j.id]}) for j in assembly.joints]})
        res = solve(trial, resolve_face=resolve_face)
        cur = {**vals, **res.solved}
    stored = {j.id: j.value for j in assembly.joints}
    res.solved = {jid: v for jid, v in res.applied.items() if v != stored.get(jid)}
    return res


def solve(assembly, *, resolve_face: FaceResolver | None = None) -> SolveResult:
    """Place every instance. ``assembly`` has ``.instances`` and ``.joints`` (pydantic models or
    objects with the same attributes) and optionally ``.loops``. Raises on an unknown instance,
    a joint loop (closed chains use loop constraints) or a coupling cycle.

    Order: coupled joints take their partner's value (x ratio + offset), the joint tree places
    every body, then (with loops) the loop solve adjusts the free joints on each loop path."""
    tree = _Tree(assembly, resolve_face)
    instances = tree.instances
    values: dict[str, object] = {j.id: j.value for j in tree.joints}
    apply_couplings(tree.order, values)
    result = SolveResult(transforms={}, dof_remaining=0)
    free: list[str] = []
    if tree.loops:
        free = _solve_loops(tree, values, result) or []
    transforms, applied, _via = tree.place(values)
    for lp in tree.loops:
        if any(e["loop_id"] == lp.id for e in result.loop_errors):
            continue
        r = _loop_residual(tree, lp, transforms)
        miss = math.sqrt(sum(c * c for c in r))
        result.loop_residuals[lp.id] = miss
        if miss > LOOP_TOL_MM:
            result.loop_errors.append({
                "loop_id": lp.id, "residual_mm": round(miss, 4),
                "reason": "the loop cannot close at this input (out of range or past a "
                          "toggle position)"})
    stored = {j.id: j.value for j in tree.joints}
    if result.loop_errors and free:
        # a loop that did not close: the optimizer's best effort is NOT a pose to keep. Draw and
        # report it, but never hand it back to be stored (the last good values stay)
        for e in result.loop_errors:
            e["reason"] += "; the stored joint values were kept (the last good pose)"
        values = {**values, **{jid: stored[jid] for jid in free}}
        apply_couplings(tree.order, values)
        transforms, applied, _via = tree.place(values)
    loop_moved = {jid for jid in values if jid in applied and values[jid] != stored[jid]}
    overconstrained = [sorted(m for m in members if instances[m].fixed)
                       for members in tree.components.values()
                       if sum(1 for m in members if instances[m].fixed) > 1]
    movable = sum(1 for inst in assembly.instances if not inst.fixed)
    dof = (6 * movable - sum(CONSTRAINTS.get(j.kind, 0) for j in tree.joints)
           - len(tree.order))
    if tree.loops:
        # a loop removes only as many freedoms as its closure equations are independent (a
        # planar or spherical loop counted spatially would over-count): the Jacobian's rank
        dof -= _loop_rank(tree, values)
    result.transforms = transforms
    result.dof_remaining = max(dof, 0)
    result.applied = applied
    result.overconstrained = overconstrained
    result.solved = {jid: applied[jid] for jid in loop_moved}
    return result


def _loop_rank(tree: _Tree, values: dict[str, object]) -> int:
    """Rank of the loop-closure equations w.r.t. every non-coupled tree joint value."""
    import numpy as np
    coupled = {j.id for j in tree.order}
    slots = [(j, k) for j in tree.joints if j.id not in coupled for k in range(_NCOMP[j.kind])]
    if not slots:
        return 0

    def res(dx):
        vals = dict(values)
        for (j, k), d in zip(slots, dx):
            v = vals[j.id]
            if isinstance(v, (list, tuple)):
                v = [float(c) for c in (list(v) + [0.0] * _NCOMP[j.kind])[:_NCOMP[j.kind]]]
                v[k] += d
                vals[j.id] = v
            else:
                vals[j.id] = float(v) + d
        apply_couplings(tree.order, vals)
        tr, _a, _v = tree.place(vals)
        out: list[float] = []
        for lp in tree.loops:
            out += _loop_residual(tree, lp, tr)
        return np.array(out)
    h = 1e-4
    r0 = res(np.zeros(len(slots)))
    jac = np.zeros((len(r0), len(slots)))
    for c in range(len(slots)):
        e = np.zeros(len(slots))
        e[c] = h
        jac[:, c] = (res(e) - r0) / h
    sv = np.linalg.svd(jac, compute_uv=False)
    return int(np.sum(sv > 1e-5 * max(1.0, sv[0] if len(sv) else 1.0)))
