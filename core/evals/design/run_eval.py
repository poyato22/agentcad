"""Run the D28 design eval: build every prompt with one model, render it, write a contact sheet.

    cd core && uv run python evals/design/run_eval.py --model claude-code/opus --out OUT
        [--only humanoid,robot_dog] [--timeout 2400] [--home DIR]

Each prompt gets its own build in a temporary MISES_HOME (never the user's real one, unless
--home names one), goes to the build's lead exactly as the app's composer would (lead.submit:
routing, design class, the lead and its sub-agents), and waits for the whole run tree. Then
every built part of the build (its assembly when it has one) is rendered hero 3/4 shaded and
zebra (the product renderer's styles when it has them, else evals/design/preview.py), and
OUT gets:
- <id>/hero.png, <id>/zebra.png, <id>/front.png and <id>/result.json (class, routed agent,
  part count, straight-bar parts, cost, time, stop reason, the lead's last words);
- contact_sheet.png (every prompt's hero, labelled) and results.json (all rows).

Pair two runs (two models, or two judge settings) with preference_sheet.py. Not run in CI:
it spends real model time. Live model calls go through the user's own configured runtime.
Unattended: every approval request is auto-REJECTED (never approved; see reject_approvals).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_preview():
    """evals/design/preview.py, loaded by path (no sys.path change: nothing can shadow it)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mises_design_eval_preview",
                                                  HERE / "preview.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod       # a unique name (dataclasses look it up); no sys.path
    spec.loader.exec_module(mod)
    return mod


preview = _load_preview()


def load_prompts(only: str | None = None) -> list[dict]:
    rows = json.loads((HERE / "prompts.json").read_text())["prompts"]
    if only:
        keep = {s.strip() for s in only.split(",") if s.strip()}
        rows = [r for r in rows if r["id"] in keep]
    return rows


def _tree(store, root: str) -> list:
    out, todo = [], [root]
    while todo:
        rid = todo.pop()
        out.append(store.get_run(rid))
        todo += [r.id for r in store.list_runs(500) if r.parent_id == rid]
    return out


async def _wait_tree(rt, root: str, timeout: float) -> str:
    """Wait for the lead, then for any sub-agent still running; returns the lead's stop."""
    deadline = time.monotonic() + timeout
    res = await asyncio.wait_for(rt.wait(root), timeout)
    while time.monotonic() < deadline:
        busy = [r for r in _tree(rt.store, root) if r.status in ("queued", "running")]
        if not busy:
            break
        await asyncio.sleep(2.0)
    return res.stop_reason


def _built_parts(rt, project_id: str) -> list[tuple[str, Path]]:
    parts = Path(rt.home) / "parts"
    return [(p.id, parts / p.id) for p in rt.store.list_part_rows(project_id)
            if (parts / p.id / "model.stl").is_file()]


def render_build(rt, project_id: str, out: Path) -> dict:
    """Hero / zebra / front PNGs of the build's built parts; returns {parts, bars}."""
    from mises.cad.render import load_stl
    from mises.design_intensity import bar_like

    items, bars = [], []
    for pid, d in _built_parts(rt, project_id):
        items.append(preview.Item(pid, load_stl((d / "model.stl").read_bytes()), "shell"))
        try:
            if bar_like(json.loads((d / "metrics.json").read_text())):
                bars.append(pid)
        except (OSError, ValueError):
            pass
    out.mkdir(parents=True, exist_ok=True)
    if items:
        for name, view, style in (("hero", "hero", "shaded"), ("zebra", "hero", "zebra"),
                                  ("front", "front", "shaded")):
            (out / f"{name}.png").write_bytes(preview.render_preview(items, view, 1024, style))
    return {"parts": [i.name for i in items], "bars": bars}


async def reject_approvals(rt) -> None:
    """Unattended eval: every approval an agent asks for is REJECTED at once (never approved),
    so a needs_approval action neither waits out the timeout nor happens. The rejection is
    recorded as decided by "design-eval" and the agent sees a normal refusal."""
    sub = rt.events.subscribe()
    while True:
        ev = await sub.queue.get()
        if ev.type != "approval_requested":
            continue
        try:
            rt.approvals.decide(ev.data["id"], ev.data["payload_hash"], approve=False,
                                decided_by="design-eval (unattended: auto-reject)")
        except Exception as e:  # noqa: BLE001 - already decided or expired
            print(f"   approval {ev.data.get('id')}: {e}", flush=True)


async def run_one(rt, item: dict, model: str, out: Path, timeout: float) -> dict:
    from mises import projects
    from mises.orchestrator import lead

    t0 = time.monotonic()
    proj = projects.create_project(rt.store, f"eval {item['id']}")
    row = {"id": item["id"], "expect": item["expect"], "prompt": item["prompt"],
           "model": model, "project_id": proj["id"]}
    try:
        sub = await lead.submit(rt, item["prompt"], project_id=proj["id"], model_ref=model)
        row["routed"] = sub["routed"]
        row["stop_reason"] = await _wait_tree(rt, sub["run_id"], timeout)
        runs = _tree(rt.store, sub["run_id"])
        lead_run = rt.store.get_run(sub["run_id"])
        row["design_class"] = lead_run.design_class
        row["usd"] = round(sum(r.usd for r in runs), 4)
        row["runs"] = len(runs)
    except Exception as e:  # noqa: BLE001 - one prompt failing must not stop the eval
        row["error"] = f"{type(e).__name__}: {e}"
    row.update(render_build(rt, proj["id"], out / item["id"]))
    row["seconds"] = round(time.monotonic() - t0, 1)
    (out / item["id"] / "result.json").write_text(json.dumps(row, indent=1, default=str))
    return row


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="model ref, e.g. claude-code/opus")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default=None, help="comma-separated prompt ids")
    ap.add_argument("--timeout", type=float, default=2400.0, help="seconds per prompt")
    ap.add_argument("--home", type=Path, default=None,
                    help="MISES_HOME to use (default: a fresh temporary one)")
    a = ap.parse_args(argv)
    home = a.home or Path(tempfile.mkdtemp(prefix="mises-design-eval-"))
    os.environ["MISES_HOME"] = str(home)
    from mises.runtime import Runtime
    from mises.server.app import serve_async

    a.out.mkdir(parents=True, exist_ok=True)
    rt = Runtime(home=home)
    _port, _token, handle = await serve_async(rt, write_server_json=False)
    rejector = asyncio.get_running_loop().create_task(reject_approvals(rt))
    rows = []
    try:
        for item in load_prompts(a.only):
            print(f"[{item['id']}] {item['prompt'][:70]}", flush=True)
            rows.append(await run_one(rt, item, a.model, a.out, a.timeout))
            print(f"   -> {rows[-1].get('stop_reason') or rows[-1].get('error')} "
                  f"{len(rows[-1]['parts'])} parts, {rows[-1]['seconds']} s", flush=True)
    finally:
        rejector.cancel()
        await rt.close_claude_sessions()
        await handle.stop()
        await rt.aclose()
    (a.out / "results.json").write_text(json.dumps(rows, indent=1, default=str))
    sheet = [(f"{r['id']} ({(r.get('design_class') or {}).get('cls', '?')})",
              (a.out / r["id"] / "hero.png").read_bytes())
             for r in rows if (a.out / r["id"] / "hero.png").is_file()]
    if sheet:
        (a.out / "contact_sheet.png").write_bytes(preview.contact_sheet(sheet, cols=5, cell=360))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
