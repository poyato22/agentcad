"""Trajectories for the dynamics worker: keyframes, IK (damped least squares) and named
motions (squat, stand_up, trot, reach, sweep, hold). Plan 10 C3.

Worker side: numpy + mujoco only, never mises. A trajectory arrives already mapped to MuJoCo
joint names and SI units (rad, m, s) by `mises.dyn.tools`. `plan(m, model, traj)` returns a
`Plan`: joint targets on a fine time grid that the forward simulation tracks with its position
actuators, plus the time the measured part of the motion starts (after the settle).

Leg motions solve IK on a kinematic copy with the root held at its exported pose: each foot
target is given relative to the root, so the targets do not depend on how the body drifts
while it moves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import mujoco

GRID_S = 0.01               # targets are solved every 10 ms and interpolated between
IK_ITERS = 60
IK_DAMPING = 1e-3
IK_TOL_M = 2e-4
MAX_DURATION_S = 120.0


@dataclass
class Plan:
    times: np.ndarray                         # (n,) s
    targets: dict[str, np.ndarray]            # joint name -> (n,) rad or m
    start_s: float                            # measurement starts here (after the settle)
    duration_s: float
    notes: list[str] = field(default_factory=list)
    ik_error_m: float = 0.0

    def at(self, t: float) -> dict[str, float]:
        return {j: float(np.interp(t, self.times, v)) for j, v in self.targets.items()}


def _cos_blend(s: float) -> float:
    """0 -> 1 smoothly (zero velocity at both ends)."""
    s = min(max(s, 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * s)


def actuated(m) -> list[str]:
    """Hinge/slide joints driven by an actuator, in actuator order."""
    out = []
    for a in range(m.nu):
        j = int(m.actuator_trnid[a, 0])
        out.append(m.joint(j).name)
    return out


def qpos0_of(m, name: str) -> float:
    return float(m.qpos0[m.joint(name).qposadr[0]])


def feet(m, model: dict) -> list[tuple[int, np.ndarray]]:
    """(body id, foot point in that body's frame) for every geom the exporter marked a foot:
    the bottom centre of the geom at the exported pose."""
    out = []
    for b in model["bodies"]:
        for g in b.get("geoms", []):
            if not g.get("foot"):
                continue
            bid = m.body(b["name"]).id
            p = np.array(g.get("pos", [0, 0, 0]), dtype=float)
            if g.get("type", "box") == "box":
                p = p + np.array([0.0, 0.0, -float(g["size"][2])])
            else:
                p = p + np.array([0.0, 0.0, -float(g["size"][0])])
            out.append((bid, p))
    return out


def effector(m, model: dict) -> tuple[int, np.ndarray]:
    """The point of the leaf body farthest (along the tree) from the root: an arm's tip."""
    depth = {}
    for b in model["bodies"]:
        depth[b["name"]] = depth.get(b.get("parent") or "", -1) + 1
    leaf = max(model["bodies"], key=lambda b: (depth[b["name"]], b["name"]))
    bid = m.body(leaf["name"]).id
    g = (leaf.get("geoms") or [{"pos": leaf["com"], "size": [0, 0, 0]}])[0]
    return bid, np.array(g.get("pos", leaf["com"]), dtype=float)


class IK:
    """Damped least-squares IK over the actuated joints with the root held still."""

    def __init__(self, m, joints: list[str]):
        self.m = m
        self.d = mujoco.MjData(m)
        self.d.qpos[:] = m.qpos0
        self.joints = joints
        self.dofs = [int(m.joint(j).dofadr[0]) for j in joints]
        self.qadr = [int(m.joint(j).qposadr[0]) for j in joints]
        self.ranges = [(m.jnt_range[m.joint(j).id] if m.jnt_limited[m.joint(j).id] else None)
                       for j in joints]

    def point(self, bid: int, local: np.ndarray) -> np.ndarray:
        return self.d.xpos[bid] + self.d.xmat[bid].reshape(3, 3) @ local

    def solve(self, goals: list[tuple[int, np.ndarray, np.ndarray]]) -> float:
        """goals: (body id, local point, world target). Warm-starts from the last solution.
        Returns the remaining worst position error (m)."""
        m, d = self.m, self.d
        jacp = np.zeros((3, m.nv))
        err = 0.0
        for _ in range(IK_ITERS):
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            rows, es = [], []
            for bid, local, target in goals:
                p = self.point(bid, local)
                es.append(target - p)
                mujoco.mj_jac(m, d, jacp, None, p, bid)
                rows.append(jacp[:, self.dofs].copy())
            e = np.concatenate(es)
            err = float(np.max(np.abs(e))) if len(e) else 0.0
            if err < IK_TOL_M:
                break
            J = np.vstack(rows)
            dq = J.T @ np.linalg.solve(J @ J.T + IK_DAMPING * np.eye(len(e)), e)
            step = min(1.0, 0.2 / max(1e-9, float(np.max(np.abs(dq)))))
            for k, a in enumerate(self.qadr):
                v = d.qpos[a] + step * dq[k]
                r = self.ranges[k]
                if r is not None:
                    v = min(max(v, r[0]), r[1])
                d.qpos[a] = v
        return err

    def values(self) -> dict[str, float]:
        return {j: float(self.d.qpos[a]) for j, a in zip(self.joints, self.qadr)}


def _grid(duration: float) -> np.ndarray:
    if duration > MAX_DURATION_S:
        raise ValueError(f"motion of {duration:.0f} s is over {MAX_DURATION_S:g} s")
    n = max(2, int(round(duration / GRID_S)) + 1)
    return np.linspace(0.0, duration, n)


def _hold(m, joints, times) -> dict[str, np.ndarray]:
    return {j: np.full(len(times), qpos0_of(m, j)) for j in joints}


def _root_height(m, model) -> float:
    floor = (model.get("floor") or {}).get("z")
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    root = m.body(model["bodies"][0]["name"]).id
    return float(d.subtree_com[root][2] - (floor if floor is not None else 0.0))


def _leg_motion(m, model, joints, times, settle, offset_fn, notes) -> tuple[dict, float]:
    """Joint targets that put foot k at (its exported position relative to the root) +
    offset_fn(k, t) for every grid time; the root is held at its exported pose."""
    fs = feet(m, model)
    if not fs:
        raise ValueError("no feet: this motion needs a free-standing robot with feet")
    ik = IK(m, joints)
    mujoco.mj_kinematics(m, ik.d)
    home = [ik.point(bid, local).copy() for bid, local in fs]
    out = {j: np.zeros(len(times)) for j in joints}
    worst = 0.0
    for i, t in enumerate(times):
        tm = max(0.0, t - settle)
        goals = [(bid, local, home[k] + offset_fn(k, tm)) for k, (bid, local) in enumerate(fs)]
        err = ik.solve(goals) if t >= settle else 0.0
        worst = max(worst, err)
        for j, v in ik.values().items():
            out[j][i] = v
    if worst > 0.002:
        notes.append(f"IK left the feet up to {worst * 1000:.1f} mm from their targets: the "
                     "motion is partly out of reach (smaller depth or step)")
    return out, worst


def plan(m, model: dict, traj: dict) -> Plan:
    joints = actuated(m)
    kind = traj.get("kind", "hold")
    free = bool(model["bodies"][0].get("free"))
    settle = float(traj.get("settle_s", 0.5 if free else 0.0))
    notes: list[str] = []
    err = 0.0
    if kind == "hold":
        dur = settle + float(traj.get("duration_s", 1.0))
        times = _grid(dur)
        targets = _hold(m, joints, times)
    elif kind == "keyframes":
        keys = sorted(traj["keys"], key=lambda k: float(k["t"]))
        dur = settle + float(traj.get("duration_s") or keys[-1]["t"])
        times = _grid(dur)
        targets = {}
        for j in joints:
            pts_t = [0.0] + [settle + float(k["t"]) for k in keys if j in k["q"]]
            pts_v = [qpos0_of(m, j)] + [float(k["q"][j]) for k in keys if j in k["q"]]
            v = np.zeros(len(times))
            for i, t in enumerate(times):     # cosine blend between keys (smooth start/stop)
                if t <= pts_t[0] or len(pts_t) == 1:
                    v[i] = pts_v[0]
                    continue
                k = int(np.searchsorted(pts_t, t)) - 1
                k = min(max(k, 0), len(pts_t) - 2)
                t0, t1 = pts_t[k], pts_t[k + 1]
                s = _cos_blend((t - t0) / max(t1 - t0, 1e-9)) if t < pts_t[-1] else 1.0
                v[i] = pts_v[k] + (pts_v[k + 1] - pts_v[k]) * s if t < pts_t[-1] else pts_v[-1]
            targets[j] = v
    elif kind in ("squat", "stand_up"):
        h = _root_height(m, model)
        depth = float(traj.get("depth_m") or 0.3 * h)
        period = float(traj.get("period_s", 2.0))
        cycles = int(traj.get("cycles", 1))
        dur = settle + period * cycles + float(traj.get("hold_s", 0.3))

        def lift(k, t):
            if kind == "stand_up":       # crouched -> standing over one period
                s = 1.0 - _cos_blend(t / period)
            else:                        # standing -> down -> up, `cycles` times
                s = 0.5 - 0.5 * math.cos(2 * math.pi * min(t, period * cycles) / period)
            return np.array([0.0, 0.0, depth * s])
        times = _grid(dur)
        targets, err = _leg_motion(m, model, joints, times, settle, lift, notes)
        notes.append(f"{kind}: root lowered by up to {depth * 1000:.0f} mm, period "
                     f"{period:g} s")
    elif kind == "trot":
        period = float(traj.get("period_s", 0.6))
        step = float(traj.get("step_m", 0.04))
        lift_h = float(traj.get("lift_m", 0.025))
        cycles = int(traj.get("cycles", 3))
        dur = settle + period * cycles + 0.3
        fs = feet(m, model)
        d0 = mujoco.MjData(m)
        mujoco.mj_kinematics(m, d0)
        root = m.body(model["bodies"][0]["name"]).id
        c = d0.xpos[root]
        # diagonal pairs: front-left with hind-right, by the sign of x*y relative to the root
        phase = []
        for bid, local in fs:
            p = d0.xpos[bid] + d0.xmat[bid].reshape(3, 3) @ local - c
            phase.append(0.0 if p[0] * p[1] >= 0 else 0.5)

        def swing(k, t):
            if t >= period * cycles:
                return np.zeros(3)
            ph = (t / period + phase[k]) % 1.0
            if ph < 0.5:                 # swing: foot moves forward and up
                s = ph / 0.5
                return np.array([step * (s - 0.5), 0.0, lift_h * math.sin(math.pi * s)])
            s = (ph - 0.5) / 0.5         # stance: foot sweeps back under the body
            return np.array([step * (0.5 - s), 0.0, 0.0])
        times = _grid(dur)
        targets, err = _leg_motion(m, model, joints, times, settle, swing, notes)
        notes.append(f"trot in place: {cycles} cycles of {period:g} s, step {step * 1000:.0f} "
                     f"mm, lift {lift_h * 1000:.0f} mm (feet impacts: forces are estimates)")
    elif kind == "reach":
        dur = settle + float(traj.get("duration_s", 2.0))
        times = _grid(dur)
        bid, local = effector(m, model)
        ik = IK(m, joints)
        mujoco.mj_kinematics(m, ik.d)
        start = ik.point(bid, local).copy()
        goal = np.array(traj["target_m"], dtype=float)
        targets = {j: np.zeros(len(times)) for j in joints}
        for i, t in enumerate(times):
            s = _cos_blend((t - settle) / max(dur - settle, 1e-9))
            e = ik.solve([(bid, local, start + (goal - start) * s)])
            err = max(err, e)
            for j, v in ik.values().items():
                targets[j][i] = v
        if err > 0.002:
            notes.append(f"reach target out of reach by {err * 1000:.1f} mm")
    elif kind == "sweep":
        names = [j for j in traj.get("joints") or joints if j in joints]
        period = float(traj.get("period_s", 2.0))
        dur = settle + period * len(names)
        times = _grid(dur)
        targets = _hold(m, joints, times)
        for n_i, j in enumerate(names):
            jid = m.joint(j).id
            lo, hi = (m.jnt_range[jid] if m.jnt_limited[jid] else (-math.pi / 2, math.pi / 2))
            q0 = qpos0_of(m, j)
            t0 = settle + n_i * period
            for i, t in enumerate(times):
                if t0 <= t < t0 + period:
                    s = (t - t0) / period    # q0 -> hi -> lo -> q0
                    w = math.sin(2 * math.pi * s)
                    targets[j][i] = q0 + (hi - q0) * w if w >= 0 else q0 + (q0 - lo) * w
        notes.append(f"sweep: each joint through its range in turn ({period:g} s each)")
    else:
        raise ValueError(f"unknown trajectory kind {kind!r}")
    if float(times[-1]) > MAX_DURATION_S:     # never reached through the tools (capped at 60)
        raise ValueError(f"motion of {times[-1]:.0f} s is over {MAX_DURATION_S:g} s")
    return Plan(times=times, targets=targets, start_s=settle, duration_s=float(times[-1]),
                notes=notes, ik_error_m=err)
