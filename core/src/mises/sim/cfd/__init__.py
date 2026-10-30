"""Wind / aerodynamic (CFD) studies.

Solver choice (researched 2026-10-29, see the C7 report):
- SU2 (LGPL): conda-forge ships `su2` only for linux-64 / linux-aarch64, no osx-arm64 build
  (https://anaconda.org/conda-forge/su2, api.anaconda.org/package/conda-forge/su2); the SU2
  download page offers a macOS binary without naming the architecture
  (https://su2code.github.io/download.html). Not installable by Mises; detected only.
- OpenFOAM: conda-forge `openfoam` is linux-64 only; on a Mac it runs in a container (Docker
  or OrbStack). Detected only, never required.
- Built-in: a numpy D3Q19 lattice-Boltzmann solver (solver.py), labelled "low fidelity", with an
  empirical shape-class estimate (labelled "estimate") when the lattice does not fit the time
  or memory budget. This is the solver Mises runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np

from ..proc import run_sandboxed
from ..types import SimError

SU2_CANDIDATES = [str(Path("~/miniconda3/envs/mises-su2/bin/SU2_CFD").expanduser()),
                  str(Path("~/miniforge3/envs/mises-su2/bin/SU2_CFD").expanduser()),
                  "/opt/homebrew/bin/SU2_CFD", "/usr/local/bin/SU2_CFD"]
DOCKER_CANDIDATES = ["/usr/local/bin/docker", "/opt/homebrew/bin/docker",
                     str(Path("~/.orbstack/bin/docker").expanduser())]


def _exe(p: str | None) -> str | None:
    return p if p and os.path.isfile(p) and os.access(p, os.X_OK) else None


def find_su2() -> str | None:
    return (_exe(os.environ.get("MISES_SU2")) or _exe(shutil.which("SU2_CFD"))
            or next((c for c in SU2_CANDIDATES if _exe(c)), None))


def find_container_runtime() -> str | None:
    return _exe(shutil.which("docker")) or next((c for c in DOCKER_CANDIDATES if _exe(c)), None)


def cfd_solver_status() -> list[dict]:
    """Rows for the system check (C6's GET /system/check): the built-in solver is always ok;
    SU2 and an OpenFOAM container are optional and only detected (never installed: SU2 has no
    osx-arm64 conda-forge build). Same row shape as /system/check."""
    su2 = find_su2()
    dock = find_container_runtime()
    return [
        {"id": "cfd_builtin", "name": "Wind solver (built-in lattice-Boltzmann)", "status": "ok",
         "version": "D3Q19 LES", "path": None,
         "detail": "always available; low fidelity (coarse lattice, modelled skin friction)",
         "fix": None},
        {"id": "su2", "name": "SU2 (optional CFD solver)",
         "status": "ok" if su2 else "optional_missing", "version": None, "path": su2,
         "detail": ("found; Mises still uses the built-in solver (SU2 runs are not wired up "
                    "yet)") if su2 else ("not installed; conda-forge has no macOS arm64 build, "
                                         "the built-in solver is used"),
         "fix": None if su2 else {"kind": "open_url", "label": "SU2 downloads",
                                  "url": "https://su2code.github.io/download.html"}},
        {"id": "openfoam_container", "name": "OpenFOAM via Docker / OrbStack (optional)",
         "status": "ok" if dock else "optional_missing", "version": None, "path": dock,
         "detail": ("container runtime found; OpenFOAM runs are not wired up yet") if dock
         else "no Docker or OrbStack; not needed (the built-in solver is used)",
         "fix": None},
    ]


WORKER_MODULE = "mises.sim.cfd.worker"
MAX_MEMORY_MB = 8192
OS_RESERVE_MB = 6 * 1024


def memory_cap_mb(ram_gb: float, local_models_gb: float = 0.0) -> int:
    """The most a wind study may use: min(8192, RAM - 6 GB for macOS, Mises and the CAD
    kernel - what running local models hold), never below the 64 MB minimum."""
    ram_gb = ram_gb if ram_gb > 0 else 16.0  # unknown: assume a 16 GB Mac
    free = ram_gb * 1024 - OS_RESERVE_MB - local_models_gb * 1024
    return int(max(64, min(MAX_MEMORY_MB, free)))


async def run_wind_job(tris_part: np.ndarray, settings: dict, job_dir: Path, *,
                       progress=None) -> dict:
    """Run one wind study in the sandboxed CFD worker; returns its result dict. Outputs
    (result.glb, fields.npz, slice PNGs) land in job_dir. Cancel kills the process group."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    np.save(job_dir / "tris.npy", np.asarray(tris_part, dtype=np.float64))
    (job_dir / "input.json").write_text(json.dumps(settings))
    poll = asyncio.create_task(_poll(job_dir / "progress.json", progress))
    try:
        rc, _, err = await run_sandboxed(
            [sys.executable, "-I", "-m", WORKER_MODULE, str(job_dir.resolve())], job_dir,
            timeout_s=settings["timeout_s"] + 30, what="the wind solver",
            memory_limit_mb=float(settings["memory_mb"]))
    finally:
        poll.cancel()
    out = job_dir / "result.json"
    if rc != 0 or not out.exists():
        msg = next((ln for ln in err.splitlines() if ln.startswith("CFD_ERROR")), err.strip())
        raise SimError("solve_failed", f"wind solver failed: {msg[-800:] or f'exit {rc}'}",
                       hint=failure_hint(msg))
    return json.loads(out.read_text())


def failure_hint(msg: str) -> str:
    """The fix for a failed wind run. An under-resolved body needs a FINER lattice (QA B1-5:
    the old one-size hint said 'coarser', which makes a thin part worse); a lattice over the
    memory or time budget needs a coarser one."""
    if "too thin" in msg or "smaller than one lattice cell" in msg:
        return "use a finer refinement, or solver 'empirical' for a shape-class estimate"
    return "try a coarser refinement, a longer timeout or solver 'empirical'"


async def _poll(path: Path, progress) -> None:
    last = None
    while True:
        await asyncio.sleep(0.5)
        if progress is None:
            continue
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if d != last:
            last = d
            progress(d)
