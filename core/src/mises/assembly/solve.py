"""Assembly kinematics: a tree solve from fixed instances through joints.

Pure Python 4x4 row-major matrix math (no build123d), so it is cheap to import and easy to
test. A joint couples a frame on instance ``a`` with a frame on instance ``b``; the solve places
``b`` relative to ``a`` (or the reverse, whichever endpoint is already placed) as

    T_b = T_a . F_a . M(value) . F_b^-1

where ``F_x`` is the frame transform in that instance's part coordinates and ``M`` is the joint
motion in the shared frame (rotation about local Z for revolute, translation along local Z for
slider, both for cylindrical, a 3-angle rotation for ball, identity for fixed).

Loops in the joint graph are over-constrained for this closed-form solve and are rejected.
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


def solve(assembly, *, resolve_face: FaceResolver | None = None) -> SolveResult:
    """Place every instance. ``assembly`` has ``.instances`` and ``.joints`` (pydantic models or
    objects with the same attributes). Raises on an unknown instance or a joint loop."""
    instances = {inst.id: inst for inst in assembly.instances}
    joints = list(assembly.joints)

    uf = _UnionFind()
    for iid in instances:
        uf.add(iid)
    for j in joints:
        for ref in (j.a, j.b):
            if ref.instance_id not in instances:
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"joint {j.id} references unknown instance {ref.instance_id!r}")
        if not uf.union(j.a.instance_id, j.b.instance_id):
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"joint {j.id} closes a loop; this solve needs an open joint tree",
                             hint="remove the redundant joint or ground one side")

    transforms: dict[str, Mat] = {}
    applied: dict[str, object] = {}

    # anchors: every fixed instance, plus one instance per component with no fixed member.
    comp_members: dict[str, list[str]] = {}
    for iid in instances:
        comp_members.setdefault(uf.find(iid), []).append(iid)
    for inst in assembly.instances:
        if inst.fixed:
            transforms[inst.id] = [row[:] for row in inst.transform]
    for root, members in comp_members.items():
        if not any(instances[m].fixed for m in members):
            anchor = members[0]
            transforms[anchor] = [row[:] for row in instances[anchor].transform]

    # propagate through joints until nothing new can be placed
    pending = list(joints)
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
            value = clamp_value(j.value, j.limits)
            applied[j.id] = value
            fa = _resolve_frame(instances[a].part_id, j.a.frame, resolve_face)
            fb = _resolve_frame(instances[b].part_id, j.b.frame, resolve_face)
            m = joint_motion(j.kind, value)
            if ta is not None:
                transforms[b] = matmul(matmul(matmul(ta, fa), m), inverse_rigid(fb))
            else:
                transforms[a] = matmul(matmul(matmul(tb, fb), inverse_rigid(m)), inverse_rigid(fa))
            progress = True
        pending = still

    # free bodies that no anchor reached: leave at their stored transform
    for inst in assembly.instances:
        transforms.setdefault(inst.id, [row[:] for row in inst.transform])

    movable = sum(1 for inst in assembly.instances if not inst.fixed)
    dof = 6 * movable - sum(CONSTRAINTS.get(j.kind, 0) for j in joints)
    return SolveResult(transforms=transforms, dof_remaining=max(dof, 0), applied=applied)
