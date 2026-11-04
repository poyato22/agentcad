"""The dynamics computations (Plan 10 C3): build/validate, joint torques (forward tracking +
inverse dynamics), stability (support margin, tip angle, CoP along a motion, tilt test),
joint reactions per link, and range-of-motion collision sweeps.

Worker side: numpy + mujoco (+ scipy for hulls) only, never mises. `run(job)` takes the job
JSON {"task", "model", "trajectory"?, ...} and returns plain JSON (SI units).
"""
from __future__ import annotations

import math
import time

import numpy as np

import mujoco

try:                                     # loaded by path in the worker, as a package in tests
    from . import mjmodel, traj as trajmod
except ImportError:                      # pragma: no cover - the worker injects these
    mjmodel = trajmod = None             # type: ignore[assignment]

SAMPLE_S = 0.002          # recorded every 2 ms
INVERSE_FILTER_SAMPLES = 5  # free-base inverse dynamics: a 10 ms moving average (impacts)
ACTUATOR_BAND_SAMPLES = 10  # ... and the actuator's demand: a 20 ms mean (servo bandwidth)
SERIES_POINTS = 240       # points per chart series
PENETRATION_TOL_M = 0.0005
# MuJoCo enums as plain ints: numpy ints from the model arrays never compare equal to them
FREE, HINGE, SLIDE = (int(mujoco.mjtJoint.mjJNT_FREE), int(mujoco.mjtJoint.mjJNT_HINGE),
                      int(mujoco.mjtJoint.mjJNT_SLIDE))
EQ_CONNECT = int(mujoco.mjtEq.mjEQ_CONNECT)


def _thin(xs, n: int = SERIES_POINTS) -> list:
    if len(xs) <= n:
        return list(xs)
    step = len(xs) / n
    return [xs[int(i * step)] for i in range(n)] + [xs[-1]]


def _r(x, n: int = 6):
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_r(v, n) for v in x]
    return round(float(x), n)


def _robot_floor_contacts(m, d, floor_id: int) -> list[dict]:
    """Floor contacts: world point and the floor's force on the robot (world frame)."""
    out = []
    f6 = np.zeros(6)
    for i in range(d.ncon):
        c = d.contact[i]
        if floor_id not in (c.geom1, c.geom2):
            continue
        mujoco.mj_contactForce(m, d, i, f6)
        frame = np.array(c.frame).reshape(3, 3)
        f = frame.T @ f6[:3]               # force geom1 exerts on geom2, world frame
        robot = c.geom2 if c.geom1 == floor_id else c.geom1
        if c.geom2 == floor_id:            # robot is geom1: the floor's push is the opposite
            f = -f
        out.append({"pos": np.array(c.pos), "force": f, "geom": int(robot),
                    "body": int(m.geom_bodyid[robot])})
    return out


def _floor_id(m) -> int:
    try:
        return m.geom("floor").id
    except KeyError:
        return -1


def _robot_bodies(m) -> list[int]:
    return [b for b in range(1, m.nbody)]


def _hull_margin(points: np.ndarray, p: np.ndarray) -> tuple[float, list]:
    """Signed distance (m) of p to the edges of the convex hull of `points` (2D): positive
    inside. Fewer than 3 distinct points: the distance to the segment/point, negated."""
    pts = np.unique(np.round(points, 5), axis=0)
    if len(pts) >= 3:
        try:
            from scipy.spatial import ConvexHull
            hull = ConvexHull(pts)
            eq = hull.equations
            margin = float(np.min(-(eq[:, :2] @ p + eq[:, 2])))
            return margin, pts[hull.vertices].tolist()
        except Exception:  # noqa: BLE001 - collinear points: fall through
            pass
    if len(pts) == 0:
        return -math.inf, []
    if len(pts) == 1:
        return -float(np.linalg.norm(p - pts[0])), pts.tolist()
    a, b = pts[0], pts[-1]
    t = np.clip(np.dot(p - a, b - a) / max(np.dot(b - a, b - a), 1e-12), 0, 1)
    return -float(np.linalg.norm(p - (a + t * (b - a)))), pts.tolist()


# ---------------------------------------------------------------- build

def task_build(job: dict) -> dict:
    model = job["model"]
    t0 = time.perf_counter()
    m, d = mjmodel.compile_model(model, "dyn")
    compile_s = time.perf_counter() - t0
    mr, dr = mjmodel.compile_model(model, "rom")
    pen = _penetrations(mr, dr, set(), PENETRATION_TOL_M)
    eq_res = []
    for i in range(m.neq):
        if int(m.eq_type[i]) == EQ_CONNECT:
            b1, b2 = m.eq_obj1id[i], m.eq_obj2id[i]
            a1 = d.xpos[b1] + d.xmat[b1].reshape(3, 3) @ m.eq_data[i, :3]
            a2 = d.xpos[b2] + d.xmat[b2].reshape(3, 3) @ m.eq_data[i, 3:6]
            eq_res.append(float(np.linalg.norm(a1 - a2)))
    return {"nbody": int(m.nbody - 1), "njnt": int(m.njnt), "nu": int(m.nu), "neq": int(m.neq),
            "total_mass_kg": _r(sum(m.body_mass)), "compile_s": _r(compile_s, 4),
            "bodies": [{"name": m.body(b).name, "mass_kg": _r(m.body_mass[b])}
                       for b in range(1, m.nbody)],
            "joints": [m.joint(j).name for j in range(m.njnt)],
            "actuators": [m.actuator(a).name for a in range(m.nu)],
            "initial_penetrations": pen, "loop_residual_m": _r(max(eq_res) if eq_res else 0.0)}


def _penetrations(m, d, skip: set, tol: float) -> list[dict]:
    out = []
    for i in range(d.ncon):
        c = d.contact[i]
        if c.dist >= -tol:
            continue
        b1, b2 = int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])
        key = tuple(sorted((m.body(b1).name, m.body(b2).name)))
        if key in skip:
            continue
        out.append({"bodies": list(key), "depth_mm": _r(-c.dist * 1000, 3)})
    return out


# ---------------------------------------------------------------- simulate a plan

def _simulate(m, d, plan, record_reactions: bool, floor_id: int,
              foot_geoms: frozenset = frozenset()):
    """Track the plan with the position actuators. Returns per-sample records. `foot_geoms`:
    the geom ids flagged as feet (any other floor contact is the body touching down)."""
    names = [m.actuator(a).name for a in range(m.nu)]
    jnames = trajmod.actuated(m)
    every = max(1, int(round(SAMPLE_S / m.opt.timestep)))
    steps = int(round(plan.duration_s / m.opt.timestep))
    rec = {"t": [], "qpos": [], "qvel": [], "force": [], "err": [], "cop": [], "support": [],
           "com": [], "react": [], "contacts": [], "xmat": [], "xpos": [], "subcom": [],
           "tilt": [], "touch": []}
    root_body = m.body_rootid[1] if m.nbody > 1 else 0
    jq = [int(m.joint(j).qposadr[0]) for j in jnames]
    # a position actuator is kp (q* - q) - kv qdot; a servo's own loop tracks the moving
    # setpoint, so its damping acts on the speed ERROR: ctrl = q* + (kv / kp) qdot* gives
    # kp (q* - q) + kv (qdot* - qdot). Without it a 2 N m servo spent all its torque damping a
    # fast swing (DOG demo: every trot saturated with 15-25 deg of lag).
    ff = [(-float(m.actuator_biasprm[a, 2]) / float(m.actuator_gainprm[a, 0])
           if m.actuator_gainprm[a, 0] > 0 and m.actuator_biasprm[a, 1] < 0 else 0.0)
          for a in range(len(jnames))]
    for i in range(steps + 1):
        t = i * m.opt.timestep
        tg = plan.at(t)
        rate = plan.rate_at(t) if any(ff) else None
        for a, j in enumerate(jnames):
            d.ctrl[a] = tg[j] + (ff[a] * rate[j] if rate is not None else 0.0)
        if i % every == 0:
            mujoco.mj_forward(m, d)
            rec["t"].append(t)
            rec["qpos"].append(d.qpos.copy())
            rec["qvel"].append(d.qvel.copy())
            rec["force"].append(d.actuator_force.copy())
            rec["err"].append([tg[j] - d.qpos[q] for j, q in zip(jnames, jq)])
            root = m.body_rootid[1] if m.nbody > 1 else 0
            rec["com"].append(d.subtree_com[root].copy())
            cons = _robot_floor_contacts(m, d, floor_id) if floor_id >= 0 else []
            # the body's tilt (its z axis from vertical) and the floor contacts of geoms that
            # are not feet: what a fall looks like (the CoP cannot show it, see below)
            zx = float(np.clip(d.xmat[root_body][8], -1.0, 1.0))
            rec["tilt"].append(math.degrees(math.acos(zx)))
            rec["touch"].append({c["geom"] for c in cons if c["geom"] not in foot_geoms})
            fz = sum(max(c["force"][2], 0.0) for c in cons)
            if fz > 1e-6:
                cop = sum(c["pos"][:2] * max(c["force"][2], 0.0) for c in cons) / fz
                rec["cop"].append(cop)
                rec["support"].append(np.array([c["pos"][:2] for c in cons]))
            else:
                rec["cop"].append(None)
                rec["support"].append(None)
            if record_reactions:
                mujoco.mj_rnePostConstraint(m, d)
                rec["react"].append(d.cfrc_int.copy())
                rec["contacts"].append(cons)
                rec["xmat"].append(d.xmat.copy())
                rec["xpos"].append(d.xpos.copy())
                rec["subcom"].append(d.subtree_com.copy())
        mujoco.mj_step(m, d)
    return names, jnames, rec


def _settle(m, d, seconds: float) -> None:
    jnames = trajmod.actuated(m)
    for a, j in enumerate(jnames):
        d.ctrl[a] = trajmod.qpos0_of(m, j)
    for _ in range(int(round(seconds / m.opt.timestep))):
        mujoco.mj_step(m, d)


def _inverse(m, rec, active, plan, jnames, free: bool):
    """Joint torques by inverse dynamics. A fixed base: the ideal plan itself (exact). A free
    base: the recorded (contact-consistent) motion with finite-difference accelerations."""
    dofs = [int(m.joint(j).dofadr[0]) for j in jnames]
    d2 = mujoco.MjData(m)
    out = []
    if not free:
        # the ideal motion's own derivatives, taken on the plan's grid (smooth) and then
        # sampled: differencing the linearly interpolated targets would spike at grid nodes
        ts = np.array(rec["t"])
        q, qd, qdd = {}, {}, {}
        for j in jnames:
            v = plan.targets[j]
            d1 = np.gradient(v, plan.times)
            d2_ = np.gradient(d1, plan.times)
            q[j] = np.interp(ts, plan.times, v)
            qd[j] = np.interp(ts, plan.times, d1)
            qdd[j] = np.interp(ts, plan.times, d2_)
        for i in range(len(ts)):
            d2.qpos[:] = m.qpos0
            d2.qvel[:] = 0
            d2.qacc[:] = 0
            for j in jnames:
                jid = m.joint(j).id
                qa, da = m.jnt_qposadr[jid], m.jnt_dofadr[jid]
                d2.qpos[qa] = q[j][i]
                d2.qvel[da] = qd[j][i]
                d2.qacc[da] = qdd[j][i]
            mujoco.mj_inverse(m, d2)
            out.append([d2.qfrc_inverse[k] for k in dofs])
        return np.array(out)
    Q = rec["qpos"]
    dt = SAMPLE_S
    for i in range(len(Q)):
        if i == 0 or i == len(Q) - 1:
            out.append([0.0] * len(dofs))
            continue
        d2.qpos[:] = Q[i]
        v1, v2 = np.zeros(m.nv), np.zeros(m.nv)
        mujoco.mj_differentiatePos(m, v1, dt, Q[i - 1], Q[i])
        mujoco.mj_differentiatePos(m, v2, dt, Q[i], Q[i + 1])
        d2.qvel[:] = (v1 + v2) / 2
        d2.qacc[:] = (v2 - v1) / dt
        mujoco.mj_inverse(m, d2)
        out.append([d2.qfrc_inverse[k] for k in dofs])
    return np.array(out)


def _mean_filter(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) <= n or arr.ndim != 2 or not arr.shape[1]:
        return arr
    k = np.ones(n) / n
    return np.vstack([np.convolve(arr[:, c], k, mode="same") for c in range(arr.shape[1])]).T


def _inverse_bands(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A free base's inverse dynamics (finite-difference accelerations) as (actuator demand,
    impact): the demand is band-limited to what a servo's loop can follow (a 20 ms mean, ~25 Hz
    to its first null); foot-strike spikes shorter than that load the gears and structure, not
    the motor, and stay in the impact series (the 10 ms mean that only removes the
    differencing noise)."""
    return (_mean_filter(arr, ACTUATOR_BAND_SAMPLES), _mean_filter(arr, INVERSE_FILTER_SAMPLES))


def task_torque(job: dict) -> dict:
    model = job["model"]
    m, d = mjmodel.compile_model(model, "dyn")
    free = bool(model["bodies"][0].get("free"))
    t0 = time.perf_counter()
    plan = trajmod.plan(m, model, job.get("trajectory") or {"kind": "hold"})
    plan_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    # a lower leg's own box may graze the floor beside its foot: only bodies without a foot
    # (the trunk, hips, thighs) touching down is a fall
    foot_bodies = {b["name"] for b in model["bodies"]
                   if any(g.get("foot") for g in b.get("geoms", []))}
    feet = frozenset(m.geom(g["name"]).id for b in model["bodies"] for g in b.get("geoms", [])
                     if b["name"] in foot_bodies)
    names, jnames, rec = _simulate(m, d, plan, bool(job.get("reactions")), _floor_id(m), feet)
    sim_s = time.perf_counter() - t0
    if any(not np.all(np.isfinite(q)) for q in rec["qpos"]):
        raise RuntimeError("the simulation went unstable (NaN): check masses, inertias and "
                           "joint frames")
    ts = np.array(rec["t"])
    active = ts >= plan.start_s - 1e-9
    F_raw = np.array(rec["force"])
    # the stiff PD answers a foot strike within ms by hitting its force limit; the motor's
    # demand is the same servo band as the inverse dynamics (the raw peak is the impact)
    F = _mean_filter(F_raw, ACTUATOR_BAND_SAMPLES) if free else F_raw
    V = np.array(rec["qvel"])
    inv = _inverse(m, rec, active, plan, jnames, free)
    inv, impact = _inverse_bands(inv) if free else (inv, inv)
    joints = []
    for a, j in enumerate(jnames):
        dof = int(m.joint(j).dofadr[0])
        f, w, iv = F[active, a], V[active, dof], inv[active, a]
        err = np.array(rec["err"])[active, a]
        joints.append({
            "joint": j, "actuator": names[a],
            "peak_servo": _r(np.max(np.abs(f)) if len(f) else 0.0),
            "rms_servo": _r(np.sqrt(np.mean(f ** 2)) if len(f) else 0.0),
            "peak_inverse": _r(np.max(np.abs(iv)) if len(iv) else 0.0),
            "peak_impact": _r(max(np.max(np.abs(impact[active, a])),
                                  np.max(np.abs(F_raw[active, a]))) if len(iv) else 0.0),
            "rms_inverse": _r(np.sqrt(np.mean(iv ** 2)) if len(iv) else 0.0),
            "peak_speed": _r(np.max(np.abs(w)) if len(w) else 0.0),
            "rms_speed": _r(np.sqrt(np.mean(w ** 2)) if len(w) else 0.0),
            "peak_power_w": _r(np.max(np.abs(f * w)) if len(f) else 0.0),
            "max_tracking_error": _r(np.max(np.abs(err)) if len(err) else 0.0),
            # (speed, torque) operating points for the torque-speed check
            "points": _r(_thin([[abs(float(x)), abs(float(y))] for x, y in
                                zip(w, np.maximum(np.abs(f), np.abs(iv)))], 160), 5),
            # ... and the servo's own (what it drew), for a servo that kept up
            "points_servo": _r(_thin([[abs(float(x)), abs(float(y))] for x, y in
                                      zip(w, np.abs(f))], 160), 5),
            "series": {"t": _r(_thin(ts[active].tolist())),
                       "servo": _r(_thin(f.tolist())), "inverse": _r(_thin(iv.tolist())),
                       "target": _r(_thin([plan.at(t)[j] for t in ts[active]]))}})
    out = {"joints": joints, "duration_s": _r(plan.duration_s - plan.start_s, 4),
           "settle_s": plan.start_s, "notes": plan.notes, "ik_error_m": _r(plan.ik_error_m),
           "free_base": free, "plan_s": _r(plan_s, 4), "sim_s": _r(sim_s, 4),
           "realtime_x": _r(plan.duration_s / max(sim_s, 1e-9), 1),
           "total_mass_kg": _r(sum(m.body_mass))}
    if free:
        out["stability"] = _trajectory_stability(rec, active, model, m)
        # H4: how far the body travelled over the motion (a gait's forward progress)
        idx = [k for k, ok in enumerate(active) if ok]
        if idx and rec.get("com"):
            a, b = np.array(rec["com"][idx[0]]), np.array(rec["com"][idx[-1]])
            out["com_travel_m"] = _r(float(np.hypot(*(b - a)[:2])))
            # the gait's real speed at full stride (between the ramp-in and the ramp-out)
            tj = job.get("trajectory") or {}
            if tj.get("kind") in ("trot", "walk"):
                period = float(tj.get("period_s") or (1.2 if tj["kind"] == "walk" else 0.6))
                cycles = int(tj.get("cycles", 3))
                t0, t1 = plan.start_s + period / 2, plan.start_s + period * cycles - period / 2
                ks = [k for k in idx if t0 <= rec["t"][k] <= t1]
                if len(ks) > 1 and t1 > t0:
                    pa, pb = np.array(rec["com"][ks[0]]), np.array(rec["com"][ks[-1]])
                    out["com_speed_mps"] = _r(float(np.hypot(*(pb - pa)[:2])) /
                                              (rec["t"][ks[-1]] - rec["t"][ks[0]]), 4)
    if job.get("reactions"):
        out["reactions"] = _reactions(m, rec, active)
    return out


def _trajectory_stability(rec, active, model, m=None) -> dict:
    """The CoP's margin inside the contacts' hull (a quasi-static gait's measure: the CoP is a
    positive-weighted mean of the contact points, so it is never outside, and with two feet
    down, a trot, it is 0 by construction), plus what shows a fall in any gait: the body's
    largest tilt, how far the CoM dropped below where the motion started, and the parts that
    are not feet that touched the floor."""
    idx = [k for k, ok in enumerate(active) if ok]
    tilts = [rec["tilt"][k] for k in idx] if rec.get("tilt") else []
    touched = sorted({m.body(int(m.geom_bodyid[g])).name.removeprefix("b_")
                      for k in idx for g in rec["touch"][k]}) if m is not None and \
        rec.get("touch") else []
    com_z = [c[2] for c in rec["com"]]
    z0 = com_z[idx[0]] if idx else (com_z[0] if com_z else 0.0)
    body = {"max_tilt_deg": _r(max(tilts) if tilts else 0.0, 2),
            "com_drop_m": _r(max(0.0, z0 - min(com_z[k] for k in idx)) if idx else 0.0),
            "non_foot_floor_contacts": touched}
    margins = []
    for k in idx:
        if rec["cop"][k] is None:
            continue
        margin, _ = _hull_margin(rec["support"][k], rec["cop"][k])
        margins.append((rec["t"][k], margin))
    if not margins:
        return {"min_cop_margin_m": None, "note": "no floor contact during the motion", **body}
    t_min, mmin = min(margins, key=lambda x: x[1])
    return {"min_cop_margin_m": _r(mmin), "at_s": _r(t_min, 4),
            "com_z_range_m": _r([min(com_z), max(com_z)]), **body,
            "series": {"t": _r(_thin([x[0] for x in margins])),
                       "margin": _r(_thin([x[1] for x in margins]))}}


def _reactions(m, rec, active) -> list[dict]:
    """Per link with a parent joint: the force + moment its parent exerts on it at the joint
    anchor (world frame, from cfrc_int shifted to the anchor), peak over the motion; at the
    peak, the child-joint reactions and floor contacts acting on the link too."""
    out = []
    idx = [k for k, ok in enumerate(active) if ok]
    for b in range(1, m.nbody):
        if m.body_jntnum[b] == 0:
            continue
        jid = int(m.body_jntadr[b])
        if int(m.jnt_type[jid]) == FREE:
            continue
        root = int(m.body_rootid[b])
        mags, vecs = [], []
        for k in idx:
            f6 = rec["react"][k][b]
            xa = _anchor(m, rec, k, jid, b)
            c = rec["subcom"][k][root]     # cfrc_int is about the tree root's subtree com
            force = f6[3:]
            moment = f6[:3] + np.cross(c - xa, force)
            mags.append(float(np.linalg.norm(force)))
            vecs.append((force, moment, xa))
        if not mags:
            continue
        kk = int(np.argmax(mags))
        k = idx[kk]
        force, moment, xa = vecs[kk]
        axis = _axis(m, rec, k, jid, b)
        m_perp = moment - np.dot(moment, axis) * axis if axis is not None else moment
        children = []
        for cb in range(1, m.nbody):
            if m.body_parentid[cb] != b or m.body_jntnum[cb] == 0:
                continue
            cj = int(m.body_jntadr[cb])
            f6 = rec["react"][k][cb]
            ca = _anchor(m, rec, k, cj, cb)
            cf = f6[3:]
            cm = f6[:3] + np.cross(rec["subcom"][k][int(m.body_rootid[cb])] - ca, cf)
            # the child pushes back on this link: minus the parent's force on the child
            cax = _axis(m, rec, k, cj, cb)
            children.append({"body": m.body(cb).name, "joint": m.joint(cj).name,
                             "anchor_m": _r(ca), "force_n": _r(-cf), "moment_nm": _r(-cm),
                             "axis": _r(cax) if cax is not None else None})
        contacts = [{"pos_m": _r(c["pos"]), "force_n": _r(c["force"])}
                    for c in rec["contacts"][k] if c["body"] == b]
        out.append({"body": m.body(b).name, "joint": m.joint(jid).name,
                    "peak_force_n": _r(mags[kk], 4), "at_s": _r(rec["t"][k], 4),
                    "rms_force_n": _r(math.sqrt(sum(x * x for x in mags) / len(mags)), 4),
                    "anchor_m": _r(xa), "axis": _r(axis) if axis is not None else None,
                    "force_n": _r(force), "moment_nm": _r(moment),
                    "moment_perp_nm": _r(float(np.linalg.norm(m_perp)), 5),
                    "xmat": _r(rec["xmat"][k][b]), "xpos_m": _r(rec["xpos"][k][b]),
                    "children": children, "contacts": contacts,
                    "series": {"t": _r(_thin([rec["t"][i] for i in idx])),
                               "force_n": _r(_thin(mags), 4)}})
    return out


def _anchor(m, rec, k, jid, b) -> np.ndarray:
    xmat = rec["xmat"][k][b].reshape(3, 3)
    return rec["xpos"][k][b] + xmat @ m.jnt_pos[jid]


def _axis(m, rec, k, jid, b):
    if int(m.jnt_type[jid]) not in (HINGE, SLIDE):
        return None
    a = rec["xmat"][k][b].reshape(3, 3) @ m.jnt_axis[jid]
    return a / max(np.linalg.norm(a), 1e-12)


# ---------------------------------------------------------------- stability

def task_stability(job: dict) -> dict:
    model = job["model"]
    if not model["bodies"][0].get("free") or model.get("floor") is None:
        return {"applicable": False,
                "note": "fixed base: the robot is grounded, tip-over does not apply"}
    m, d = mjmodel.compile_model(model, "dyn")
    floor = _floor_id(m)
    _settle(m, d, float(job.get("settle_s", 0.8)))
    mujoco.mj_forward(m, d)
    root = m.body(model["bodies"][0]["name"]).id
    com = d.subtree_com[root].copy()
    cons = _robot_floor_contacts(m, d, floor)
    if not cons:
        return {"applicable": True, "standing": False,
                "note": "the robot does not touch the floor after settling (it fell or "
                        "nothing reaches the floor)"}
    pts = np.array([c["pos"][:2] for c in cons])
    margin, hull = _hull_margin(pts, com[:2])
    h = float(com[2] - model["floor"]["z"])
    tip = math.degrees(math.atan2(margin, h)) if margin > 0 else 0.0
    out = {"applicable": True, "standing": True, "com_m": _r(com), "com_height_m": _r(h),
           "support_polygon_m": _r(hull), "support_margin_m": _r(margin),
           "static_tip_angle_deg": _r(tip, 2), "contacts": len(cons),
           "weight_n": _r(sum(m.body_mass) * 9.81, 4),
           "floor_force_n": _r(sum(c["force"][2] for c in cons), 4)}
    if job.get("tilt", True):
        out["tilt_test"] = _tilt_test(model, float(job.get("tilt_max_deg", 45.0)))
    return out


def _tilt_test(model: dict, max_deg: float) -> dict:
    """Tilt gravity (= the floor) about the x and y axes by growing angles; the first angle
    at which the torso rolls over (> 20 deg off its own tilt) or slides (> 30 mm)."""
    m, d = mjmodel.compile_model(model, "dyn")
    root = m.body(model["bodies"][0]["name"]).id
    res = {}
    for name, ax in (("+x", (1, 0)), ("-x", (-1, 0)), ("+y", (0, 1)), ("-y", (0, -1))):
        tipped = slid = None
        ang = 2.5
        while ang <= max_deg + 1e-9:
            mujoco.mj_resetData(m, d)
            _settle(m, d, 0.3)
            p0 = d.xpos[root].copy()
            th = math.radians(ang)
            # floor tilted so its downhill side is +ax: gravity gets a component along ax
            g = np.array([ax[0] * math.sin(th), ax[1] * math.sin(th), -math.cos(th)]) * 9.81
            m.opt.gravity[:] = g
            _settle(m, d, 0.6)
            m.opt.gravity[:] = [0, 0, -9.81]
            zt = d.xmat[root].reshape(3, 3)[:, 2]
            off = math.degrees(math.acos(max(-1.0, min(1.0, float(zt[2])))))
            if off > ang + 20.0:
                tipped = ang
                break
            if slid is None and np.linalg.norm((d.xpos[root] - p0)[:2]) > 0.03:
                slid = ang
            ang += 2.5
        res[name] = {"tips_at_deg": tipped, "slides_at_deg": slid}
    tips = [v["tips_at_deg"] for v in res.values() if v["tips_at_deg"] is not None]
    return {"directions": res, "min_tip_deg": min(tips) if tips else None,
            "max_tested_deg": max_deg}


# ---------------------------------------------------------------- range of motion

def task_rom(job: dict) -> dict:
    model = job["model"]
    m, d = mjmodel.compile_model(model, "rom")
    rest = {tuple(p["bodies"]) for p in _penetrations(m, d, set(), PENETRATION_TOL_M)}
    names = job.get("joints") or [m.joint(j).name for j in range(m.njnt)
                                  if int(m.jnt_type[j]) in (HINGE, SLIDE)]
    n = int(job.get("n", 2000))
    rng = np.random.default_rng(int(job.get("seed", 1)))
    ranges = []
    for j in names:
        jid = m.joint(j).id
        lo, hi = (m.jnt_range[jid] if m.jnt_limited[jid]
                  else (-math.pi, math.pi) if int(m.jnt_type[jid]) == HINGE
                  else (-0.05, 0.05))
        ranges.append((float(lo), float(hi)))
    # Latin hypercube: every joint's range cut into n strata, shuffled per joint
    k = len(names)
    u = (np.argsort(rng.random((n, k)), axis=0) + rng.random((n, k))) / n if k else np.zeros((n, 0))
    if k == 1:
        u = np.linspace(0, 1, n).reshape(n, 1)
    hits: dict[tuple, dict] = {}
    t0 = time.perf_counter()
    for i in range(n):
        d.qpos[:] = m.qpos0
        for c, j in enumerate(names):
            lo, hi = ranges[c]
            d.qpos[m.joint(j).qposadr[0]] = lo + (hi - lo) * u[i, c]
        mujoco.mj_forward(m, d)
        for p in _penetrations(m, d, rest, PENETRATION_TOL_M):
            key = tuple(p["bodies"])
            if key not in hits or p["depth_mm"] > hits[key]["depth_mm"]:
                hits[key] = {**p, "pose": {j: _r(d.qpos[m.joint(j).qposadr[0]])
                                           for j in names}, "first_pose": i}
            hits[key]["count"] = hits[key].get("count", 0) + 1
    worst = sorted(hits.values(), key=lambda h: -h["depth_mm"])
    return {"poses": n, "joints": names, "ranges": _r(ranges), "rest_contacts": sorted(rest),
            "collisions": worst[:20], "colliding_pairs": len(worst),
            "wall_s": _r(time.perf_counter() - t0, 4)}


TASKS = {"build": task_build, "torque": task_torque, "stability": task_stability,
         "rom": task_rom}


def run(job: dict) -> dict:
    return TASKS[job["task"]](job)
