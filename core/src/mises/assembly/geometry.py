"""Geometry side of assemblies: resolve face frames and run the sandboxed worker for
interference and the combined GLB. Kept out of ``solve.py`` so that stays pure math."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..cad import safe_io
from ..cad.runner import WORKER
from ..platform import kill_process_tree, sandboxed_command
from ..types import ErrorCode, MisesError
from .model import check_part_id
from .solve import SolveResult


def part_dir(parts_root: Path, part_id: str) -> Path:
    # stored instances are validated already; re-check here because this builds a path in the
    # core process (face resolution is not sandboxed)
    try:
        check_part_id(part_id)
    except ValueError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, str(e)) from None
    return Path(parts_root) / part_id


def resolve_face(parts_root: Path, part_id: str, face_id: str) -> tuple[list[float], list[float]]:
    """A face id -> (centre, outward normal) from the part's stored metrics."""
    # read without following links (C6 safe_io): the build sandbox can write the part dir
    try:
        metrics = safe_io.read_json(part_dir(parts_root, part_id), "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        metrics = None
    if not isinstance(metrics, dict):
        raise MisesError(ErrorCode.NOT_FOUND, f"part {part_id!r} is not built",
                         hint="build the part before using a face frame")
    for f in metrics.get("face_list", []):
        if f.get("id") == face_id:
            normal = f.get("normal")
            if not normal:
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"face {face_id} of {part_id} has no usable normal")
            return f["center"], normal
    raise MisesError(ErrorCode.NOT_FOUND, f"no face {face_id!r} on part {part_id!r}")


def face_resolver(parts_root: Path):
    return lambda part_id, face_id: resolve_face(parts_root, part_id, face_id)


@dataclass
class GeometryResult:
    interferences: list[dict] = field(default_factory=list)
    glb_path: Path | None = None
    step_path: Path | None = None                          # want="step": the whole assembly
    stderr: str = ""
    positions: list[dict] = field(default_factory=list)   # want="sweep": one per pose


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_geometry(parts_root: Path, assembly, result: SolveResult, *, want: str = "both",
                       threshold: float = 0.01, out_dir: Path | None = None,
                       timeout_s: float = 90, poses: list[dict] | None = None) -> GeometryResult:
    """Spawn the sandboxed worker to compute interference and/or write the assembly GLB.

    Only instances whose part is built (``model.step`` present) take part. With ``poses`` (a
    list of {instance id: world transform}) the worker sweeps them in ONE process, each STEP
    read once (want="sweep"): ``positions`` gets interference plus min clearance per pose, and
    the instances are named by id. ``out_dir`` holds the
    GLB; when omitted a temp dir is used (its GLB is read into memory by the caller if needed).
    """
    parts_root = Path(parts_root)
    owned = out_dir is None
    out = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="mises-asm-"))
    out.mkdir(parents=True, exist_ok=True)
    # each built part's model.step is copied (read via safe_io, links never followed) into the
    # job dir: the worker only ever opens host-made copies, never a path into a part dir
    inputs = out / "inputs"
    instances = []
    copied: dict[str, Path] = {}
    for inst in assembly.instances:
        step = copied.get(inst.part_id)
        if step is None:
            try:
                step = safe_io.copy_out(part_dir(parts_root, inst.part_id), "model.step", inputs,
                                        f"{inst.part_id}.step")
            except (OSError, safe_io.UnsafePath):
                step = None
            if step is not None:
                copied[inst.part_id] = step
        if step is not None:
            instances.append({"name": inst.id if poses is not None else (inst.name or inst.id),
                              "step": str(step), "id": inst.id,
                              "transform": result.transforms[inst.id]})
    if not instances:
        raise MisesError(ErrorCode.NOT_FOUND, "no built parts in this assembly",
                         hint="build the referenced parts first")

    tmp = out / "tmp"
    tmp.mkdir(exist_ok=True)
    job = out / "job.json"
    job.write_text(json.dumps({"out_dir": str(out.resolve()),
                               "want": "sweep" if poses is not None else want,
                               "threshold": threshold, "instances": instances,
                               **({"poses": poses} if poses is not None else {})}))
    (out / "result.json").unlink(missing_ok=True)
    nonce = secrets.token_hex(16)
    env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command([sys.executable, "-I", str(WORKER), "--assembly", str(job)], out),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        raise MisesError(ErrorCode.TOOL_FAILED, f"assembly geometry timed out after {timeout_s:g}s")
    except BaseException:
        _kill_group(proc)
        await proc.wait()
        raise
    _kill_group(proc)
    # backstop to the profile's (deny process-fork), as in sim/proc.py: nothing outlives the job
    await asyncio.to_thread(kill_process_tree, proc.pid)
    stderr = err.decode(errors="replace")[-4000:]
    if proc.returncode != 0:
        raise MisesError(ErrorCode.TOOL_FAILED, f"assembly geometry failed: {stderr.strip()}")
    try:
        data = safe_io.read_json(out, "result.json")    # a planted link is refused
        if data.get("nonce") != nonce:
            raise ValueError("nonce mismatch")
    except Exception as e:  # noqa: BLE001
        raise MisesError(ErrorCode.TOOL_FAILED, f"assembly geometry produced no result: {e}")
    glb = out / "assembly.glb" if data.get("glb") else None
    step = out / "assembly.step" if data.get("step") else None
    return GeometryResult(interferences=data.get("interferences", []), glb_path=glb, stderr=stderr,
                          positions=data.get("positions", []), step_path=step)
