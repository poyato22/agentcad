"""Built-in wind solver: a vectorized numpy D3Q19 lattice-Boltzmann (BGK, optional Smagorinsky
LES sub-grid model) around a voxelized body. LOW FIDELITY.

Lattice frame: the flow runs along +x. The inlet plane (x=0) is held at the free-stream
equilibrium, the outlet (x=nx-1) copies its upstream neighbour (zero gradient), the four side
walls are periodic (a tunnel without wall boundary layers). The body is a staircase of solid
cells with half-way bounce-back.

Forces, per lattice step:
- momentum exchange over every fluid-solid link: the total (pressure + friction) force;
- the pressure force: p = rho / 3 integrated over every exposed solid cell face.
On these coarse grids the boundary layer is not resolved, so the momentum-exchange friction is
far too high (sphere at 12 cells: Cd 0.72 vs ~0.47 measured). The reported drag is therefore
the resolved pressure drag plus a skin-friction estimate from a flat-plate correlation at the
real Reynolds number (see wind.py), and the raw lattice total is kept for transparency.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

C = np.array([[0, 0, 0], [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1],
              [1, 1, 0], [-1, -1, 0], [1, -1, 0], [-1, 1, 0], [1, 0, 1], [-1, 0, -1],
              [1, 0, -1], [-1, 0, 1], [0, 1, 1], [0, -1, -1], [0, 1, -1], [0, -1, 1]])
W = np.array([1 / 3] + [1 / 18] * 6 + [1 / 36] * 12, dtype=np.float32)
OPP = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17])
_CF = C.astype(np.float32)
_PAIRS = [(a, b) for a in range(3) for b in range(a, 3)]
_CC = np.stack([_CF[:, a] * _CF[:, b] for a, b in _PAIRS])            # (6, 19)
_MULT = np.array([1.0 if a == b else 2.0 for a, b in _PAIRS], dtype=np.float32)
# Peak resident memory per lattice cell, MEASURED (ru_maxrss of run_lbm on this Mac: 1075 B at
# 0.5 M cells, 920 B at 1 M; the 19-float arrays are only ~440 B, the rest is numpy temporaries
# in feq / the LES model and the float64 field averages), rounded up; plus the interpreter.
BYTES_PER_CELL = 1100
FIXED_MB = 150
NS_PER_CELL_STEP = 110.0           # measured on an M-series Mac (numpy, float32); a guide


class Cancelled(Exception):
    pass


def feq(rho: np.ndarray, u: np.ndarray) -> np.ndarray:
    cu = np.tensordot(_CF, u, axes=(1, 0))
    usq = (u * u).sum(0)
    return W.reshape((19,) + (1,) * rho.ndim) * rho * (1 + 3 * cu + 4.5 * cu * cu - 1.5 * usq)


@dataclass
class LbmResult:
    steps: int
    seconds: float
    force_total: np.ndarray          # time-averaged (lattice units), momentum exchange
    force_pressure: np.ndarray       # time-averaged pressure force
    moment_pressure: np.ndarray      # about `moment_ref` (lattice units)
    history: list[dict] = field(default_factory=list)   # [{step, fx, fy, fz, px, py, pz}]
    converged: bool = False
    rho_mean: np.ndarray | None = None
    u_mean: np.ndarray | None = None
    stopped: str = "iterations"      # "iterations" | "converged" | "time"


def run_lbm(solid: np.ndarray, *, u0: float, nu: float, steps: int, smagorinsky: float = 0.16,
            time_budget_s: float = 1e9, sample_every: int = 10, average_from: float = 0.5,
            moment_ref=None, tol: float = 0.02, min_steps: int = 0, progress=None,
            should_stop=None) -> LbmResult:
    """Run up to `steps` lattice steps. Forces and fields are averaged over the samples after
    `average_from` x the steps run; the run stops early once the mean drag of the last two
    windows agree within `tol` (after `min_steps`), or when `time_budget_s` is used up."""
    shape = solid.shape
    ny, nz = shape[1], shape[2]
    tau = 3.0 * nu + 0.5
    cs2 = np.float32(18 * np.sqrt(2) * smagorinsky ** 2)
    rho = np.ones(shape, np.float32)
    u = np.zeros((3,) + shape, np.float32)
    u[0] = u0
    u[:, solid] = 0
    f = feq(rho, u).astype(np.float32)
    fluid = ~solid
    links = [None] + [np.nonzero(fluid & np.roll(solid, tuple(C[i]), axis=(0, 1, 2)))
                      for i in range(1, 19)]
    uin = np.zeros((3, 1, ny, nz), np.float32)
    uin[0] = u0
    f_in = feq(np.ones((1, ny, nz), np.float32), uin)[:, 0]
    if moment_ref is None:
        moment_ref = np.argwhere(solid).mean(0) if solid.any() else np.zeros(3)
    # exposed faces: fluid cell x with solid at x + c_i (i = 1..6) pushes the body along c_i
    faces = []
    for i in range(1, 7):
        idx = links[OPP[i]]
        pos = np.stack(idx, 1).astype(np.float64) + 0.5 * C[i] - moment_ref
        faces.append((idx, C[i].astype(np.float64), pos))
    hist: list[dict] = []
    n_acc = 0
    rho_sum = np.zeros(shape, np.float64)
    u_sum = np.zeros((3,) + shape, np.float64)
    t0 = time.monotonic()
    stopped, converged, step = "iterations", False, 0
    fn = np.empty_like(f)
    for step in range(1, steps + 1):
        rho = f.sum(0)
        u = np.tensordot(_CF.T, f, axes=(1, 0)) / rho
        fe = feq(rho, u)
        fneq = f - fe
        if smagorinsky > 0:
            P = np.tensordot(_CC, fneq, axes=(1, 0))
            Q = np.sqrt(np.tensordot(_MULT, P * P, axes=(0, 0)))
            tau_e = 0.5 * (tau + np.sqrt(tau * tau + cs2 * Q / rho))
            fp = f - fneq / tau_e
        else:
            fp = f - fneq / np.float32(tau)
        for i in range(19):
            fn[i] = np.roll(fp[i], tuple(C[i]), axis=(0, 1, 2))
        ft = np.zeros(3)
        for i in range(1, 19):
            o = OPP[i]
            vals = fp[o][links[i]]
            fn[i][links[i]] = vals
            ft += 2.0 * float(vals.sum(dtype=np.float64)) * C[o]
        fn[:, 0] = f_in
        fn[:, -1] = fn[:, -2]
        f, fn = fn, f
        if step % sample_every == 0 or step == steps:
            fpres = np.zeros(3)
            mom = np.zeros(3)
            for idx, n, pos in faces:
                p = rho[idx].astype(np.float64) / 3.0
                fpres += p.sum() * n
                mom += np.cross(pos, np.outer(p, n)).sum(0)
            hist.append({"step": step, "fx": ft[0], "fy": ft[1], "fz": ft[2],
                         "px": fpres[0], "py": fpres[1], "pz": fpres[2],
                         "mx": mom[0], "my": mom[1], "mz": mom[2]})
            if step >= average_from * steps or (min_steps and step >= average_from * min_steps):
                n_acc += 1
                rho_sum += rho
                u_sum += u
            if not np.isfinite(ft).all() or not np.isfinite(rho).all():
                raise FloatingPointError("the lattice solution diverged")
            if progress is not None:
                progress(step, steps, hist[-1])
            if min_steps and step >= min_steps and _settled(hist, tol):
                stopped, converged = "converged", True
                break
            if time.monotonic() - t0 > time_budget_s:
                stopped = "time"
                break
            if should_stop is not None and should_stop():
                raise Cancelled()
    if n_acc == 0:
        n_acc = 1
        rho_sum, u_sum = rho.astype(np.float64), u.astype(np.float64)
    # forces: the mean over the second half of the samples actually run (past the start-up
    # transient, whose momentum-exchange spikes are large on thin walls)
    tail = hist[len(hist) // 2:] or hist[-1:]

    def mean(*keys):
        return np.array([np.mean([h[k] for h in tail]) for k in keys])
    return LbmResult(steps=step, seconds=time.monotonic() - t0,
                     force_total=mean("fx", "fy", "fz"), force_pressure=mean("px", "py", "pz"),
                     moment_pressure=mean("mx", "my", "mz"),
                     history=hist, converged=converged, stopped=stopped,
                     rho_mean=(rho_sum / n_acc).astype(np.float32),
                     u_mean=(u_sum / n_acc).astype(np.float32))


def _settled(hist: list[dict], tol: float) -> bool:
    """The mean pressure drag over the last two equal windows (each a third of the run so far,
    at least 30 samples) agree within tol."""
    n = len(hist) // 3
    if n < 30:
        return False
    a = np.mean([h["px"] for h in hist[-2 * n:-n]])
    b = np.mean([h["px"] for h in hist[-n:]])
    return abs(b) > 0 and abs(a - b) / abs(b) < tol
