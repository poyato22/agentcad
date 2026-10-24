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

from ..platform import sandboxed_command

WORKER = Path(__file__).resolve().parent / "worker.py"
_TAIL = 4000


@dataclass
class BuildResult:
    ok: bool
    metrics: dict = field(default_factory=dict)
    vars: dict = field(default_factory=dict)
    error: str | None = None
    stdout_tail: str = ""


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_build(part_dir: Path, vars: dict[str, float], *, timeout_s: float = 60,
                    density_g_cm3: float = 1.24, vars_path: Path | None = None) -> BuildResult:
    """Build part_dir/part.py with `vars`. `vars` is written to a temp file (or `vars_path`)
    inside part_dir/tmp; the caller owns promoting it to vars.json."""
    part_dir = Path(part_dir).resolve()
    tmp = part_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    vp = vars_path or (tmp / "build_vars.json")
    vp.write_text(json.dumps(vars))
    result_path = part_dir / "result.json"
    result_path.unlink(missing_ok=True)  # no stale marker may survive into this build
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
        _kill_group(proc)
        await proc.wait()
        return BuildResult(False, error=f"timeout after {timeout_s:g}s")
    except BaseException:  # cancellation etc.
        _kill_group(proc)
        await proc.wait()
        raise
    _kill_group(proc)  # reap stragglers the script may have spawned
    tail = out.decode(errors="replace")[-_TAIL:]
    if proc.returncode != 0:
        msg = err.decode(errors="replace")[-_TAIL:].strip() or f"worker exited {proc.returncode}"
        return BuildResult(False, error=msg, stdout_tail=tail)
    try:
        result = json.loads(result_path.read_text())
        if not isinstance(result, dict) or result.get("nonce") != nonce:
            raise ValueError("result marker missing or nonce mismatch")
    except Exception as e:  # noqa: BLE001
        return BuildResult(False, error=f"build did not complete (no valid result): {e}",
                           stdout_tail=tail)
    return BuildResult(True, metrics=result["metrics"], vars=result.get("vars", {}),
                       stdout_tail=tail)
