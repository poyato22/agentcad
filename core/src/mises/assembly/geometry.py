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

from ..cad.runner import WORKER
from ..platform import sandboxed_command
from ..types import ErrorCode, MisesError
from .solve import SolveResult


def part_dir(parts_root: Path, part_id: str) -> Path:
    return Path(parts_root) / part_id


def resolve_face(parts_root: Path, part_id: str, face_id: str) -> tuple[list[float], list[float]]:
    """A face id -> (centre, outward normal) from the part's stored metrics."""
    metrics = part_dir(parts_root, part_id) / "metrics.json"
    if not metrics.is_file():
        raise MisesError(ErrorCode.NOT_FOUND, f"part {part_id!r} is not built",
                         hint="build the part before using a face frame")
    for f in json.loads(metrics.read_text()).get("face_list", []):
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
    stderr: str = ""


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_geometry(parts_root: Path, assembly, result: SolveResult, *, want: str = "both",
                       threshold: float = 0.01, out_dir: Path | None = None,
                       timeout_s: float = 90) -> GeometryResult:
    """Spawn the sandboxed worker to compute interference and/or write the assembly GLB.

    Only instances whose part is built (``model.step`` present) take part. ``out_dir`` holds the
    GLB; when omitted a temp dir is used (its GLB is read into memory by the caller if needed).
    """
    parts_root = Path(parts_root)
    instances = []
    for inst in assembly.instances:
        step = part_dir(parts_root, inst.part_id) / "model.step"
        if step.is_file():
            instances.append({"name": inst.name or inst.id, "step": str(step.resolve()),
                              "transform": result.transforms[inst.id]})
    if not instances:
        raise MisesError(ErrorCode.NOT_FOUND, "no built parts in this assembly",
                         hint="build the referenced parts first")

    owned = out_dir is None
    out = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="mises-asm-"))
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "tmp"
    tmp.mkdir(exist_ok=True)
    job = out / "job.json"
    job.write_text(json.dumps({"out_dir": str(out.resolve()), "want": want,
                               "threshold": threshold, "instances": instances}))
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
    stderr = err.decode(errors="replace")[-4000:]
    if proc.returncode != 0:
        raise MisesError(ErrorCode.TOOL_FAILED, f"assembly geometry failed: {stderr.strip()}")
    try:
        data = json.loads((out / "result.json").read_text())
        if data.get("nonce") != nonce:
            raise ValueError("nonce mismatch")
    except Exception as e:  # noqa: BLE001
        raise MisesError(ErrorCode.TOOL_FAILED, f"assembly geometry produced no result: {e}")
    glb = out / "assembly.glb" if data.get("glb") else None
    if owned and glb is None:
        pass
    return GeometryResult(interferences=data.get("interferences", []), glb_path=glb, stderr=stderr)
