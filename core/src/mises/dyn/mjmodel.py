"""A dynamics job's model (plain JSON, SI units) -> a compiled MuJoCo model (Plan 10 C3).

Runs inside the sandboxed dynamics worker and in tests; it imports only numpy and mujoco, never
mises (the worker is started with `python -I` and loads this file by path).

The model JSON (written by `mises.dyn.export`):

    {"timestep": 0.001, "gravity": [0, 0, -9.81],
     "floor": {"z": -0.12, "friction": 0.9} | null,
     "bodies": [{"name", "parent": str | null, "pos": [m, parent frame], "free": bool,
                 "mass": kg, "com": [m, body frame], "inertia": [[kg m2]] about the com,
                 "joints": [{"name", "type": "hinge"|"slide"|"ball", "axis", "pos",
                             "range": [lo, hi] | null, "ref", "armature", "damping"}],
                 "geoms": [{"name", "type": "box"|"sphere", "size", "pos", "quat",
                            "friction", "foot": bool}]}],
     "actuators": [{"name", "joint", "kind": "position"|"motor", "kp", "kv",
                    "forcerange": [lo, hi] | null}],
     "equalities": [{"type": "joint", "joint1", "joint2", "polycoef": [5 numbers]},
                    {"type": "connect", "body1", "body2", "anchor": [m, body1 frame]},
                    {"type": "weld", "body1", "body2"}]}

Bodies are world-aligned (no rotation) at the exported pose and listed parent first. Every
body's inertia is explicit (from the B-rep), so geoms only collide.

Collision layers: mode "dyn" lets the robot touch only the floor (links never collide with
each other); mode "rom" lets links collide with each other (MuJoCo still skips a body and its
parent) and drops the floor, for range-of-motion sweeps.
"""
from __future__ import annotations

import numpy as np

import mujoco

ROBOT_BIT, FLOOR_BIT = 2, 1
MIN_INERTIA = 1e-9          # kg m2: a degenerate (thin) part never breaks the compile


def _jtype(t: str):
    return {"hinge": mujoco.mjtJoint.mjJNT_HINGE, "slide": mujoco.mjtJoint.mjJNT_SLIDE,
            "ball": mujoco.mjtJoint.mjJNT_BALL}[t]


def _psd(inertia) -> np.ndarray:
    """A symmetric positive-definite 3x3 (eigenvalues floored), satisfying the triangle rule
    MuJoCo checks (each principal moment <= the sum of the other two)."""
    a = np.array(inertia, dtype=float).reshape(3, 3)
    a = 0.5 * (a + a.T)
    w, v = np.linalg.eigh(a)
    w = np.maximum(w, MIN_INERTIA)
    for i in range(3):
        others = sum(w) - w[i]
        if w[i] > others:
            w[i] = others
    return v @ np.diag(w) @ v.T


def build_spec(model: dict, mode: str = "dyn") -> "mujoco.MjSpec":
    s = mujoco.MjSpec()
    s.compiler.degree = False            # every angle in the job is in radians
    s.option.timestep = float(model.get("timestep", 0.001))
    s.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    s.option.gravity = list(model.get("gravity", [0.0, 0.0, -9.81]))
    rom = mode == "rom"
    floor = model.get("floor")
    if floor is not None and not rom:
        s.worldbody.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[50, 50, 0.1],
                             pos=[0.0, 0.0, float(floor["z"])],
                             friction=[float(floor.get("friction", 0.9)), 0.005, 0.0001],
                             contype=FLOOR_BIT, conaffinity=ROBOT_BIT)
    made: dict[str, object] = {"": s.worldbody}
    for b in model["bodies"]:
        parent = made[b.get("parent") or ""]
        body = parent.add_body(name=b["name"], pos=list(b["pos"]))
        if b.get("free"):
            body.add_freejoint(name=f"{b['name']}__free")
        body.explicitinertial = True
        body.mass = max(float(b["mass"]), 1e-6)
        body.ipos = list(b["com"])
        i = _psd(b["inertia"])
        body.fullinertia = [i[0, 0], i[1, 1], i[2, 2], i[0, 1], i[0, 2], i[1, 2]]
        for j in b.get("joints", []):
            jt = body.add_joint(name=j["name"], type=_jtype(j["type"]),
                                axis=list(j.get("axis", [0, 0, 1])), pos=list(j.get("pos", [0, 0, 0])))
            jt.armature = float(j.get("armature", 0.002))
            jt.damping = [float(j.get("damping", 0.02)), 0.0, 0.0]   # 3.17: a polynomial
            if j["type"] != "ball":
                jt.ref = float(j.get("ref", 0.0))
            if j.get("range") is not None and j["type"] != "ball":
                jt.range = [float(j["range"][0]), float(j["range"][1])]
                jt.limited = mujoco.mjtLimited.mjLIMITED_TRUE
            else:
                jt.limited = mujoco.mjtLimited.mjLIMITED_FALSE
        for g in b.get("geoms", []):
            gtype = {"box": mujoco.mjtGeom.mjGEOM_BOX,
                     "sphere": mujoco.mjtGeom.mjGEOM_SPHERE}[g.get("type", "box")]
            kw = {"name": g["name"], "type": gtype, "size": list(g["size"]) + [0.0] * (3 - len(g["size"])),
                  "pos": list(g.get("pos", [0, 0, 0])), "quat": list(g.get("quat", [1, 0, 0, 0])),
                  "friction": [float(g.get("friction", 0.9)), 0.005, 0.0001]}
            if rom:
                kw.update(contype=ROBOT_BIT, conaffinity=ROBOT_BIT)
            else:
                kw.update(contype=ROBOT_BIT, conaffinity=FLOOR_BIT)
            geom = body.add_geom(**kw)
            geom.mass = 0.0
        made[b["name"]] = body
    for a in model.get("actuators", []):
        act = s.add_actuator(name=a["name"], target=a["joint"], trntype=mujoco.mjtTrn.mjTRN_JOINT)
        if a.get("kind", "position") == "position":
            act.set_to_position(kp=float(a.get("kp", 50.0)), kv=float(a.get("kv", 1.0)))
        else:
            act.set_to_motor()
        fr = a.get("forcerange")
        if fr is not None:
            act.forcerange = [float(fr[0]), float(fr[1])]
            act.forcelimited = mujoco.mjtLimited.mjLIMITED_TRUE
        else:
            act.forcelimited = mujoco.mjtLimited.mjLIMITED_FALSE
        act.ctrllimited = mujoco.mjtLimited.mjLIMITED_FALSE
    for e in model.get("equalities", []):
        eq = s.add_equality()
        if e["type"] == "joint":
            eq.type = mujoco.mjtEq.mjEQ_JOINT
            eq.objtype = mujoco.mjtObj.mjOBJ_JOINT
            eq.name1, eq.name2 = e["joint1"], e["joint2"]
            data = np.zeros(12)
            data[:5] = (list(e.get("polycoef", [0, 1, 0, 0, 0])) + [0] * 5)[:5]
            eq.data = data
        elif e["type"] == "connect":
            eq.type = mujoco.mjtEq.mjEQ_CONNECT
            eq.objtype = mujoco.mjtObj.mjOBJ_BODY
            eq.name1, eq.name2 = e["body1"], e["body2"]
            data = np.zeros(12)
            data[:3] = list(e["anchor"])
            eq.data = data
        elif e["type"] == "weld":
            eq.type = mujoco.mjtEq.mjEQ_WELD
            eq.objtype = mujoco.mjtObj.mjOBJ_BODY
            eq.name1, eq.name2 = e["body1"], e["body2"]
            data = np.zeros(12)     # relpose quaternion all 0: held at the exported pose
            data[10] = 1.0          # torquescale
            eq.data = data
        eq.solref = [0.005, 1.0]
    return s


def compile_model(model: dict, mode: str = "dyn"):
    """(MjModel, MjData) at the exported pose (qpos0)."""
    m = build_spec(model, mode).compile()
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    return m, d
