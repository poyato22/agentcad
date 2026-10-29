"""Run an external (GPL) tool as a separate, sandboxed process with a timeout.

The mesher (gmsh) and the solver (CalculiX) are GPL: they only ever run as child processes,
never linked into the core. Each child can only write inside its job directory and has no
network; on timeout or cancellation its whole process group is killed.
"""
from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from ..platform import kill_process_tree, sandboxed_command
from .types import SimError

TAIL = 4000


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_sandboxed(argv: list[str], job_dir: Path, *, timeout_s: float, what: str,
                        env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Returns (returncode, stdout tail, stderr tail). Raises SimError("timeout") on timeout."""
    job_dir = Path(job_dir).resolve()
    base = {"PATH": "/usr/bin:/bin", "HOME": str(job_dir), "TMPDIR": str(job_dir),
            "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command(argv, job_dir), cwd=str(job_dir),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={**base, **(env or {})}, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        raise SimError("timeout", f"{what} timed out after {timeout_s:g}s",
                       hint="simplify the part or use a coarser study; large parts take longer") \
            from None
    except BaseException:  # cancelled: never leave a solver running
        _kill_group(proc)
        await proc.wait()
        raise
    _kill_group(proc)
    # backstop to the profile's (deny process-fork): nothing from the job may outlive it
    await asyncio.to_thread(kill_process_tree, proc.pid)
    return (proc.returncode, out.decode(errors="replace")[-TAIL:],
            err.decode(errors="replace")[-TAIL:])
