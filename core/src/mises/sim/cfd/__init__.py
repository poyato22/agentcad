"""Wind / aerodynamic (CFD) studies.

Solver choice (researched 2026-10-29, see the C7 report):
- SU2 (LGPL): conda-forge ships `su2` only for linux-64 / linux-aarch64, no osx-arm64 build
  (https://anaconda.org/conda-forge/su2, api.anaconda.org/package/conda-forge/su2); the SU2
  download page offers a macOS binary without naming the architecture
  (https://su2code.github.io/download.html). Not installable by Mises; detected only.
- OpenFOAM: conda-forge `openfoam` is linux-64 only; on a Mac it runs in a container (Docker
  or OrbStack) from the pinned image in ..foam_image (S2: solver "openfoam", openfoam.py).
  Optional, never required: the image is pulled only by a user's click.
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

from ..foam_image import (DOCKER_CANDIDATES, OPENFOAM_IMAGE, OPENFOAM_VERSION,  # noqa: F401
                          ORBSTACK_URL, find_container_runtime, image_status)
from ..proc import run_sandboxed
from ..types import SimError

SU2_CANDIDATES = [str(Path("~/miniconda3/envs/mises-su2/bin/SU2_CFD").expanduser()),
                  str(Path("~/miniforge3/envs/mises-su2/bin/SU2_CFD").expanduser()),
                  "/opt/homebrew/bin/SU2_CFD", "/usr/local/bin/SU2_CFD"]
OPENFOAM_PULL_LABEL = "Pull the OpenFOAM image"


def _exe(p: str | None) -> str | None:
    return p if p and os.path.isfile(p) and os.access(p, os.X_OK) else None


def find_su2() -> str | None:
    return (_exe(os.environ.get("MISES_SU2")) or _exe(shutil.which("SU2_CFD"))
            or next((c for c in SU2_CANDIDATES if _exe(c)), None))


def openfoam_rows(dock: str | None, image: str | None) -> list[dict]:
    """The container runtime row and the pinned-image row (S2). `image` is image_status()."""
    runtime = {"id": "openfoam_container", "name": "Docker / OrbStack (for OpenFOAM, optional)",
               "status": "ok" if dock else "optional_missing", "version": None, "path": dock,
               "detail": "container runtime found" if dock
               else "no Docker or OrbStack; not needed (the built-in solver is used)",
               "fix": None if dock else {"kind": "open_url", "label": "Get OrbStack",
                                         "url": ORBSTACK_URL}}
    row = {"id": "openfoam", "name": f"OpenFOAM {OPENFOAM_VERSION} image (optional CFD solver)",
           "status": "optional_missing", "version": None, "path": None, "fix": None}
    if not dock:
        row["detail"] = "needs Docker or OrbStack; the built-in solver is used"
    elif image == "present":
        row.update(status="ok", version=OPENFOAM_VERSION, path=OPENFOAM_IMAGE,
                   detail="k-omega SST (simpleFoam + snappyHexMesh), runs with no network")
    elif image == "daemon_down":
        row["detail"] = ("Docker / OrbStack is installed but not running; start it to check "
                         "for the OpenFOAM image")
    else:
        row["detail"] = "image not pulled (~180 MB, native arm64); the built-in solver is used"
        row["fix"] = {"kind": "install", "label": OPENFOAM_PULL_LABEL, "dep": "openfoam"}
    return [runtime, row]


def openfoam_available() -> tuple[str | None, str | None]:
    """(docker path, None) when an OpenFOAM run can start, else (None, why not)."""
    dock = find_container_runtime()
    if not dock:
        return None, "no Docker or OrbStack"
    st = image_status(dock)
    if st == "present":
        return dock, None
    return None, ("Docker / OrbStack is not running" if st == "daemon_down"
                  else "the OpenFOAM image is not pulled (System check)")


def cfd_solver_status() -> list[dict]:
    """Rows for the system check (C6's GET /system/check): the built-in solver is always ok;
    SU2 is detected only (no osx-arm64 conda-forge build); OpenFOAM runs in a container from
    the pinned image, which the user pulls with one click (S2). Same row shape as
    /system/check."""
    su2 = find_su2()
    dock = find_container_runtime()
    return [
        {"id": "cfd_builtin", "name": "Wind solver (built-in lattice-Boltzmann)", "status": "ok",
         "version": "D3Q19 LES", "path": None,
         "detail": "always available; low fidelity (coarse lattice, modelled skin friction)",
         "fix": None},
        {"id": "su2", "name": "SU2 (optional CFD solver)",
         "status": "ok" if su2 else "optional_missing", "version": None, "path": su2,
         "detail": ("found; AgentCAD still uses the built-in solver (SU2 runs are not wired up "
                    "yet)") if su2 else ("not installed; conda-forge has no macOS arm64 build, "
                                         "the built-in solver is used"),
         "fix": None if su2 else {"kind": "open_url", "label": "SU2 downloads",
                                  "url": "https://su2code.github.io/download.html"}},
        *openfoam_rows(dock, image_status(dock) if dock else None),
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
