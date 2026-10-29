"""Spawns the build worker under sandbox-exec with a timeout and process-group kill."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..platform import kill_process_tree, sandboxed_command
from . import safe_io

WORKER = Path(__file__).resolve().parent / "worker.py"
_TAIL = 4000


@dataclass
class BuildResult:
    ok: bool
    metrics: dict = field(default_factory=dict)
    vars: dict = field(default_factory=dict)
    error: str | None = None
    stdout_tail: str = ""
    # op label -> the signatures its targets resolved to in THIS build (see cad/worker.py);
    # untrusted (the worker ran the part script), validated by edits.refresh_op_refs
    op_refs: dict = field(default_factory=dict)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def reap(proc: asyncio.subprocess.Process) -> list[int]:
    """After a sandboxed run: kill its process group, then (backstop to the sandbox's
    `(deny process-fork)`) anything still in its session or descended from it."""
    _kill_group(proc)
    return await asyncio.to_thread(kill_process_tree, proc.pid)


async def run_build(part_dir: Path, vars: dict[str, float], *, timeout_s: float = 60,
                    density_g_cm3: float = 1.24, vars_path: Path | None = None) -> BuildResult:
    """Build part_dir/part.py with `vars`. `vars` is written to a temp file (or `vars_path`)
    inside part_dir/tmp; the caller owns promoting it to vars.json."""
    part_dir = safe_io.real_dir(part_dir)
    os.close(safe_io.open_dir(part_dir))     # the part dir must be a plain dir, not a link
    tmp = part_dir / "tmp"
    if tmp.is_symlink() or (tmp.exists() and not tmp.is_dir()):
        tmp.unlink()                          # a link left by an earlier build: never followed
    tmp.mkdir(parents=True, exist_ok=True)
    vp = vars_path or (tmp / "build_vars.json")
    safe_io.write_text(vp.parent, vp.name, json.dumps(vars))
    result_path = part_dir / "result.json"
    safe_io.unlink(part_dir, "result.json")   # no stale marker may survive into this build
    nonce = secrets.token_hex(16)
    env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "MISES_DENSITY": repr(density_g_cm3), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command([sys.executable, "-I", str(WORKER), str(part_dir), str(vp)], part_dir),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        await reap(proc)
        await proc.wait()
        return BuildResult(False, error=f"timeout after {timeout_s:g}s")
    except BaseException:  # cancellation etc.
        _kill_group(proc)
        await asyncio.shield(asyncio.to_thread(kill_process_tree, proc.pid))
        await proc.wait()
        raise
    stragglers = await reap(proc)
    tail = out.decode(errors="replace")[-_TAIL:]
    if stragglers:
        return BuildResult(False, error=f"the build left {len(stragglers)} process(es) running; "
                           "they were killed and the build refused", stdout_tail=tail)
    if proc.returncode != 0:
        msg = err.decode(errors="replace")[-_TAIL:].strip() or f"worker exited {proc.returncode}"
        return BuildResult(False, error=msg, stdout_tail=tail)
    try:
        result = json.loads(safe_io.read_bytes(part_dir, result_path.name) or b"null")
        if not isinstance(result, dict) or result.get("nonce") != nonce:
            raise ValueError("result marker missing or nonce mismatch")
    except Exception as e:  # noqa: BLE001
        return BuildResult(False, error=f"build did not complete (no valid result): {e}",
                           stdout_tail=tail)
    refs = result.get("op_refs")
    return BuildResult(True, metrics=result["metrics"], vars=result.get("vars", {}),
                       stdout_tail=tail, op_refs=refs if isinstance(refs, dict) else {})
