"""Moving instances the way the joints allow (Plan 7 R19), plus joint sweeps for animation.

Pure math on top of solve.py. A free instance (an anchor of its joint tree: grounded, or the
first instance of an ungrounded group) moves anywhere and its attached parts follow. An instance
the solve places through a joint moves only along that joint: a request is turned into a change
of the joint's value (slider: along its axis; revolute: about its axis; cylindrical: both;
ball: about one of its frame axes). A fixed joint does not move at all.
"""
from __future__ import annotations

import math

from ..types import ErrorCode, MisesError
from .solve import (CONSTRAINTS, Mat, Vec, _resolve_frame, _UnionFind, clamp_value, identity,
                    matmul, rot_axis, solve, translate)

PARALLEL = 0.999          # |cos| above which two axes count as the same direction
OFF_AXIS_MM = 1e-3        # a translation component off the joint axis that is just noise


def _dot(a: Vec, b: Vec) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: Vec) -> float:
    return math.sqrt(_dot(v, v))


def _unit(v: Vec) -> Vec:
    n = _norm(v)
    if n < 1e-12:
        raise MisesError(ErrorCode.INVALID_INPUT, "rotation axis is zero-length",
                         hint="give a non-zero axis, e.g. [0, 0, 1]")
    return [c / n for c in v]


def _col(m: Mat, j: int) -> Vec:
    return [m[0][j], m[1][j], m[2][j]]


def placement(asm) -> dict[str, tuple[object, bool]]:
    """instance id -> (the joint the solve places it through, True if it is that joint's `b`).
    Anchors (grounded instances, the first of an ungrounded group) and free bodies are absent.
    Mirrors solve()'s propagation order exactly."""
    instances = {i.id: i for i in asm.instances}
    uf = _UnionFind()
    for iid in instances:
        uf.add(iid)
    for j in asm.joints:
        uf.union(j.a.instance_id, j.b.instance_id)
    placed = {i.id for i in asm.instances if i.fixed}
    groups: dict[str, list[str]] = {}
    for iid in instances:
        groups.setdefault(uf.find(iid), []).append(iid)
    for members in groups.values():
        if not any(instances[m].fixed for m in members):
            placed.add(members[0])
    out: dict[str, tuple[object, bool]] = {}
    pending, progress = list(asm.joints), True
    while pending and progress:
        progress, still = False, []
        for j in pending:
            a, b = j.a.instance_id, j.b.instance_id
            if a not in placed and b not in placed:
                still.append(j)
                continue
            if a in placed and b in placed:
                continue
            if a in placed:
                placed.add(b)
                out[b] = (j, True)
            else:
                placed.add(a)
                out[a] = (j, False)
            progress = True
        pending = still
    return out


def joint_world_frame(asm, joint, transforms: dict[str, Mat], resolve_face=None) -> Mat:
    """The joint's frame in world space on its already-placed side (T_side . F_side)."""
    inst = {i.id: i for i in asm.instances}
    place = placement(asm)
    b_placed = place.get(joint.b.instance_id, (None, None))[0] is joint
    side = joint.a if b_placed else joint.b
    f = _resolve_frame(inst[side.instance_id].part_id, side.frame, resolve_face)
    return matmul(transforms[side.instance_id], f)


def with_values(asm, values: dict[str, object]):
    """A copy of the assembly with some joints set to new values (nothing is stored)."""
    joints = [j.model_copy(update={"value": values[j.id]}) if j.id in values else j
              for j in asm.joints]
    return asm.model_copy(update={"joints": joints})


def _components(value, n: int) -> list[float]:
    vals = list(value) if isinstance(value, (list, tuple)) else [float(value)]
    return [float(v) for v in (vals + [0.0] * n)[:n]]


def move_free(transform: Mat, *, translate_by: Vec | None, rotate: dict | None,
              relative: bool) -> Mat:
    """A free instance's new transform. relative: translate and rotate are deltas (rotation
    about `about`, default the instance origin). Absolute: translate is the new origin and
    rotate.angle_deg the new orientation about its axis."""
    t = [row[:] for row in transform]
    origin = [t[0][3], t[1][3], t[2][3]]
    if rotate is not None:
        axis, ang = _unit(rotate["axis"]), float(rotate["angle_deg"])
        if relative:
            p = list(rotate.get("about") or origin)
            r = matmul(matmul(translate(p), rot_axis(axis, ang)), translate([-c for c in p]))
            t = matmul(r, t)
        else:
            r = rot_axis(axis, ang)
            for i in range(3):
                for k in range(3):
                    t[i][k] = r[i][k]
    if translate_by is not None:
        if relative:
            t = matmul(translate(translate_by), t)
        else:
            for i in range(3):
                t[i][3] = float(translate_by[i])
    return t


def joint_move(asm, iid: str, transforms: dict[str, Mat], *, translate_by: Vec | None,
               rotate: dict | None, relative: bool, resolve_face=None
               ) -> tuple[object, object, list[str]]:
    """Turn a move of a joint-placed instance into its joint's new value.
    Returns (joint, new value (unclamped), warnings)."""
    joint, is_b = placement(asm)[iid]
    sign = 1.0 if is_b else -1.0
    w = joint_world_frame(asm, joint, transforms, resolve_face)
    axis = _col(w, 2)
    kind = joint.kind
    warnings: list[str] = []
    if kind == "fixed":
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"instance {iid} is held by fixed joint {joint.id}; it cannot move",
                         hint="change the joint (assembly_remove it and add a slider or "
                              "revolute joint), or move the instance it is attached to")
    rot_n, lin_n = {"slider": (0, 1), "revolute": (1, 0), "cylindrical": (1, 1),
                    "ball": (3, 0)}[kind]
    if kind == "ball":
        cur = _components(joint.value, 3)
    elif kind == "cylindrical":
        cur = _components(joint.value, 2)
    else:
        cur = _components(joint.value, 1)
    new = list(cur)
    if translate_by is not None:
        d = list(translate_by)
        if not relative:
            p = transforms[iid]
            d = [d[i] - p[i][3] for i in range(3)]
        along = _dot(d, axis)
        off = _norm([d[i] - along * axis[i] for i in range(3)])
        if not lin_n:
            if _norm(d) > OFF_AXIS_MM:
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"instance {iid} is on {kind} joint {joint.id}, which does not "
                                 "slide", hint="rotate it about the joint axis instead, or move "
                                 "the instance it is attached to")
        else:
            if off > OFF_AXIS_MM:
                warnings.append(f"ignored {off:.3g} mm of the move off the {kind} axis "
                                f"{[round(c, 4) for c in axis]}: a joint-attached part moves "
                                "only along its joint")
            idx = 1 if kind == "cylindrical" else 0
            # absolute targets were turned into a delta from the current origin above
            new[idx] = cur[idx] + sign * along
    if rotate is not None:
        if not rot_n:
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"instance {iid} is on slider joint {joint.id}, which does not "
                             "rotate", hint="translate it along the joint axis instead")
        r_axis = _unit(rotate["axis"])
        ang = float(rotate["angle_deg"])
        if kind == "ball":
            cols = [_col(w, k) for k in range(3)]
            hit = next((k for k in range(3) if abs(_dot(cols[k], r_axis)) > PARALLEL), None)
            if hit is None:
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"ball joint {joint.id} turns about its frame axes only",
                                 hint=f"use one of {[[round(c, 4) for c in v] for v in cols]}")
            s = sign * math.copysign(1.0, _dot(cols[hit], r_axis))
            new[hit] = (cur[hit] + s * ang) if relative else s * ang
        else:
            c = _dot(axis, r_axis)
            if abs(c) <= PARALLEL:
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"instance {iid} is on {kind} joint {joint.id}: it turns only "
                                 f"about the joint axis {[round(v, 4) for v in axis]}",
                                 hint="rotate about that axis, or move the parent instance")
            s = sign * math.copysign(1.0, c)
            new[0] = (cur[0] + s * ang) if relative else s * ang
    value: object = new if kind in ("ball", "cylindrical") else new[0]
    return joint, value, warnings


def lerp(a, b, f: float):
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        n = max(len(a) if isinstance(a, (list, tuple)) else 1,
                len(b) if isinstance(b, (list, tuple)) else 1)
        aa, bb = _components(a, n), _components(b, n)
        return [x + (y - x) * f for x, y in zip(aa, bb)]
    return float(a) + (float(b) - float(a)) * f


def round_mat(m: Mat, nd: int = 4) -> Mat:
    return [[round(v, nd) + 0.0 for v in row] for row in m]


__all__ = ["CONSTRAINTS", "identity", "joint_move", "joint_world_frame", "lerp", "move_free",
           "placement", "round_mat", "solve", "with_values", "clamp_value"]
