"""A build's assembly -> a dynamics model (the JSON mises.dyn.mjmodel compiles), plus the map
from MuJoCo names back to Mises ids (Plan 10 C3, research §4.2).

1. Tree: the joints form a graph; a spanning tree is grown from the root, and every joint
   that closes a cycle becomes a loop constraint (revolute -> equality connect at its anchor,
   fixed -> weld). C2's loop-closure records ({kind: "loop", joint_a, joint_b, frame_a,
   frame_b}) become connects too. Instances joined by `fixed` joints merge into one rigid body
   (their B-rep inertias combined).
2. Root: the cluster holding a grounded (`fixed`) instance, else the heaviest. Base "auto" is
   free (a floating root on a floor) when the robot stands on its distal links (the lowest
   point of the assembly is on a non-root body and the root is clear of the floor), else
   fixed (an arm on its base).
3. Frames: bodies are world-aligned at the assembly's current pose; a joint's anchor and axis
   come from its frame on instance a (T_a . F_a) and its MuJoCo `ref` is the joint's current
   value, so qpos equals the Mises joint value (deg -> rad, mm -> m). When the tree runs from
   b to a the axis is negated so the motion is the same.
4. Mass: each part's B-rep inertia (metrics `inertia`) scaled to its tree mass (mises.mass);
   a part without one is a solid box of its bounding box (approximate, noted).
5. Collision: one box per instance (its part's bounding box, posed); the lowest box of each
   distal body that reaches the floor is a foot.
6. Actuators: the joint's actuator (an argument, or the joint's own `actuator` metadata from
   C2) -> a position servo with forcerange +-stall and armature; no actuator -> an ideal stiff
   servo (the torque it needs is still reported). A joint with a `coupling` (C2 contract:
   value = ratio x partner's value) gets an equality joint instead of an actuator.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..assembly.geometry import face_resolver, part_dir
from ..assembly.solve import _resolve_frame, matmul, solve
from ..cad import safe_io
from ..engineering import actuators as act
from ..types import ErrorCode, MisesError
from . import inertia as inr

G = 9.81
FLOOR_FRICTION = 0.9
FOOT_BAND_M = 0.015
# position-servo model (research §4.5: hobby-servo tracking approximated with PD): a servo
# reaches stall torque at SERVO_STIFF_RAD of error; an unassigned joint is an ideal servo that
# sags IDEAL_STIFF_RAD under the robot's own weight scale. The gain is capped so the joint's
# own frequency on its armature stays well inside the 1 ms step (omega dt <= 0.3).
SERVO_STIFF_RAD = 0.05
IDEAL_STIFF_RAD = 0.02
SERVO_STIFF_M = 0.002       # a linear actuator reaches its force limit at 2 mm of error
IDEAL_STIFF_M = 0.002       # an ideal slider sags 2 mm under the robot's weight
IDEAL_ARMATURE = 0.005
STEP_S = 0.001


def _gains(kp: float, armature: float) -> tuple[float, float]:
    kp = min(kp, (0.3 / STEP_S) ** 2 * armature)
    return kp, 2.0 * math.sqrt(kp * armature)
MOVING = ("revolute", "slider", "cylindrical", "ball", "universal")


@dataclass
class Export:
    model: dict
    map: dict
    notes: list[str] = field(default_factory=list)
    base: str = "fixed"
    total_mass_kg: float = 0.0
    excluded: list[str] = field(default_factory=list)
    transforms: dict = field(default_factory=dict)       # instance id -> world 4x4 (mm)


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[ra] = rb
        return True


def _safe(name: str, used: set) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")[:40] or "body"
    n, k = base, 2
    while n in used:
        n, k = f"{base}_{k}", k + 1
    used.add(n)
    return n


def _metrics(home: Path, part_id: str) -> dict:
    try:
        m = safe_io.read_json(part_dir(Path(home) / "parts", part_id), "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath, MisesError):
        m = None
    return m if isinstance(m, dict) else {}


def _mass_kg(store, home: Path, part_id: str, metrics: dict, notes: list) -> float:
    from .. import mass as _mass
    try:
        g = _mass.part_mass(store, Path(home), part_id, metrics or None)["mass_g"]
    except Exception:  # noqa: BLE001 - a bare store
        g = None
    if g is None:
        i = (metrics or {}).get("inertia") or {}
        if i.get("mass_kg"):
            return float(i["mass_kg"])
        g = (metrics or {}).get("mass_g")
    if g is None or g <= 0:
        notes.append(f"{part_id}: no known mass, weighed as 10 g")
        return 0.01
    return float(g) / 1000.0


def _quat(r: np.ndarray) -> list[float]:
    """Rotation matrix -> unit quaternion [w, x, y, z]."""
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [(r[2, 1] - r[1, 2]) / s, 0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 2] - r[2, 0]) / s, (r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[1, 0] - r[0, 1]) / s, (r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s]
    n = math.sqrt(sum(x * x for x in q))
    return [x / n for x in q]


def _box_world(metrics: dict, T: np.ndarray):
    """(centre m, half sizes m, rotation, world min z m, world AABB (lo, hi) m) of a part's
    bbox posed by T (mm)."""
    bb = metrics.get("bbox") or {}
    lo = np.array(bb.get("min") or [0, 0, 0], dtype=float)
    hi = np.array(bb.get("max") or [0, 0, 0], dtype=float)
    half = np.maximum((hi - lo) / 2.0, 0.25) / 1000.0
    c = T[:3, :3] @ ((lo + hi) / 2.0) + T[:3, 3]
    r = T[:3, :3]
    corners = [T[:3, :3] @ np.array([x, y, z]) + T[:3, 3]
               for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    pts = np.array(corners) / 1000.0
    return c / 1000.0, half, r, float(pts[:, 2].min()), (pts.min(axis=0), pts.max(axis=0))


def _joint_value(j) -> float:
    v = j.value
    return float(v[0] if isinstance(v, (list, tuple)) and v else v or 0.0)


def _coupling(j):
    c = _get(j, "coupling")
    if c is None:
        return None
    return {"kind": _get(c, "kind"), "ratio": float(_get(c, "ratio") or 1.0),
            "partner_joint": _get(c, "partner_joint")}


def _loops(asm) -> list:
    out = []
    for name in ("loops", "loop_closures", "constraints"):
        for lp in _get(asm, name, None) or []:
            if (_get(lp, "kind") or "loop") == "loop":
                out.append(lp)
    return out


def export(home: Path, store, asm, *, base: str = "auto", actuators: dict | None = None,
           friction: float = FLOOR_FRICTION, slider_force_n: dict | None = None) -> Export:
    """slider_force_n: joint id -> the force limit (N) of a linear actuator on a slider (a
    rotary actuator's torque is no slider force: without one the slider is an ideal servo)."""
    home = Path(home)
    notes: list[str] = []
    inst = {i.id: i for i in asm.instances}
    if not inst:
        raise MisesError(ErrorCode.INVALID_INPUT, "the assembly is empty",
                         hint="add the build's parts to the assembly first")
    joints = [j for j in asm.joints if j.a.instance_id in inst and j.b.instance_id in inst]
    uf = _UF(inst)
    tree, closing = [], []
    for j in joints:
        (tree if uf.union(j.a.instance_id, j.b.instance_id) else closing).append(j)
    resolver = face_resolver(home / "parts")
    sub = type(asm)(instances=list(asm.instances), joints=tree) if hasattr(asm, "model_copy") \
        else type("A", (), {"instances": list(asm.instances), "joints": tree})()
    res = solve(sub, resolve_face=resolver)
    T = {i: np.array(res.transforms[i], dtype=float) for i in inst}

    # rigid clusters: instances joined by fixed tree joints
    cu = _UF(inst)
    for j in tree:
        if j.kind == "fixed":
            cu.union(j.a.instance_id, j.b.instance_id)
    clusters: dict[str, list[str]] = {}
    for iid in inst:
        clusters.setdefault(cu.find(iid), []).append(iid)

    metrics = {i.part_id: _metrics(home, i.part_id) for i in asm.instances}
    masses: dict[str, inr.MassProps] = {}
    box_parts = set()
    for iid, i in inst.items():
        mt = metrics[i.part_id]
        mp = inr.of_part(mt, _mass_kg(store, home, i.part_id, mt, notes))
        if mp.source == "box":
            box_parts.add(i.part_id)
        masses[iid] = inr.to_world(mp, T[iid])
    if box_parts:
        notes.append("no B-rep inertia for " + ", ".join(sorted(box_parts)[:6]) +
                     " (rebuild them): solid-box inertia used, approximate")
    cmass = {c: inr.combine([masses[i] for i in members]) for c, members in clusters.items()}

    # the component the root belongs to; the rest is left out
    comp = _UF(clusters)
    edges = [j for j in tree if j.kind != "fixed"]
    for j in edges:
        comp.union(cu.find(j.a.instance_id), cu.find(j.b.instance_id))
    fixed_c = [cu.find(i.id) for i in asm.instances if i.fixed]
    root = fixed_c[0] if fixed_c else max(clusters, key=lambda c: cmass[c].mass)
    keep = {c for c in clusters if comp.find(c) == comp.find(root)}
    excluded = sorted(i for c in clusters if c not in keep for i in clusters[c])
    if excluded:
        notes.append(f"{len(excluded)} instance(s) not joined to the rest were left out: "
                     + ", ".join(inst[i].name or i for i in excluded[:6]))

    # boxes, and the floor
    boxes = {iid: _box_world(metrics[inst[iid].part_id], T[iid])
             for c in keep for iid in clusters[c]}
    zmin = min(b[3] for b in boxes.values())
    root_min = min(boxes[i][3] for i in clusters[root])
    if base == "auto":
        base = "free" if root_min > zmin + 0.010 else "fixed"
    free = base == "free"

    # BFS over the cluster tree
    adj: dict[str, list] = {c: [] for c in keep}
    for j in edges:
        ca, cb = cu.find(j.a.instance_id), cu.find(j.b.instance_id)
        if ca in keep:
            adj[ca].append((cb, j))
            adj[cb].append((ca, j))
    order, parent_of, via = [root], {root: None}, {}
    q = [root]
    while q:
        c = q.pop(0)
        for nb, j in adj[c]:
            if nb in parent_of:
                continue
            parent_of[nb], via[nb] = c, j
            order.append(nb)
            q.append(nb)

    used: set[str] = set()
    bname = {c: _safe("b_" + (inst[clusters[c][0]].name or clusters[c][0]), used) for c in order}
    children = {c: [k for k in order if parent_of.get(k) == c] for c in order}
    # body origins (world, m): the root at its com, every other body at its joint anchor
    anchor: dict[str, np.ndarray] = {}
    axis_w: dict[str, np.ndarray] = {}
    for c in order[1:]:
        j = via[c]
        ta = T[j.a.instance_id]
        fa = np.array(_resolve_frame(inst[j.a.instance_id].part_id, j.a.frame, resolver))
        w = ta @ fa
        anchor[c] = w[:3, 3] / 1000.0
        ax = w[:3, 2] / max(np.linalg.norm(w[:3, 2]), 1e-12)
        # the tree runs a -> b when the child holds b; else the motion is reversed
        axis_w[c] = ax if cu.find(j.b.instance_id) == c else -ax
    origin = {root: np.array(cmass[root].com)}
    origin.update(anchor)

    total = sum(cmass[c].mass for c in order)
    extent = max(np.linalg.norm(b[0] - np.array(cmass[root].com)) + np.linalg.norm(b[1])
                 for b in boxes.values())
    tau_scale = max(total * G * max(extent, 0.02), 0.05)
    overrides = dict(actuators or {})
    coupled = {}
    for j in joints:
        cp = _coupling(j)
        if cp and cp["partner_joint"]:
            coupled[j.id] = cp

    bodies, acts, eqs = [], [], []
    jmap: dict[str, dict] = {}
    amap: dict[str, dict] = {}
    leaf_reach = set()
    for c in order:
        if not children[c] and c != root:
            leaf_reach.add(c)
    for c in order:
        p = parent_of[c]
        mp = cmass[c]
        o = origin[c]
        body = {"name": bname[c], "parent": bname[p] if p else None,
                "pos": (o - origin[p]).tolist() if p else o.tolist(),
                "free": bool(free and c == root), "mass": mp.mass,
                "com": (np.array(mp.com) - o).tolist(), "inertia": mp.inertia,
                "joints": [], "geoms": [], "instances": list(clusters[c])}
        if p is not None:
            j = via[c]
            ax = axis_w[c].tolist()
            v = _joint_value(j)
            kinds = {"revolute": [("hinge", "", math.radians)], "slider": [("slide", "", lambda x: x / 1000.0)],
                     "cylindrical": [("hinge", "_r", math.radians), ("slide", "_t", lambda x: x / 1000.0)],
                     "ball": [("ball", "", None)], "universal": [("ball", "", None)]}.get(j.kind)
            if kinds is None:
                notes.append(f"joint {j.id}: kind {j.kind!r} has no dynamics model; held fixed")
                kinds = []
            if j.kind == "universal":
                notes.append(f"joint {j.id}: universal joint modelled as a ball joint")
            for k, (mjt, suffix, conv) in enumerate(kinds):
                name = f"j_{j.id}{suffix}"
                val = (j.value[k] if isinstance(j.value, (list, tuple)) and len(j.value) > k
                       else (v if k == 0 else 0.0))
                jd = {"name": name, "type": mjt, "axis": ax, "pos": [0.0, 0.0, 0.0],
                      "damping": 0.02}
                if conv is not None:
                    jd["ref"] = conv(float(val))
                    if j.limits is not None and j.kind != "cylindrical":
                        jd["range"] = [conv(float(j.limits[0])), conv(float(j.limits[1]))]
                    elif j.limits is not None and k == 0:
                        jd["range"] = [conv(float(j.limits[0])), conv(float(j.limits[1]))]
                perf = act.resolve(overrides.get(j.id) or _get(j, "actuator"))
                if perf is not None and mjt == "slide":
                    # a torque rating says nothing about a slider's force (it depends on the
                    # pinion radius or the screw lead): drive it through a coupling, or give
                    # slider_force_n
                    notes.append(f"joint {j.id}: {perf.name} is rotary; the slider is "
                                 "modelled as an ideal servo (give slider_force_n, or couple "
                                 "it to the revolute that drives it)")
                    perf = None
                arm = perf.armature() if perf else IDEAL_ARMATURE
                jd["armature"] = arm
                body["joints"].append(jd)
                jmap[name] = {"joint_id": j.id, "kind": j.kind, "dof": k, "mj_type": mjt,
                              "unit": "deg" if mjt == "hinge" else ("mm" if mjt == "slide" else "quat")}
                if mjt == "ball" or j.id in coupled:
                    continue
                aname = f"a_{j.id}{suffix}"
                force = (slider_force_n or {}).get(j.id) if mjt == "slide" else None
                if force:
                    kp, kv = _gains(float(force) / SERVO_STIFF_M, arm)
                    acts.append({"name": aname, "joint": name, "kind": "position", "kp": kp,
                                 "kv": kv, "forcerange": [-float(force), float(force)]})
                    amap[aname] = {"joint_id": j.id, "actuator": None, "perf": None,
                                   "force_n": float(force)}
                elif perf is not None:
                    stall = perf.stall_torque_nm
                    kp, kv = _gains(stall / SERVO_STIFF_RAD, arm)
                    acts.append({"name": aname, "joint": name, "kind": "position", "kp": kp,
                                 "kv": kv, "forcerange": [-stall, stall]})
                    amap[aname] = {"joint_id": j.id, "actuator": perf.id, "perf": perf.as_dict()}
                else:
                    kp, kv = _gains(tau_scale / IDEAL_STIFF_RAD if mjt == "hinge"
                                    else total * G / IDEAL_STIFF_M, arm)
                    acts.append({"name": aname, "joint": name, "kind": "position", "kp": kp,
                                 "kv": kv, "forcerange": None})
                    amap[aname] = {"joint_id": j.id, "actuator": None, "perf": None}
        # one box per instance; the lowest box of a distal body near the floor is a foot
        low = None
        for iid in clusters[c]:
            cen, half, r, zlo, aabb = boxes[iid]
            g = {"name": _safe("g_" + (inst[iid].name or iid), used), "type": "box",
                 "size": half.tolist(), "pos": (cen - o).tolist(), "quat": _quat(r),
                 "friction": friction, "instance": iid}
            body["geoms"].append(g)
            if low is None or zlo < low[1]:
                low = (g, zlo, aabb)
        if free and c in leaf_reach and low is not None and low[1] <= zmin + FOOT_BAND_M:
            # the foot: a sphere at the lowest point of the posed box (research §4.2: a
            # primitive for feet), in place of that box
            g, zlo, (lo, hi) = low
            r = float(min(min(g["size"]), 0.02))
            iid = g["instance"]
            cen, half, rot, _z, _ab = boxes[iid]
            T_i = T[iid]
            bb = metrics[inst[iid].part_id].get("bbox") or {}
            blo, bhi = np.array(bb.get("min") or [0, 0, 0]), np.array(bb.get("max") or [0, 0, 0])
            corners = np.array([T_i[:3, :3] @ np.array([x, y, z]) + T_i[:3, 3]
                                for x in (blo[0], bhi[0]) for y in (blo[1], bhi[1])
                                for z in (blo[2], bhi[2])]) / 1000.0
            bottom = corners[corners[:, 2] <= zlo + 0.001]
            p = np.array([bottom[:, 0].mean(), bottom[:, 1].mean(), zlo + r])
            g.update(type="sphere", size=[r], pos=(p - o).tolist(), quat=[1.0, 0.0, 0.0, 0.0],
                     foot=True)
        bodies.append(body)
    # couplings and loops
    for jid, cp in coupled.items():
        mine = [n for n, v in jmap.items() if v["joint_id"] == jid and v["dof"] == 0]
        theirs = [n for n, v in jmap.items() if v["joint_id"] == cp["partner_joint"] and v["dof"] == 0]
        if not mine or not theirs:
            notes.append(f"coupling of joint {jid} to {cp['partner_joint']}: a joint is not in "
                         "the model; not applied")
            continue
        a_t, b_t = jmap[mine[0]]["mj_type"], jmap[theirs[0]]["mj_type"]
        k = cp["ratio"]
        if a_t == "slide" and b_t == "hinge":       # rack / screw: mm per rad
            k = k / 1000.0
        elif a_t == "hinge" and b_t == "slide":
            k = 1000.0 / k if k else 0.0
        eqs.append({"type": "joint", "joint1": mine[0], "joint2": theirs[0],
                    "polycoef": [0.0, k, 0.0, 0.0, 0.0]})
        jmap[mine[0]]["coupled_to"] = cp["partner_joint"]
    cl_of = {iid: c for c in order for iid in clusters[c]}
    for j in closing:
        ca, cb = cl_of.get(j.a.instance_id), cl_of.get(j.b.instance_id)
        if ca is None or cb is None or ca == cb:
            continue
        w = T[j.a.instance_id] @ np.array(_resolve_frame(inst[j.a.instance_id].part_id,
                                                         j.a.frame, resolver))
        a = w[:3, 3] / 1000.0 - origin[ca]
        if j.kind == "fixed":
            eqs.append({"type": "weld", "body1": bname[ca], "body2": bname[cb]})
        else:
            eqs.append({"type": "connect", "body1": bname[ca], "body2": bname[cb],
                        "anchor": a.tolist()})
            if j.kind != "revolute":
                notes.append(f"loop joint {j.id} ({j.kind}) closed as a pin (connect)")
    for lp in _loops(asm):
        ra, rb = _get(lp, "joint_a"), _get(lp, "joint_b")
        ia = _get(ra, "instance_id", ra) if not isinstance(ra, str) else ra
        ib = _get(rb, "instance_id", rb) if not isinstance(rb, str) else rb
        fa = _get(lp, "frame_a") or _get(ra, "frame")
        ca, cb = cl_of.get(ia), cl_of.get(ib)
        if ca is None or cb is None or fa is None:
            notes.append("a loop-closure record names no instances in the model; skipped")
            continue
        w = T[ia] @ np.array(_resolve_frame(inst[ia].part_id, fa, resolver))
        eqs.append({"type": "connect", "body1": bname[ca], "body2": bname[cb],
                    "anchor": (w[:3, 3] / 1000.0 - origin[ca]).tolist()})
    model = {"timestep": 0.001, "gravity": [0.0, 0.0, -G],
             "floor": {"z": zmin, "friction": friction} if free else None,
             "bodies": bodies, "actuators": acts, "equalities": eqs}
    mapping = {"bodies": {b["name"]: b["instances"] for b in bodies}, "joints": jmap,
               "actuators": amap, "base": base, "root": bname[root],
               "floor_z_mm": zmin * 1000.0 if free else None}
    return Export(model=model, map=mapping, notes=notes, base=base, total_mass_kg=total,
                  excluded=excluded, transforms={k: v.tolist() for k, v in T.items()})


def to_mj(map_: dict, joint_id: str, value: float, dof: int = 0) -> tuple[str, float]:
    """A Mises joint value (deg / mm) -> (MuJoCo joint name, rad / m)."""
    for name, v in map_["joints"].items():
        if v["joint_id"] == joint_id and v["dof"] == dof:
            return name, (math.radians(value) if v["mj_type"] == "hinge" else value / 1000.0)
    raise MisesError(ErrorCode.NOT_FOUND, f"joint {joint_id!r} is not a moving joint of the "
                     "dynamics model", hint="use a revolute / slider joint id from assembly_get")


def from_mj(map_: dict, name: str, value: float) -> float:
    v = map_["joints"].get(name) or {}
    return math.degrees(value) if v.get("mj_type") == "hinge" else value * 1000.0


def kinematics(home: Path, store, asm, *, base: str = "auto") -> dict:
    """The assembly as plain kinematics for the static checks (no MuJoCo): instances with
    mass and world com (m), the spanning tree's joints with world anchor + axis (m), the root
    instance, whether the base floats and which instances are feet; plus `pose(values)` that
    re-solves with some joint values changed (deg / mm) and returns (instances, tree)."""
    home = Path(home)
    inst = {i.id: i for i in asm.instances}
    if not inst:
        raise MisesError(ErrorCode.INVALID_INPUT, "the assembly is empty",
                         hint="add the build's parts to the assembly first")
    uf = _UF(inst)
    tree = [j for j in asm.joints if j.a.instance_id in inst and j.b.instance_id in inst
            and uf.union(j.a.instance_id, j.b.instance_id)]
    resolver = face_resolver(home / "parts")
    notes: list[str] = []
    metrics = {i.part_id: _metrics(home, i.part_id) for i in asm.instances}
    own = {iid: inr.of_part(metrics[i.part_id], _mass_kg(store, home, i.part_id,
                                                         metrics[i.part_id], notes))
           for iid, i in inst.items()}

    def pose(values: dict | None = None):
        js = [j.model_copy(update={"value": values[j.id]}) if values and j.id in values else j
              for j in tree]
        sub = type(asm)(instances=list(asm.instances), joints=js)
        res = solve(sub, resolve_face=resolver)
        T = {i: np.array(res.transforms[i], dtype=float) for i in inst}
        rows = []
        for iid, i in inst.items():
            w = inr.to_world(own[iid], T[iid])
            _c, _h, _r, zlo, (lo, hi) = _box_world(metrics[i.part_id], T[iid])
            rows.append({"id": iid, "name": i.name, "part_id": i.part_id, "mass_kg": w.mass,
                         "com": w.com, "zmin": zlo, "aabb": [lo.tolist(), hi.tolist()],
                         "foot": [float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2), zlo]})
        jrows = []
        for j in js:
            fa = np.array(_resolve_frame(inst[j.a.instance_id].part_id, j.a.frame, resolver))
            w = T[j.a.instance_id] @ fa
            jrows.append({"id": j.id, "kind": j.kind, "a": j.a.instance_id,
                          "b": j.b.instance_id, "anchor": (w[:3, 3] / 1000.0).tolist(),
                          "axis": (w[:3, 2] / max(np.linalg.norm(w[:3, 2]), 1e-12)).tolist(),
                          "value": j.value, "limits": j.limits,
                          "actuator": _get(j, "actuator")})
        return rows, jrows

    rows, jrows = pose()
    fixed = [i.id for i in asm.instances if i.fixed]
    root = fixed[0] if fixed else max(rows, key=lambda r: r["mass_kg"])["id"]
    # the rigid cluster of the root (fixed joints) decides the base, as export() does
    cu = _UF(inst)
    for j in tree:
        if j.kind == "fixed":
            cu.union(j.a.instance_id, j.b.instance_id)
    zmin = min(r["zmin"] for r in rows)
    root_min = min(r["zmin"] for r in rows if cu.find(r["id"]) == cu.find(root))
    free = (root_min > zmin + 0.010) if base == "auto" else base == "free"
    feet = [r["id"] for r in rows if free and cu.find(r["id"]) != cu.find(root)
            and r["zmin"] <= zmin + FOOT_BAND_M]
    return {"instances": rows, "tree": jrows, "root": root, "free": free, "feet": feet,
            "pose": pose, "notes": notes, "total_mass_kg": sum(r["mass_kg"] for r in rows)}
