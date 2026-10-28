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

from .materials import Material
from .mesh import Mesh
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


# gmsh 10-node tetra -> CalculiX C3D10: the last two edge nodes are swapped
_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


def write_inp(path: Path, mesh: Mesh, fixed: set[int], loads: dict[int, tuple[float, float, float]],
              material: Material) -> None:
    """Static linear-elastic deck. Units: mm, N, MPa (so stress in MPa, displacement in mm)."""
    lines = ["*HEADING", "Mises static study (guide only)", "*NODE"]
    lines += [f"{n},{x:.9g},{y:.9g},{z:.9g}" for n, (x, y, z) in mesh.nodes.items()]
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    for i, t in enumerate(mesh.tets, 1):
        lines.append(f"{i}," + ",".join(str(t[k]) for k in _TET10))
    lines.append("*NSET, NSET=NFIX")
    fx = sorted(fixed)
    lines += [",".join(map(str, fx[i:i + 16])) for i in range(0, len(fx), 16)]
    lines += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{material.e_mpa:.9g},{material.nu:.9g}",
              "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
              "*STEP", "*STATIC", "*BOUNDARY", "NFIX,1,3", "*CLOAD"]
    for n, vec in sorted(loads.items()):
        for dof, v in enumerate(vec, 1):
            if v != 0.0:
                lines.append(f"{n},{dof},{v:.9g}")
    lines += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP", ""]
    Path(path).write_text("\n".join(lines))


async def run_ccx(ccx: str, job_dir: Path, *, job: str = "job", timeout_s: float) -> Path:
    """Run `ccx -i job` in job_dir; returns the .frd path."""
    threads = str(max(1, min(4, os.cpu_count() or 1)))
    rc, out, err = await run_sandboxed(
        [ccx, "-i", job], job_dir, timeout_s=timeout_s, what="CalculiX",
        env={"OMP_NUM_THREADS": threads, "CCX_NPROC_EQUATION_SOLVER": threads})
    frd = Path(job_dir) / f"{job}.frd"
    errors = [ln for ln in (out + "\n" + err).splitlines() if "*ERROR" in ln]
    if rc != 0 or errors or not frd.exists():
        detail = "; ".join(errors[-5:]) or (err.strip() or out.strip())[-800:] or f"exit {rc}"
        raise SimError("solve_failed", f"CalculiX failed: {detail}",
                       hint="check that the part is one solid and is held by the fixed face")
    return frd
