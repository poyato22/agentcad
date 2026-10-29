"""System check and one-click installer routes (admin token only).

`GET /system/check` reports every dependency. `POST /system/install/{dep}` installs one — but
only a dep on a fixed allowlist, with a command built entirely from that allowlist (NO arguments
from the request body), streaming its output as `system_install` events. Installing is never a
tool, so no agent can reach it; it runs only when the user presses an install button.
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Awaitable, Callable

from fastapi import APIRouter

from ..events import Event
from ..system_check import (BREW_URL, CONDA_URL, INSTALLERS, Probes, find_brew, find_conda,
                            system_check)
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

# A command runner: run argv, call emit(line) per output line, return the exit code. Injected so
# tests never spawn a real brew/conda.
CommandRunner = Callable[[list[str], Callable[[str], None]], Awaitable[int]]
_MGR_URL = {"brew": BREW_URL, "conda": CONDA_URL}
_OUTPUT_CAP = 20000  # lines; a runaway installer cannot flood events forever


def _find_mgr(name: str) -> str | None:
    # Looked up through the module each call so tests can monkeypatch find_brew/find_conda.
    return {"brew": find_brew, "conda": find_conda}[name]()


async def _subprocess_runner(argv: list[str], emit: Callable[[str], None]) -> int:
    """Run argv, streaming stdout+stderr lines to `emit`. The environment is minimal."""
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
           "HOME": os.path.expanduser("~")}
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=env)
    assert proc.stdout is not None
    n = 0
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        n += 1
        if n <= _OUTPUT_CAP:
            emit(raw.decode("utf-8", "replace").rstrip("\n"))
    return await proc.wait()


def build_system_router(runtime: Runtime, *, probes: Probes | None = None,
                        runner: CommandRunner | None = None) -> APIRouter:
    router = APIRouter()
    run_cmd = runner or _subprocess_runner
    installing: set[str] = set()
    # one install at a time: two brew (or conda) runs at once fight over the manager's own lock
    serial = asyncio.Lock()

    @router.get("/system/check")
    async def check():
        return await system_check(runtime, probes)

    @router.post("/system/install/{dep}")
    async def install(dep: str):
        if dep not in INSTALLERS:
            raise MisesError(ErrorCode.INVALID_INPUT, f"{dep!r} is not installable",
                             hint=f"installable: {', '.join(sorted(INSTALLERS))}")
        spec = INSTALLERS[dep]
        mgr_path = _find_mgr(spec.needs)
        if mgr_path is None:
            # the app's contract (A1 SystemInstallStart): {ok:false, fix} is an answer, not an
            # error, so the UI can offer the fix button
            return {"ok": False, "dep": dep, "started": False,
                    "error": f"{spec.needs} is not installed",
                    "fix": {"kind": "open_url", "label": f"Get {spec.needs}",
                            "url": _MGR_URL[spec.needs]}}
        if dep in installing:
            raise MisesError(ErrorCode.DENIED, f"{dep} is already installing")
        # The command is fixed: the manager's absolute path + the allowlisted args. Nothing from
        # the request body ever reaches argv.
        argv = [mgr_path, *spec.cmd]
        installing.add(dep)
        task = asyncio.get_running_loop().create_task(
            _do_install(runtime, dep, argv, run_cmd, serial=serial))
        task.add_done_callback(lambda _t, d=dep: installing.discard(d))
        runtime._track(task)
        return {"ok": True, "fix": None, "dep": dep, "status": "running", "command": argv}

    return router


async def _do_install(runtime: Runtime, dep: str, argv: list[str], run_cmd: CommandRunner, *,
                      serial: asyncio.Lock | None = None) -> bool:
    def emit(line: str) -> None:
        runtime.events.publish(Event(type="system_install",
                                     data={"dep": dep, "line": line, "done": False, "ok": None}))

    ok = False
    serial = serial or asyncio.Lock()
    if serial.locked():
        emit("waiting for another install to finish…")
    try:
        async with serial:
            code = await run_cmd(argv, emit)
        ok = code == 0
    except Exception as e:  # noqa: BLE001 - a crash in the installer is a failed install
        emit(f"install error: {type(e).__name__}: {e}")
    runtime.events.publish(Event(type="system_install",
                                 data={"dep": dep, "line": "", "done": True, "ok": ok}))
    return ok


__all__ = ["build_system_router", "CommandRunner"]
