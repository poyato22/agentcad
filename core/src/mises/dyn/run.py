"""Run a dynamics job in the sandboxed child process (the sim/proc.py pattern) and keep its
result (Plan 10 C3).

A job is plain JSON (`task`, `model` from mises.dyn.export, `trajectory`, ...). It runs as
`python -I dyn/worker.py job.json` under sandbox-exec (writes only its job dir, no network,
no forks), in a heavy-worker slot, with a timeout and a memory cap. Results of the dyn_*
tools are kept per build under <home>/dyn/<project_id>/<result_id>.json for the checks
report and the app's cards.
"""
from __future__ import annotations

import json
import secrets
import shutil
import sys
import time
from pathlib import Path

from ..cad import safe_io
from ..sim.proc import run_sandboxed
from ..sim.types import SimError
from ..types import ErrorCode, MisesError

WORKER = Path(__file__).resolve().parent / "worker.py"
TIMEOUT_S = 180.0
MEMORY_MB = 3072.0
KEEP_RESULTS = 40


def dyn_root(home: Path) -> Path:
    return Path(home) / "dyn"


async def run_job(home: Path, job: dict, *, timeout_s: float = TIMEOUT_S,
                  what: str = "dynamics") -> dict:
    root = Path(home) / "dyn_tmp"
    root.mkdir(parents=True, exist_ok=True)
    job_dir = root / ("dyn_" + secrets.token_hex(6))
    job_dir.mkdir()
    nonce = secrets.token_hex(16)
    try:
        (job_dir / "job.json").write_text(json.dumps(job))
        try:
            rc, _out, err = await run_sandboxed(
                [sys.executable, "-I", str(WORKER), str(job_dir / "job.json")], job_dir,
                timeout_s=timeout_s, what=what, env={"MISES_NONCE": nonce},
                memory_limit_mb=MEMORY_MB)
        except SimError as e:
            raise MisesError(ErrorCode.TOOL_FAILED, e.message, hint=e.hint) from None
        try:
            data = safe_io.read_json(job_dir, "result.json")   # a planted link is refused
        except (OSError, ValueError, safe_io.UnsafePath):
            data = None
        if not isinstance(data, dict) or data.get("nonce") != nonce:
            raise MisesError(ErrorCode.TOOL_FAILED, f"{what} produced no result "
                             f"(exit {rc}): {err.strip()[-600:]}")
        if not data.get("ok"):
            raise MisesError(ErrorCode.TOOL_FAILED, f"{what} failed: {data.get('error')}",
                             hint="check the assembly's joints, masses and the trajectory")
        return data["result"]
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def save_result(home: Path, project_id: str, kind: str, data: dict) -> str:
    """Keep a dyn_* result for the build; returns its id. The newest KEEP_RESULTS stay."""
    d = dyn_root(home) / project_id
    d.mkdir(parents=True, exist_ok=True)
    rid = f"dyn_{secrets.token_hex(6)}"
    (d / f"{rid}.json").write_text(json.dumps({"id": rid, "kind": kind, "at": time.time(),
                                               **data}, default=float))
    olds = sorted(d.glob("dyn_*.json"), key=lambda p: p.stat().st_mtime)
    for p in olds[:-KEEP_RESULTS]:
        p.unlink(missing_ok=True)
    return rid


def load_result(home: Path, project_id: str, rid: str) -> dict | None:
    if not rid.startswith("dyn_") or not rid[4:].isalnum():
        return None
    try:
        return json.loads((dyn_root(home) / project_id / f"{rid}.json").read_text())
    except (OSError, ValueError):
        return None
