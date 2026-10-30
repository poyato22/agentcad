"""Run an external (GPL) tool as a separate, sandboxed process with a timeout.

The mesher (gmsh) and the solver (CalculiX) are GPL: they only ever run as child processes,
never linked into the core. Each child can only write inside its job directory and has no
network; on timeout or cancellation its whole process group is killed.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path

from ..platform import kill_process_tree, sandboxed_command
from .types import SimError

TAIL = 4000
WATCH_INTERVAL_S = 0.25


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def group_rss_mb(pgid: int) -> float:
    """Resident memory (MB) of every process in the process group (macOS / BSD ps)."""
    try:
        r = subprocess.run(["/bin/ps", "-A", "-o", "pgid=,rss="], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    kb = 0
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid):
            try:
                kb += int(parts[1])
            except ValueError:
                pass
    return kb / 1024.0


async def _watch_memory(proc: asyncio.subprocess.Process, limit_mb: float,
                        hit: list[float]) -> None:
    """Kill the process group once its resident memory passes limit_mb (macOS ignores
    RLIMIT_AS / RLIMIT_DATA, so the parent enforces the budget)."""
    while proc.returncode is None:
        rss = await asyncio.to_thread(group_rss_mb, proc.pid)
        if rss > limit_mb:
            hit.append(rss)
            _kill_group(proc)
            return
        await asyncio.sleep(WATCH_INTERVAL_S)


async def run_sandboxed(argv: list[str], job_dir: Path, *, timeout_s: float, what: str,
                        env: dict[str, str] | None = None,
                        memory_limit_mb: float | None = None,
                        read_dirs: "tuple[Path, ...] | list[Path]" = ()) -> tuple[int, str, str]:
    """Returns (returncode, stdout tail, stderr tail). Raises SimError("timeout") on timeout,
    and SimError("memory") when memory_limit_mb is given and the process group exceeds it.
    `read_dirs` are extra dirs the process may read (e.g. the job root holding model.step when
    the mesher writes into a level subdir)."""
    job_dir = Path(job_dir).resolve()
    base = {"PATH": "/usr/bin:/bin", "HOME": str(job_dir), "TMPDIR": str(job_dir),
            "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command(argv, job_dir, read_dirs=read_dirs), cwd=str(job_dir),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={**base, **(env or {})}, start_new_session=True)
    hit: list[float] = []
    watch = (asyncio.create_task(_watch_memory(proc, memory_limit_mb, hit))
             if memory_limit_mb else None)
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
    finally:
        if watch is not None:
            watch.cancel()
    _kill_group(proc)
    # backstop to the profile's (deny process-fork): nothing from the job may outlive it
    await asyncio.to_thread(kill_process_tree, proc.pid)
    if hit:
        raise SimError("memory", f"{what} used {hit[0]:.0f} MB, over its {memory_limit_mb:.0f} "
                       "MB budget, and was stopped",
                       hint="use a coarser refinement or a smaller domain")
    return (proc.returncode, out.decode(errors="replace")[-TAIL:],
            err.decode(errors="replace")[-TAIL:])
