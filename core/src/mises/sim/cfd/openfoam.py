"""OpenFOAM wind studies (S2): generate the case (foam_case), run it in the pinned container
(foam_image) and read forces / forceCoeffs / sampled surfaces back (foam_parse) into the same
result dict, GLB and slice PNGs as the built-in solver.

Container isolation (hard rules):
- `--network none`: no network at all;
- the ONLY host mount is this angle's case dir inside the job dir, read-write
  (`-v case:/case:rw`; a symlinked path is refused); the root filesystem is read-only
  (`--read-only`), with an in-memory /tmp (`--tmpfs`, TMPDIR=/tmp) for OpenMPI;
- CPU (`--cpus`), memory (`--memory` = `--memory-swap`, so no swap) and process (`--pids-limit`)
  limits; all capabilities dropped, no privilege escalation, the host user's uid:gid;
- `--pull never`: a run never fetches anything (the image is pulled only by the user's click);
- a timeout, and cancel: both `docker kill` then `docker rm -f` the named container (the CLI
  process is killed too); a daemon that does not answer is retried and reported, and leftover
  labelled containers are removed before the next run;
- `cpus` is clamped to the CPUs the Docker VM reports;
- every file read back from the case is opened no-follow, must be regular and is size-capped.
The docker CLI itself is not wrapped in sandbox-exec: it must reach the daemon's socket, and the
container is the sandbox.

Tests never start a container: `make_runner` is replaced with a fake that replays recorded
OpenFOAM output files (tests/fixtures/openfoam).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from ..foam_image import OPENFOAM_IMAGE, OPENFOAM_VERSION, docker_env
from ..types import SimError
from . import foam_parse
from .foam_case import plan_case, write_case
from .setup import frontal_area_mm2, wind_frame, yawed
from .validation import openfoam_error_band, openfoam_validation_rows

LABEL = "OpenFOAM k-ω SST"
PIDS_LIMIT = 1024
SHM_MB = 512
KILL_TIMEOUT_S = 20.0
TAIL = 4000
AVERAGE_TAIL = 0.1          # coefficients: the mean over the last 10% of the iterations run


LABEL_FILTER = "label=mises.study=wind"
TMP_MB = 256
TMPFS = f"/tmp:rw,nosuid,nodev,size={TMP_MB}m"   # in-memory: not a host mount

log = logging.getLogger("mises.sim.openfoam")


class ContainerRunner(Protocol):
    async def run(self, argv: list[str], *, timeout_s: float) -> tuple[int, str]: ...
    async def kill(self, name: str) -> str | None: ...       # None, or what went wrong
    async def cleanup(self) -> None: ...
    async def ncpu(self) -> int | None: ...


class DockerRunner:
    """Runs the docker CLI (not sandboxed: it talks to the daemon) and kills by name."""

    def __init__(self, docker: str):
        self.docker = docker

    async def _cli(self, *args: str, timeout_s: float | None = None) -> tuple[int, str]:
        """(exit code, stdout) of a short docker CLI call; (-1, why) on a timeout / OS error."""
        timeout_s = KILL_TIMEOUT_S if timeout_s is None else timeout_s
        try:
            p = await asyncio.create_subprocess_exec(
                self.docker, *args, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=docker_env(self.docker), start_new_session=True)
        except OSError as e:
            return -1, str(e)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout_s)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            return -1, f"`docker {args[0]}` did not answer within {timeout_s:g}s"
        return p.returncode or 0, out.decode(errors="replace")

    async def run(self, argv: list[str], *, timeout_s: float) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=docker_env(self.docker),
            start_new_session=True)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        return proc.returncode or 0, out.decode(errors="replace")[-TAIL:]

    async def kill(self, name: str) -> str | None:
        """`docker kill`, then `docker rm -f` (a container cancelled between create and start
        is never --rm'd); a slow daemon is retried once and reported, never ignored."""
        rc, msg = await self._cli("kill", name)
        if rc == -1:
            log.warning("docker kill %s: %s; retrying with rm -f", name, msg)
        for attempt in (1, 2):
            rc2, msg2 = await self._cli("rm", "-f", name)
            if rc2 != -1:
                return None
            log.warning("docker rm -f %s (attempt %d): %s", name, attempt, msg2)
        return (f"the OpenFOAM container {name} could not be stopped ({msg2}); stop it with "
                f"`docker rm -f {name}`")

    async def cleanup(self) -> None:
        """Remove Mises wind containers left behind (a crashed or quit core, a failed rm).
        Wind studies run one at a time (studies.semaphore), so none of them is live."""
        rc, out = await self._cli("ps", "-aq", "--filter", LABEL_FILTER)
        ids = out.split() if rc == 0 else []
        if ids:
            log.warning("removing %d leftover OpenFOAM container(s)", len(ids))
            await self._cli("rm", "-f", *ids)

    async def ncpu(self) -> int | None:
        rc, out = await self._cli("info", "--format", "{{.NCPU}}")
        try:
            return int(out.strip()) if rc == 0 else None
        except ValueError:
            return None


def make_runner(docker: str) -> ContainerRunner:
    return DockerRunner(docker)


def mount_path(case: Path, job_root: Path | None = None) -> Path:
    """The case dir to mount, refused (like safe_io) when it, job_root or any dir between them
    is a symlink, when it escapes job_root, or when its text would split the -v spec. (Dirs
    above job_root, e.g. macOS's /var -> /private/var, are the Mises home's own.)"""
    case = Path(case).absolute()
    root = Path(job_root).absolute() if job_root is not None else case
    if not (case == root or case.is_relative_to(root)):
        raise SimError("bad_setup", "the OpenFOAM case is outside the study folder")
    p = case
    while True:
        if p.is_symlink() or not p.is_dir():
            raise SimError("bad_setup", f"refusing to mount {case}: {p} is a symlink or not a "
                           "directory", hint="the study folder must be a plain directory")
        if p == root:
            break
        p = p.parent
    real = case.resolve()
    if ":" in str(real) or "," in str(real):
        raise SimError("bad_setup", "the study folder path contains ':' or ','",
                       hint="move the Mises home to a plain path")
    return real


def docker_argv(docker: str, case: Path, name: str, *, cpus: int, memory_mb: int,
                job_root: Path | None = None) -> list[str]:
    """The one `docker run` Mises issues. Nothing from the part or the user's text reaches it
    except numbers (cpus, memory) and the case path Mises created. The root filesystem is
    read-only; the only writable places are the case mount and an in-memory /tmp (not a host
    mount)."""
    case = mount_path(case, job_root)
    return [docker, "run", "--rm", "--name", name, "--pull", "never",
            "--network", "none",
            "-v", f"{case}:/case:rw", "-w", "/case",
            "--read-only", "--tmpfs", TMPFS,
            f"--cpus={cpus}", f"--memory={memory_mb}m", f"--memory-swap={memory_mb}m",
            f"--pids-limit={PIDS_LIMIT}", f"--shm-size={SHM_MB}m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/case", "-e", "TMPDIR=/tmp",
            "-e", "OMPI_MCA_btl_vader_single_copy_mechanism=none",
            "--label", "mises.study=wind",
            OPENFOAM_IMAGE, "/bin/bash", "/case/Allrun"]


def _coeff_mean(tab: dict, key: str, n_tail: int) -> float:
    v = tab.get(key)
    if v is None or not len(v):
        return 0.0
    return float(np.mean(v[-n_tail:]))


def read_results(case: Path) -> dict:
    """Cd / Cl / Cs and the moments (wind frame) from postProcessing, averaged over the last
    AVERAGE_TAIL of the iterations, with the history and the log state."""
    try:
        coef = foam_parse.function_object_table(case, "forceCoeffs", "coefficient.dat")
        forces = foam_parse.function_object_table(case, "forces", "force.dat")
        moments = foam_parse.function_object_table(case, "forces", "moment.dat")
    except (OSError, ValueError) as e:     # a symlink, a non-regular or an oversized file
        raise SimError("solve_failed", f"unreadable OpenFOAM output: {e}") from None
    if coef is None or not len(coef.get("Time", [])):
        raise SimError("solve_failed", "OpenFOAM wrote no force coefficients",
                       hint="see log.simpleFoam in the study folder")
    n = len(coef["Time"])
    tail = max(1, int(math.ceil(AVERAGE_TAIL * n)))
    out = {k: _coeff_mean(coef, k, tail) for k in ("Cd", "Cl", "Cs", "CmRoll", "CmPitch",
                                                    "CmYaw")}
    cd_tail = coef["Cd"][-tail:]
    out["convergence_band"] = (float(np.std(cd_tail) / abs(out["Cd"])) if out["Cd"] else None)
    step = max(1, n // 400)
    out["history"] = [{"step": int(coef["Time"][i]), "cd": float(coef["Cd"][i]),
                       "cl": float(coef.get("Cl", np.zeros(n))[i])}
                      for i in range(0, n, step)]
    out["iterations"] = int(coef["Time"][-1])

    def vec(tab, root):
        if tab is None or f"{root}_x" not in tab:
            return None
        return [float(np.mean(tab[f"{root}_{c}"][-tail:])) for c in "xyz"]
    out["force_n"] = vec(forces, "total")
    out["force_pressure_n"] = vec(forces, "pressure")
    out["force_viscous_n"] = vec(forces, "viscous")
    out["moment_nm"] = vec(moments, "total")
    try:
        st = foam_parse.read_log_file(case / "log.simpleFoam")
    except (OSError, ValueError):
        st = {}
    out["converged"] = bool(st.get("converged"))
    out["stopped"] = "converged" if out["converged"] else "iterations"
    return out


class LogTail:
    """The last 'Time = N' of a growing log, reading only the bytes added since the last poll
    (no-follow, at most LOG_TAIL per poll)."""

    def __init__(self, path: Path):
        self.path, self.offset, self.iteration, self._rest = path, 0, None, ""

    def poll(self) -> float | None:
        try:
            text, size = foam_parse.read_capped(self.path, foam_parse.LOG_TAIL, tail=True,
                                                offset=self.offset)
        except (OSError, ValueError):
            return self.iteration
        if size < self.offset:                 # truncated / replaced: start over
            self.offset, self._rest = 0, ""
            return self.poll()
        self.offset = size
        chunk = self._rest + text
        cut = chunk.rfind("\n")
        self._rest = chunk[cut + 1:][-200:] if cut >= 0 else chunk[-200:]
        it = foam_parse.read_log(chunk[:cut + 1] if cut >= 0 else "")["iteration"]
        if it is not None:
            self.iteration = it
        return self.iteration


async def _watch(case: Path, plan_iters: int, progress) -> None:
    last = None
    tail = LogTail(case / "log.simpleFoam")
    while True:
        await asyncio.sleep(1.0)
        if progress is None:
            continue
        try:
            stage = foam_parse.read_capped(case / "stage", 256, tail=True)[0].strip()
        except (OSError, ValueError):
            continue
        it = await asyncio.to_thread(tail.poll) if stage == "simpleFoam" else None
        d = (stage, it)
        if d != last:
            last = d
            progress({"stage": stage, "iteration": it, "iterations": plan_iters})


async def _stop(runner: ContainerRunner, name: str) -> str | None:
    problem = await runner.kill(name)
    if problem:
        log.error(problem)
    return problem


async def run_case(case: Path, runner: ContainerRunner, docker: str, *, cpus: int,
                   iterations: int, memory_mb: int, timeout_s: float, progress=None,
                   job_root: Path | None = None) -> None:
    name = f"mises-of-{uuid.uuid4().hex[:12]}"
    argv = docker_argv(docker, case, name, cpus=cpus, memory_mb=memory_mb, job_root=job_root)
    watch = asyncio.create_task(_watch(case, iterations, progress))
    try:
        rc, out = await runner.run(argv, timeout_s=timeout_s)
    except asyncio.TimeoutError:
        problem = await _stop(runner, name)
        raise SimError("timeout", f"OpenFOAM timed out after {timeout_s:g}s"
                       + (f"; {problem}" if problem else ""),
                       hint="raise timeout_s, lower refinement or openfoam_iterations") \
            from None
    except BaseException:                 # cancelled: never leave a container running
        await _stop(runner, name)
        raise
    finally:
        watch.cancel()
    if rc == 137:
        raise SimError("memory", f"OpenFOAM went over its {memory_mb} MB memory limit and "
                       "was stopped", hint="lower refinement or raise memory_mb")
    if rc != 0:
        m = re.search(r"MISES_STAGE_FAILED (\S+)", out)
        stage = m.group(1) if m else None
        fatal = None
        if stage and re.fullmatch(r"[A-Za-z]+", stage):
            try:
                fatal = foam_parse.read_log_file(case / f"log.{stage}")["error"]
            except (OSError, ValueError):
                fatal = None
        msg = fatal or out.strip()[-600:] or f"exit {rc}"
        raise SimError("solve_failed", f"OpenFOAM {stage or 'run'} failed: {msg}",
                       hint=_hint(stage, out))


def _hint(stage: str | None, out: str) -> str:
    if "No such image" in out or "Unable to find image" in out or "pull access" in out:
        return "pull the OpenFOAM image from the System check"
    if stage == "snappyHexMesh":
        return "the part's STL may not be closed; or lower the refinement"
    if stage == "simpleFoam":
        return "the run diverged; lower the refinement or use solver 'auto'"
    return "see the log files in the study folder"


async def run_openfoam_study(tris_part_mm: np.ndarray, s: dict, job_dir: Path, docker: str, *,
                             runner: ContainerRunner | None = None, progress=None) -> dict:
    """All angles (yaw 0 first, the main result with outputs). One container run per angle,
    each in its own case dir under job_dir; the time budget is shared."""
    job_dir = Path(job_dir)
    runner = runner or make_runner(docker)
    t0 = time.monotonic()
    await runner.cleanup()             # leftovers of a crashed / quit core (studies run 1 at a time)
    ncpu = await runner.ncpu()
    s = {**s, "cpus": max(1, min(int(s.get("cpus", 1)), ncpu or os.cpu_count() or 1))}
    d0, up = np.array(s["direction"], float), np.array(s["up"], float)
    angles = [0.0] + [a for a in s.get("yaw_deg", []) if abs(a) > 1e-9]
    a_ref = s.get("ref_area_m2")
    main = None
    sweep = []
    for k, ang in enumerate(angles):
        left = s["timeout_s"] - (time.monotonic() - t0)
        if k and left <= 5:            # the main angle always runs (it owns the timeout)
            sweep.append({"yaw_deg": ang, "skipped": "time budget used up"})
            continue

        def prog(d, _k=k):
            if progress is not None:
                progress({**d, "angle": _k, "angles": len(angles)})
        r = await _solve_angle(tris_part_mm, s, job_dir / f"openfoam_{k}",
                               yawed(d0, up, ang), up, a_ref, docker, runner,
                               left if k == 0 else min(left, s["timeout_s"] / len(angles)),
                               prog)
        if main is None:
            main = r
            a_ref = r["ref_area_m2"]
        sweep.append({"yaw_deg": ang, "cd": r["cd"], "cl": r["cl"], "cs": r["cs"],
                      "drag_n": r["drag_n"], "lift_n": r["lift_n"], "side_n": r["side_n"],
                      "method": "openfoam"})
    main["yaw_sweep"] = sweep if len(angles) > 1 else []
    case0 = Path(main.pop("_case"))
    R = np.array(main["frame"])
    write_outputs(job_dir, case0, tris_part_mm, R, s, main)
    main["elapsed_s"] = time.monotonic() - t0
    return main


async def _solve_angle(tris_part_mm, s, case: Path, direction, up, a_ref_m2, docker, runner,
                       timeout_s, progress) -> dict:
    R = wind_frame(direction, up)
    tris_w = tris_part_mm @ R.T
    U, rho, mu = s["speed_m_s"], s["air_density"], s["air_viscosity"]
    q = 0.5 * rho * U * U
    frontal = frontal_area_mm2(tris_w) * 1e-6
    a_ref = a_ref_m2 or frontal
    tris_m = tris_w * 1e-3
    plan = plan_case(tris_m, s)
    if plan.memory_mb_estimate > s["memory_mb"]:
        raise SimError("memory", f"the OpenFOAM mesh (~{plan.cells_estimate / 1e6:.2g} M cells) "
                       f"needs ~{plan.memory_mb_estimate:.0f} MB, over the {s['memory_mb']} MB "
                       "budget", hint="lower the refinement or raise memory_mb")
    write_case(case, tris_m, s, a_ref, plan)
    re_ = rho * U * plan.d_ref_m / mu           # on lRef = D, like Cm and the benchmarks
    await run_case(case, runner, docker, cpus=int(s.get("cpus", 1)),
                   iterations=plan.end_iterations, memory_mb=int(s["memory_mb"]),
                   timeout_s=timeout_s, progress=progress, job_root=case.parent)
    r = read_results(case)
    m_w = r["moment_nm"]
    if m_w is None:      # from the coefficients: Cm q A lRef (roll x, pitch y, yaw z)
        m_w = [c * q * a_ref * plan.d_ref_m for c in (r["CmRoll"], r["CmPitch"], r["CmYaw"])]
    band = openfoam_error_band()
    return {"frontal_area_m2": frontal, "ref_area_m2": a_ref, "reynolds": re_,
            "reynolds_length_m": plan.d_ref_m, "l_ref_m": plan.d_ref_m,
            "dynamic_pressure_pa": q, "frame": R.tolist(), "method": "openfoam",
            "fidelity": LABEL, "cd": r["Cd"], "cl": r["Cl"], "cs": r["Cs"],
            "drag_n": r["Cd"] * q * a_ref, "lift_n": r["Cl"] * q * a_ref,
            "side_n": r["Cs"] * q * a_ref, "moment_nm": (R.T @ np.array(m_w)).tolist(),
            "force_n_wind": r["force_n"], "force_pressure_n_wind": r["force_pressure_n"],
            "force_viscous_n_wind": r["force_viscous_n"],
            "convergence_band": r["convergence_band"], "model_error_band": band,
            "validation": openfoam_validation_rows(), "converged": r["converged"],
            "stopped": r["stopped"], "steps": r["iterations"], "history": r["history"],
            "mesh": {**plan.to_dict(), "solver": f"OpenFOAM {OPENFOAM_VERSION}",
                     "image": OPENFOAM_IMAGE, "turbulence": "k-omega SST (RANS, wall "
                     "functions)", "boundaries": "fixed-velocity inlet, fixed-pressure outlet, "
                     "symmetry sides"},
            "_case": str(case)}


# ---- outputs: surface Cp on the part (GLB) and velocity slices (PNG)

def _nearest(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Index into src of the nearest point to each dst point: scipy's KD-tree when it is
    importable (it comes in with the CAD stack), else brute force in ~64 MB chunks."""
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        cKDTree = None
    if cKDTree is not None:
        return cKDTree(src).query(dst)[1].astype(int)
    out = np.empty(len(dst), dtype=int)
    chunk = max(1, int(4e6 // max(len(src), 1)))
    for i in range(0, len(dst), chunk):
        d2 = ((dst[i:i + chunk, None, :] - src[None, :, :]) ** 2).sum(-1)
        out[i:i + chunk] = d2.argmin(1)
    return out


def _surface(path: Path, field: str) -> tuple[np.ndarray, np.ndarray]:
    """surface_values, or empty when the file is a symlink, not regular or over the cap (the
    output is then left out with no Cp / slice; the forces still stand)."""
    try:
        return foam_parse.surface_values(path, field)
    except (OSError, ValueError) as e:
        log.warning("skipping OpenFOAM surface %s: %s", path.name, e)
        return np.zeros((0, 3)), np.zeros(0)


def write_outputs(out_dir: Path, case: Path, tris_part_mm: np.ndarray, R: np.ndarray, s: dict,
                  main: dict) -> None:
    from ..results import write_glb
    from .results import slice_png
    from .wind import _dedupe

    verts, idx = _dedupe(tris_part_mm)
    q_kin = 0.5 * s["speed_m_s"] ** 2           # simpleFoam p is kinematic (p / rho)
    cpv = np.zeros(len(verts))
    surf = foam_parse.find_surface(case, "surfaces", "body", "p")
    outputs: dict = {"glb": "result.glb"}
    if surf is not None:
        xyz, p = _surface(surf, "p")
        if len(xyz) and np.ndim(p) == 1:
            vw = verts @ R.T * 1e-3
            cpv = p[_nearest(xyz, vw)] / q_kin
    lo, hi = (float(np.percentile(cpv, 2)), float(np.percentile(cpv, 98))) if len(cpv) else (0, 1)
    lo, hi = min(lo, -0.5), max(hi, 1.0)
    write_glb(out_dir / "result.glb", verts.ravel().tolist(), idx.tolist(), color=cpv.tolist(),
              vmin=lo, vmax=hi, field="cp", units="",
              extras={"wind": {"speed_m_s": s["speed_m_s"], "direction": list(s["direction"]),
                               "solver": "openfoam"}},
              channels=[{"name": "cp", "attribute": "_CP", "units": "", "kind": "scalar",
                         "min": lo, "max": hi, "values": cpv.tolist()}])
    main["cp_range"] = [float(cpv.min()), float(cpv.max())] if len(cpv) else None
    slices = []
    tris_m = tris_part_mm @ R.T * 1e-3
    for name, axes in (("slice_xz", (0, 2)), ("slice_xy", (0, 1))):
        f = foam_parse.find_surface(case, "surfaces", name, "U")
        if f is None:
            continue
        xyz, U = _surface(f, "U")
        if not len(xyz) or np.ndim(U) != 2:
            continue
        img = slice_image(xyz, np.linalg.norm(U, axis=1), tris_m, axes)
        if img is None:
            continue
        speed2d, solid2d = img
        slice_png(out_dir / f"{name}.png", speed2d, solid2d, 1.5 * s["speed_m_s"])
        slices.append(f"{name}.png")
    if slices:
        outputs["slices"] = slices
    main["outputs"] = outputs


def slice_image(xyz: np.ndarray, speed: np.ndarray, tris_m: np.ndarray, axes: tuple[int, int],
                px_across: int = 48) -> tuple[np.ndarray, np.ndarray] | None:
    """Resample a cutting-plane's scattered speeds onto a pixel grid around the body (x from
    1 D upstream to 4 D downstream, 1.5 D either side), nearest sample per pixel, with the body
    masked from the part's own triangles (voxelized on the plane)."""
    from .setup import Grid, voxelize

    lo, hi = tris_m.reshape(-1, 3).min(0), tris_m.reshape(-1, 3).max(0)
    d = max(hi[1] - lo[1], hi[2] - lo[2], 1e-9)
    h = d / px_across
    a, b = axes
    other = 3 - a - b
    c = 0.5 * (lo + hi)
    u0, u1 = lo[a] - d, hi[a] + 4 * d
    v0, v1 = c[b] - 1.5 * d, c[b] + 1.5 * d
    nu_, nv_ = int(math.ceil((u1 - u0) / h)), int(math.ceil((v1 - v0) / h))
    U, V = np.meshgrid(u0 + (np.arange(nu_) + 0.5) * h, v0 + (np.arange(nv_) + 0.5) * h,
                       indexing="ij")
    keep = ((xyz[:, a] > u0 - 2 * h) & (xyz[:, a] < u1 + 2 * h) & (xyz[:, b] > v0 - 2 * h)
            & (xyz[:, b] < v1 + 2 * h))
    if keep.sum() < 4:
        return None
    pts = np.stack([xyz[keep, a], xyz[keep, b]], 1)
    near = _nearest(pts, np.stack([U.ravel(), V.ravel()], 1))
    speed2d = speed[keep][near].reshape(nu_, nv_)
    # the body on this plane: voxelize a one-cell-thick grid through the plane
    origin = np.zeros(3)
    origin[a], origin[b], origin[other] = u0 + h / 2, v0 + h / 2, c[other]
    shape = [1, 1, 1]
    shape[a], shape[b] = nu_, nv_
    g = Grid(tuple(shape), h, origin, px_across, d, lo, hi)
    solid = voxelize(tris_m, g)
    solid2d = solid.reshape(nu_, nv_) if a < b else solid.reshape(nv_, nu_).T
    return speed2d, solid2d


def progress_adapter(progress: Callable | None):
    """sim_progress events from the container's stage file and the simpleFoam log."""
    def prog(d: dict) -> None:
        if progress is None:
            return
        n = d.get("angles", 1)
        ang = f" · angle {d.get('angle', 0) + 1}/{n}" if n > 1 else ""
        stage = d.get("stage")
        if stage == "simpleFoam":
            it, cap = d.get("iteration"), d.get("iterations")
            frac = (it or 0) / cap if cap else 0.0
            progress("solve", phase="solve", pct=min(95.0, 25 + 70 * frac),
                     detail=f"simpleFoam iteration {int(it or 0)}{ang}")
        elif stage in ("blockMesh", "snappyHexMesh", "decomposePar"):
            progress("mesh", phase="mesh", pct=10.0 if stage == "blockMesh" else 15.0,
                     detail=f"OpenFOAM {stage}{ang}")
    return prog
