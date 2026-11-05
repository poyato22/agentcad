"""BASELINE: the same Claude Code model, a plain build123d prompt and one sandboxed exec tool.

No Mises harness: no Mises tools, agents, rules, renders, critique, FEA or catalog. The model
gets the task text (and image), a short build123d brief, and `run_build123d` (see
baseline_exec_mcp.py). Same model alias and Think level as the Mises run, the same wall-time
cap, and up to MAX_TURNS turns. The last successful execution is the answer, exported to
<out>/result.step / result.stl (and components/ for multi-part answers).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .common import HERE, Task, write_json

MAX_TURNS = 60

BRIEF = """You are a CAD engineer working in build123d (Python, on OpenCascade).
Write build123d code for the requested object and execute it with the run_build123d tool.
The tool runs your code in a sandbox and returns either the error or a summary of `result`
(solids, volume, bounding box, validity). Iterate until the model is right.

Rules:
- Assign the final shape to `result`. For an object made of several separate parts, assign a
  dict {part_name: shape} with every part already positioned in place (world coordinates).
- Units are millimetres.
- The LAST successful run_build123d call is taken as your final answer, so finish with a run
  of the complete, final model.
- Work autonomously: do not ask questions; make reasonable assumptions and state them briefly.
"""


def _user_message(task: Task) -> dict:
    content = []
    for p in task.images:
        data = Path(p).read_bytes()
        mt = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                     "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": task.prompt})
    return {"type": "user", "message": {"role": "user", "content": content}}


def run_task(task: Task, out: Path, cap_s: float, model: str = "sonnet", effort: str = "high",
             followups: list[str] | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    jobs = out / "jobs"
    if jobs.exists():
        shutil.rmtree(jobs)
    work = Path(tempfile.mkdtemp(prefix="bench-baseline-"))
    cfg = work / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"bench": {
        "command": sys.executable,
        "args": ["-I", str(HERE / "baseline_exec_mcp.py"), "--jobs", str(jobs)]}}}))
    argv = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--tools", "", "--strict-mcp-config", "--mcp-config", str(cfg),
            "--allowedTools", "mcp__bench__run_build123d", "--setting-sources=",
            "--no-session-persistence", "--max-turns", str(MAX_TURNS),
            "--append-system-prompt", BRIEF, "--model", model, "--effort", effort]
    row: dict = {"system": "baseline", "suite": task.suite, "id": task.id, "model": model,
                 "effort": effort, "cap_s": cap_s}
    t0 = time.monotonic()
    log = open(out / "claude.jsonl", "wb")
    proc = subprocess.Popen(argv, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=open(out / "claude.stderr", "wb"), start_new_session=True,
                            env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"})
    messages = [task.prompt, *(followups or [])]
    results, timed_out, sent = [], False, 0

    def send(i: int) -> None:
        msg = _user_message(task) if i == 0 else \
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": messages[i]}]}}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    import selectors
    send(0)
    sent = 1
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    buf = b""
    while True:
        if time.monotonic() - t0 > cap_s:
            timed_out = True
            break
        if not sel.select(timeout=2.0):
            if proc.poll() is not None:
                break
            continue
        chunk = proc.stdout.read1(65536)
        if not chunk:
            break
        log.write(chunk)
        log.flush()
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "rate_limit_event":
                win = ((ev.get("rate_limit_info") or {}).get("unifiedWindows") or {})
                row["seven_day_util"] = (win.get("seven_day") or {}).get("utilization")
            if ev.get("type") == "result":
                results.append(ev)
                if sent < len(messages):
                    _snapshot(jobs, out / f"stage_{sent - 1}")
                    send(sent)
                    sent += 1
                else:
                    proc.stdin.close()
        if proc.poll() is not None and not buf:
            break
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)   # our own child's group
            proc.wait(15)
        except (ProcessLookupError, PermissionError):
            pass                                  # it exited meanwhile
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(30)
    log.close()
    row["timed_out"] = timed_out
    row["usd"] = round(sum(r.get("total_cost_usd") or 0 for r in results), 4)
    usage = [r.get("usage") or {} for r in results]
    row["tokens_in"] = sum((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                           + (u.get("cache_creation_input_tokens") or 0) for u in usage)
    row["tokens_out"] = sum(u.get("output_tokens") or 0 for u in usage)
    row["turns"] = sum(r.get("num_turns") or 0 for r in results)
    row["stop"] = "timeout" if timed_out else ",".join(r.get("subtype", "?") for r in results)
    row["last_words"] = (results[-1].get("result") or "")[-1500:] if results else ""
    row["tool_calls"] = len(list(jobs.glob("job_*"))) if jobs.exists() else 0
    row["exported"] = _snapshot(jobs, out)
    row["seconds"] = round(time.monotonic() - t0, 1)
    shutil.rmtree(work, ignore_errors=True)
    write_json(out / "run.json", row)
    return row


def _snapshot(jobs: Path, dest: Path) -> bool:
    """Copy the last successful job's result into dest (result.step/.stl, components/)."""
    last = jobs / "LAST_OK"
    if not last.is_file():
        return False
    job = jobs / last.read_text().strip()
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("result.step", "result.stl", "code.py"):
        if (job / name).is_file():
            shutil.copy2(job / name, dest / name)
    if (job / "components").is_dir():
        shutil.copytree(job / "components", dest / "components", dirs_exist_ok=True)
    return (dest / "result.step").is_file()
