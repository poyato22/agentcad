"""Run one benchmark task through a real Mises core, over its HTTP API.

Each task gets a fresh MISES_HOME (never the user's real one) and its own `mises serve`
process, so builds cannot learn from each other. The prompt (and its images) go to
POST /projects/{id}/chat exactly as the app's composer sends them, with the default model
(Mises' own pick: the signed-in Claude Code) and Think=High. We then poll the build's run
tree until every run is idle, answering what the unattended app would have to:

- approvals: a reversible tool's one-off approval is approved (the build's own reversible
  actions, as the user would); an irreversible tool is always rejected. Each decision is logged.
- ui_ask_user questions: answered "no preference, use your judgement" (logged).

When the time cap passes every busy run is cancelled. The result is exported to
<out>/result.step and result.stl: the solved assembly when the build has instances (each
part's model.step placed by its world transform), else the build's parts (one part: that
part; several: a compound of all of them).
"""
from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import CORE, Task, write_json

POLL_S = 5.0
QUESTION_ANSWER = ("No preference: use your best engineering judgement, state your "
                   "assumptions, and proceed with the build without asking again.")


class Core:
    def __init__(self, home: Path, log: Path):
        self.home, self.log = Path(home), Path(log)
        self.proc: subprocess.Popen | None = None
        self.base = self.token = None

    def __enter__(self):
        self.home.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "MISES_HOME": str(self.home)}
        self.proc = subprocess.Popen(["uv", "run", "mises", "serve", "--port", "0"], cwd=CORE,
                                     env=env, stdout=open(self.log, "ab"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        sj = self.home / "server.json"
        for _ in range(240):
            if sj.is_file():
                try:
                    info = json.loads(sj.read_text())
                    self.base, self.token = f"http://127.0.0.1:{info['port']}", info["token"]
                    self.call("GET", "/health")
                    return self
                except (ValueError, KeyError, OSError):
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"mises serve exited ({self.proc.returncode}); see {self.log}")
            time.sleep(0.5)
        raise RuntimeError("mises serve did not start")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)        # our own child: clean stop
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)  # the group we started
                self.proc.wait(10)

    def call(self, method: str, path: str, body=None, timeout: float = 120):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: HTTP {e.code} {e.read()[:500]!r}") from None


def _safety_by_tool(core: Core) -> dict[str, str]:
    try:
        return {t["name"]: t["safety"] for t in core.call("GET", "/tools")}
    except RuntimeError:
        return {}


def _handle_pending(core: Core, safety: dict, decisions: list) -> None:
    for a in core.call("GET", "/approvals"):
        irreversible = "irreversible" in str(safety.get(a["action"], "irreversible")).lower()
        approve = not irreversible
        try:
            core.call("POST", f"/approvals/{a['id']}/decide",
                      {"payload_hash": a["payload_hash"], "approve": approve})
            decisions.append({"kind": "approval", "action": a["action"], "approved": approve,
                              "safety": safety.get(a["action"]),
                              "reason": (a.get("deltas") or {}).get("approval_reason")})
        except RuntimeError as e:
            decisions.append({"kind": "approval", "action": a["action"], "error": str(e)})


def _answer_questions(core: Core, runs: list[dict], decisions: list) -> None:
    for r in runs:
        try:
            pend = core.call("GET", f"/runs/{r['id']}/pending")
        except RuntimeError:
            continue
        for q in pend.get("questions", []):
            qid = q.get("question_id") or q.get("id")
            try:
                core.call("POST", f"/questions/{qid}/answer", {"text": QUESTION_ANSWER})
                decisions.append({"kind": "question", "run": r["id"],
                                  "question": str(q.get("question") or q)[:300]})
            except RuntimeError as e:
                decisions.append({"kind": "question", "error": str(e)})


def _busy(runs: list[dict]) -> bool:
    return any(r["status"] in ("queued", "running") or
               (r.get("activity") or {}).get("state") not in (None, "idle")
               for r in runs)


def _runs(core: Core, pid: str) -> list[dict]:
    rows = core.call("GET", f"/runs?project_id={pid}&limit=500")
    out = []
    for r in rows:
        try:
            out.append(core.call("GET", f"/runs/{r['id']}"))
        except RuntimeError:
            out.append(r)
    return out


def export_build(core: Core, pid: str, out: Path) -> dict:
    """Write out/result.step + result.stl from the build's assembly or parts."""
    import build123d as b3
    from OCP.gp import gp_Trsf

    parts_dir = core.home / "parts"
    asm = core.call("GET", f"/projects/{pid}/assembly")
    parts = [p for p in core.call("GET", f"/parts?project_id={pid}")
             if (parts_dir / p["id"] / "model.step").is_file()]
    shapes, names, source = [], [], "parts"
    insts = [i for i in asm.get("instances", [])
             if (parts_dir / i["part_id"] / "model.step").is_file()]
    if insts:
        source = "assembly"
        for i in insts:
            s = b3.import_step(str(parts_dir / i["part_id"] / "model.step"))
            m = i["transform"]
            t = gp_Trsf()
            t.SetValues(m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3],
                        m[2][0], m[2][1], m[2][2], m[2][3])
            s = s.moved(b3.Location(t))
            s.label = i.get("name") or i["part_id"]
            shapes.append(s)
            names.append(s.label)
    else:
        for p in parts:
            s = b3.import_step(str(parts_dir / p["id"] / "model.step"))
            s.label = p["id"]
            shapes.append(s)
            names.append(p["id"])
    info = {"source": source, "components": names, "n_parts": len(parts)}
    if not shapes:
        return {**info, "exported": False}
    shape = shapes[0] if len(shapes) == 1 else b3.Compound(children=shapes)
    out.mkdir(parents=True, exist_ok=True)
    b3.export_step(shape, str(out / "result.step"))
    b3.export_stl(shape, str(out / "result.stl"))
    # each component on its own too (the functional checkers read named parts)
    comp = out / "components"
    comp.mkdir(exist_ok=True)
    for s, n in zip(shapes, names):
        b3.export_step(s, str(comp / f"{_safe(n)}.step"))
    return {**info, "exported": True}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "part"


def _image_block(path: str) -> dict:
    data = Path(path).read_bytes()
    mt = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return {"media_type": mt, "data_b64": base64.b64encode(data).decode()}


def run_task(task: Task, out: Path, home_root: Path, cap_s: float, effort: str = "high",
             followups: list[str] | None = None) -> dict:
    """One build per task. `followups` (edit-regression tasks) are sent in the same build
    after the first build finishes; the export before each follow-up is kept as stage_<n>."""
    out.mkdir(parents=True, exist_ok=True)
    home = home_root / f"{task.suite}-{task.id}-{int(time.time())}"
    row: dict = {"system": "mises", "suite": task.suite, "id": task.id, "home": str(home),
                 "effort": effort, "cap_s": cap_s}
    t0 = time.monotonic()
    decisions: list = []
    with Core(home, out / "core.log") as core:
        safety = _safety_by_tool(core)
        pid = core.call("POST", "/projects", {"name": f"bench {task.suite} {task.id}"})["id"]
        row["project_id"] = pid
        messages = [task.prompt, *(followups or [])]
        timed_out = False
        for n, text in enumerate(messages):
            body = {"text": text, "effort": effort}
            if n == 0 and task.images:
                body["images"] = [_image_block(p) for p in task.images]
            sub = core.call("POST", f"/projects/{pid}/chat", body)
            row.setdefault("routed", sub.get("routed"))
            row.setdefault("lead_run", sub.get("run_id"))
            idle_polls = 0
            while True:
                time.sleep(POLL_S)
                runs = _runs(core, pid)
                _handle_pending(core, safety, decisions)
                _answer_questions(core, runs, decisions)
                if time.monotonic() - t0 > cap_s:
                    timed_out = True
                    for r in runs:
                        if r["status"] in ("queued", "running"):
                            try:
                                core.call("POST", f"/runs/{r['id']}/cancel")
                            except RuntimeError:
                                pass
                    time.sleep(3)
                    break
                idle_polls = 0 if _busy(runs) else idle_polls + 1
                if idle_polls >= 2:
                    break
            if n < len(messages) - 1 and not timed_out:
                try:
                    row.setdefault("stages", []).append(export_build(core, pid, out / f"stage_{n}"))
                except Exception as e:  # noqa: BLE001
                    row.setdefault("stages", []).append({"error": f"{type(e).__name__}: {e}"})
            if timed_out:
                break
        runs = _runs(core, pid)
        row["timed_out"] = timed_out
        row["runs"] = [{"id": r["id"], "agent": r["agent"], "status": r["status"],
                        "provider": r.get("provider"), "model": r.get("model"),
                        "usd": r.get("usd"), "in": r.get("input_tokens"),
                        "out": r.get("output_tokens")} for r in runs]
        row["model"] = next((f"{r.get('provider')}/{r.get('model')}" for r in runs
                             if r["id"] == row.get("lead_run")), None)
        row["usd"] = round(sum(r.get("usd") or 0 for r in runs), 4)
        row["tokens_in"] = sum(r.get("input_tokens") or 0 for r in runs)
        row["tokens_out"] = sum(r.get("output_tokens") or 0 for r in runs)
        row["stop"] = "timeout" if timed_out else ",".join(sorted({r["status"] for r in runs}))
        try:
            row["last_words"] = _last_words(core, row.get("lead_run"))
            row["tool_calls"] = tool_calls(core, [r["id"] for r in runs])
        except Exception:  # noqa: BLE001
            pass
        try:
            row.update(export_build(core, pid, out))
        except Exception as e:  # noqa: BLE001
            row["export_error"] = f"{type(e).__name__}: {e}"
    row["decisions"] = decisions
    row["seconds"] = round(time.monotonic() - t0, 1)
    write_json(out / "run.json", row)
    return row


def _last_words(core: Core, run_id: str | None) -> str:
    if not run_id:
        return ""
    steps = core.call("GET", f"/runs/{run_id}/steps")
    for s in reversed(steps):
        p = s.get("payload") or {}
        if s.get("kind") == "message" and p.get("role") == "assistant":
            txt = p.get("text") or ""
            if isinstance(txt, str) and txt.strip():
                return txt[-1500:]
    return ""


def tool_calls(core: Core, run_ids: list[str]) -> int:
    n = 0
    for rid in run_ids:
        try:
            n += sum(1 for s in core.call("GET", f"/runs/{rid}/steps") if s.get("kind") == "tool_call")
        except RuntimeError:
            pass
    return n
