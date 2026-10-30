"""CalculiX (`ccx`): detection, input deck writing and running.

Mises never installs CalculiX; it detects it and tells the user the command
(`conda install -c conda-forge calculix`). The app's PATH often lacks conda, so the usual
install locations are checked explicitly.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .proc import run_sandboxed
from .types import SimError

INSTALL_HINT = "conda install -c conda-forge calculix"
GMSH_HINT = "gmsh is a core dependency: run `uv sync` in core/"
CANDIDATES = [str(Path("~/miniconda3/bin/ccx").expanduser()),
              str(Path("~/miniforge3/bin/ccx").expanduser()),
              "/opt/homebrew/bin/ccx"]


def _exe(p: str | None) -> str | None:
    return p if p and os.path.isfile(p) and os.access(p, os.X_OK) else None


def find_ccx() -> str | None:
    """MISES_CCX, then PATH, then the conda/Homebrew locations."""
    return (_exe(os.environ.get("MISES_CCX")) or _exe(shutil.which("ccx"))
            or next((c for c in CANDIDATES if _exe(c)), None))


def ccx_version(path: str) -> str | None:
    try:
        with tempfile.TemporaryDirectory(prefix="mises-ccx-") as cwd:  # ccx may drop files
            r = subprocess.run([path, "-v"], capture_output=True, text=True, timeout=5,
                               stdin=subprocess.DEVNULL, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Version\s+([0-9][0-9.]*)", r.stdout + r.stderr)
    return m.group(1) if m else None


def gmsh_version() -> str | None:
    # find_spec does not import (and so never loads the GPL library into this process)
    if importlib.util.find_spec("gmsh") is None:
        return None
    try:
        return importlib.metadata.version("gmsh")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def solver_status() -> dict:
    path = find_ccx()
    gv = gmsh_version()
    return {"ccx": {"name": "CalculiX", "found": path is not None, "path": path,
                    "version": ccx_version(path) if path else None, "install": INSTALL_HINT},
            "gmsh": {"name": "gmsh", "found": gv is not None, "version": gv,
                     "install": GMSH_HINT}}


# Input decks for every study type are written by deck.py.


async def run_ccx(ccx: str, job_dir: Path, *, job: str = "job", timeout_s: float,
                  threads: int | None = None, allow_partial: bool = False) -> Path:
    """Run `ccx -i job` in job_dir; returns the .frd path. allow_partial (nonlinear steps):
    a run that stopped early is not an error when it wrote results; its *ERROR lines go to
    job_dir/ccx_errors.txt for the caller."""
    threads = str(threads or max(1, min(4, os.cpu_count() or 1)))
    rc, out, err = await run_sandboxed(
        [ccx, "-i", job], job_dir, timeout_s=timeout_s, what="CalculiX",
        env={"OMP_NUM_THREADS": threads, "CCX_NPROC_EQUATION_SOLVER": threads})
    frd = Path(job_dir) / f"{job}.frd"
    errors = [ln for ln in (out + "\n" + err).splitlines() if "*ERROR" in ln]
    if allow_partial and frd.exists():
        # a nonlinear step that stopped early (collapse, no convergence, too many increments)
        # still has its converged increments in the .frd / .sta: the caller reads how far it got
        (Path(job_dir) / "ccx_errors.txt").write_text("\n".join(errors[-20:]))
        return frd
    if rc != 0 or errors or not frd.exists():
        detail = "; ".join(errors[-5:]) or (err.strip() or out.strip())[-800:] or f"exit {rc}"
        raise SimError("solve_failed", f"CalculiX failed: {detail}",
                       hint="check that the part is one solid and is held by the fixed face")
    return frd
