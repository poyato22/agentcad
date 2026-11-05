"""SIMP compliance minimization under a volume constraint, with the OC update.

E(x) = Emin + x^p (E0 - Emin), p = 3, Emin = 1e-4 E0. Several load cases minimize their
weighted compliance sum. Elements outside the design space and keep-outs are passive void,
keep-ins passive solid; the volume fraction is of the whole design space (keep-ins count).
Deterministic: the same problem and options give the same densities bit for bit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import fe, filters

CG_FALLBACK_ITERS = 80     # a V-cycle this slow cannot see thin members: drop to 2 levels
REFACTOR_EVERY = 3
MIN_ITERS = 15
CHECKPOINT_EVERY = 5


@dataclass
class Problem:
    n: tuple[int, int, int]
    h: float
    space: np.ndarray            # bool[ne] design space (keep-ins included)
    keep_in: np.ndarray          # bool[ne] passive solid
    keep_out: np.ndarray         # bool[ne] passive void inside the space
    fixed_dofs: np.ndarray       # bool[ndof]
    loads: list[tuple[np.ndarray, float]]   # (nodal force vector, weight) per load case
    E: float
    nu: float
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def ne(self) -> int:
        return int(np.prod(self.n))

    @property
    def active(self) -> np.ndarray:
        return self.space & ~self.keep_in & ~self.keep_out

    @property
    def void(self) -> np.ndarray:
        return ~self.space | self.keep_out

    @property
    def solver_elements(self) -> np.ndarray:
        """The elements the FE model holds: a void one adds nothing but cost."""
        return self.space & ~self.keep_out


@dataclass
class Options:
    volume_fraction: float = 0.25
    penal: float = 3.0
    filter_radius: float = 2.0          # in elements
    emin_rel: float = 1e-4
    max_iter: int = 60
    ctol: float = 1e-3                  # stop: compliance change < 0.1% over 5 iterations
    xtol: float = 0.01                  # ... or the largest design change below this
    cg_tol: float = 1e-4
    move: float = 0.2
    mirror_axes: tuple[int, ...] = ()
    extrusion_axis: int | None = None
    overhang_dir: str | None = None     # "z", "-z", "x", ...
    crisp: bool = False                 # Heaviside projection with continuation
    beta_steps: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)


@dataclass
class DensityResult:
    x: np.ndarray                       # physical densities (ne,)
    compliance: float                   # gray design, weighted sum (N mm)
    compliance_bw: float                # thresholded at 0.5, same voxel model
    case_compliance_bw: list[float]
    deflection_bw: list[float]          # mean displacement along each case's load (mm)
    vf_bw: float                        # thresholded volume / design space volume
    iters: int
    converged: bool
    history: list[dict] = field(default_factory=list)
    levels: int = 3
    fallback_at: int | None = None
    seconds: float = 0.0


class InfeasibleProblem(ValueError):
    pass


def chain_for(p: Problem, o: Options) -> filters.Chain:
    H, Hs = filters.density_filter(p.n, o.filter_radius, p.space)
    return filters.Chain(
        p.n, H, Hs, mirrors=[filters.mirror_index(p.n, a) for a in o.mirror_axes],
        columns=(filters.column_groups(p.n, o.extrusion_axis, p.space)
                 if o.extrusion_axis is not None else None),
        overhang=filters.Overhang(p.n, o.overhang_dir) if o.overhang_dir else None)


def _solver(p: Problem, levels: int) -> fe.MGCG:
    return fe.MGCG(p.n, p.h, p.nu, p.fixed_dofs, levels=levels, elements=p.solver_elements)


def _solve_all(mg: fe.MGCG, p: Problem, U: list, tol: float) -> tuple[list, int]:
    its = 0
    free = mg.fine.free
    out = []
    for (f, _w), u0 in zip(p.loads, U):
        u, it = mg.solve(f * free, u0, tol=tol)
        out.append(u)
        its = max(its, it)
    return out, its


def optimize(p: Problem, o: Options = Options(),
             on_iter: Callable[[int, dict, np.ndarray], None] | None = None,
             resume: dict | None = None,
             checkpoint: Callable[[dict], None] | None = None) -> DensityResult:
    """resume: a dict `checkpoint` was given earlier ({x, it, stage}): carry on from there.
    A restart then costs at most CHECKPOINT_EVERY iterations, not the hour already spent."""
    t0 = time.perf_counter()
    ne = p.ne
    active, keep_in, void = p.active, p.keep_in, p.void
    n_space = int(p.space.sum())
    if n_space == 0:
        raise InfeasibleProblem("the design space has no elements")
    if not active.any():
        raise InfeasibleProblem("keep regions fill the whole design space")
    target = o.volume_fraction * n_space
    if keep_in.sum() >= target:
        raise InfeasibleProblem(
            f"the keep regions alone are {keep_in.sum() / n_space:.0%} of the design space, "
            f"over the {o.volume_fraction:.0%} volume target")
    chain = chain_for(p, o)
    levels = fe.choose_levels(p.n, 3)
    mg = _solver(p, levels)
    E0, Emin = p.E, p.E * o.emin_rel
    stages = o.beta_steps if o.crisp else ()
    if stages:
        chain.beta = stages[0]
    stage_len = max(1, o.max_iter // (len(stages) + 1)) if stages else 0
    stage = 0
    x = np.zeros(ne)
    x[active] = np.clip((target - keep_in.sum()) / active.sum(), 0.01, 1.0)
    x[keep_in] = 1.0

    def physical(v: np.ndarray) -> np.ndarray:
        xp = chain.forward(v)
        xp[keep_in] = 1.0
        xp[void] = 0.0
        return xp

    start_it = 0
    if resume is not None:
        x = np.asarray(resume["x"], float).copy()
        start_it = int(resume["it"])
        stage = int(resume["stage"])
        if stages:
            chain.beta = stages[stage]
    xPhys = physical(x)
    U: list = [None] * len(p.loads)
    hist: list[dict] = []
    fallback_at = None
    converged = False
    ke = mg.fine.ke
    cg_ref: int | None = None
    cg_prev = 0
    for it in range(start_it, max(o.max_iter, start_it + 1)):
        Ee = Emin + xPhys ** o.penal * (E0 - Emin)
        ts = time.perf_counter()
        # a 2-level cycle's coarse LU is the expensive part: refresh it every REFACTOR_EVERY
        # iterations, or as soon as the stale one makes CG noticeably slower
        refactor = (len(mg.levels) > 2 or it % REFACTOR_EVERY == 0
                    or (cg_ref is not None and cg_prev > 1.5 * cg_ref))
        mg.set_density(Ee, refactor=refactor)
        U, cg = _solve_all(mg, p, U, o.cg_tol)
        if refactor:
            cg_ref = cg
        cg_prev = cg
        if cg >= CG_FALLBACK_ITERS and len(mg.levels) > 2:
            mg = _solver(p, 2)
            ke = mg.fine.ke
            mg.set_density(Ee)
            U, cg2 = _solve_all(mg, p, U, o.cg_tol)
            cg += cg2
            cg_ref = cg_prev = cg2
            fallback_at = it
        tsolve = time.perf_counter() - ts
        c = 0.0
        dc = np.zeros(ne)
        el = mg.fine.el
        for (_f, w), u in zip(p.loads, U):
            Ue = u[mg.fine.edof]
            ce = ((Ue @ ke) * Ue).sum(1)
            c += w * float(Ee[el] @ ce)
            dc[el] -= w * o.penal * (E0 - Emin) * xPhys[el] ** (o.penal - 1) * ce
        dc[keep_in | void] = 0.0
        dv = np.ones(ne)
        dv[keep_in | void] = 0.0
        # the volume constraint is on xPhys: both sensitivities go back through the chain
        dc = chain.backward(dc)
        dv = chain.backward(dv)
        dv = np.maximum(dv, 1e-12)
        l1, l2 = 0.0, 1e9
        xold = x.copy()
        xn = x
        # a sharp projection turns a large design step into a jump in xPhys
        move = o.move if chain.beta < 4 else o.move / 2
        while (l2 - l1) / (l1 + l2 + 1e-30) > 1e-4:
            lm = 0.5 * (l1 + l2)
            xn = np.clip(x * np.sqrt(np.maximum(-dc, 0) / (dv * lm)),
                         np.maximum(0.0, x - move), np.minimum(1.0, x + move))
            xn[keep_in], xn[void] = 1.0, 0.0
            if physical(xn).sum() > target:
                l1 = lm
            else:
                l2 = lm
        x = xn
        xPhys = physical(x)
        ch = float(np.abs(x - xold).max())
        row = {"it": it, "c": c, "vf": float(xPhys.sum() / n_space), "ch": ch, "cg": cg,
               "solve_s": round(tsolve, 3)}
        hist.append(row)
        if on_iter is not None:
            on_iter(it, row, xPhys)
        if checkpoint is not None and (it + 1) % CHECKPOINT_EVERY == 0:
            checkpoint({"x": x, "it": it + 1, "stage": stage})
        cs = [r["c"] for r in hist[-6:]]
        dcrel = (max(abs(cs[q + 1] - cs[q]) for q in range(len(cs) - 1)) / c
                 if len(cs) > 5 else 1.0)
        settled = it + 1 >= MIN_ITERS and (ch < o.xtol or dcrel < o.ctol)
        if stages and stage + 1 < len(stages):
            # continuation: the next sharpness when this one has settled or used its share
            if settled or (it + 1) >= (stage + 1) * stage_len:
                stage += 1
                chain.beta = stages[stage]
                xPhys = physical(x)
                hist[-1]["beta"] = chain.beta
            continue
        if settled and (not stages or (it + 1) >= (stage + 1) * stage_len):
            converged = True
            break
    # crisp check: threshold at 0.5 and re-solve with the same voxel model
    xb = (xPhys > 0.5).astype(float)
    xb[keep_in] = 1.0
    mg.set_density(Emin + xb * (E0 - Emin))
    Ub, _ = _solve_all(mg, p, U, o.cg_tol * 0.1)
    case_c, defl = [], []
    for (f, _w), u in zip(p.loads, Ub):
        ff = f * mg.fine.free
        case_c.append(float(ff @ u))
        nz = np.flatnonzero(ff)
        mag = np.abs(ff[nz]).sum()
        # the mean displacement along the load, weighted by the nodal forces
        defl.append(float((ff[nz] * u[nz]).sum() / mag) if mag > 0 else 0.0)
    c_bw = float(sum(w * c for (_f, w), c in zip(p.loads, case_c)))
    return DensityResult(x=xPhys, compliance=hist[-1]["c"], compliance_bw=c_bw,
                         case_compliance_bw=case_c, deflection_bw=defl,
                         vf_bw=float(xb[p.space].sum() / n_space), iters=len(hist),
                         converged=converged, history=hist, levels=len(mg.levels),
                         fallback_at=fallback_at, seconds=time.perf_counter() - t0)
