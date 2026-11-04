"""Mises CAD benchmark harness (not shipped code; spends real model time).

    cd core
    export MISES_BENCH_CACHE=/some/tmp/dir          # datasets + homes, never the repo
    uv run --with pyarrow python -m evals.bench.cli tasks                 # fetch + list
    uv run --with pyarrow python -m evals.bench.cli run --suite cadtestbench --system mises \\
        --limit 5 --parallel 3 --runs RUNS
    uv run --with pyarrow python -m evals.bench.cli score --runs RUNS
    uv run --with pyarrow --with matplotlib python -m evals.bench.cli report --runs RUNS \\
        --out ../results

Suites: cadbench (60 image->part, CADBench M/F/B), cadtestbench (50 text->part with
CADTests), functional (20 Mises-Functional tasks). Systems: mises (a real Mises core over HTTP,
default model, Think=High) and baseline (same Claude Code model, plain build123d prompt and
one sandboxed exec tool). Runs are resumable: a task whose run.json exists is skipped.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
import traceback
from pathlib import Path

from .common import CACHE, Task, read_json, weekly_usage, write_json

CAPS = {"cadbench": 1200, "cadtestbench": 1200, "functional": 1800}


def load_tasks(suite: str) -> list[Task]:
    if suite == "cadbench":
        from .datasets import fetch_cadbench
        return fetch_cadbench()
    if suite == "cadtestbench":
        from .datasets import fetch_cadtestbench
        return fetch_cadtestbench()
    if suite == "functional":
        from .functional.tasks import TASKS
        return [Task("functional", t.id, t.prompt, [],
                     {"category": t.category, "followups": t.followups}) for t in TASKS]
    raise SystemExit(f"unknown suite {suite}")


def pilot_order(tasks: list[Task]) -> list[Task]:
    """Spread a --limit across families/categories (round robin), deterministically."""
    key = (lambda t: t.meta.get("family") or t.meta.get("category") or "")
    groups: dict[str, list[Task]] = {}
    for t in tasks:
        groups.setdefault(key(t), []).append(t)
    out, i = [], 0
    while any(groups.values()):
        for k in sorted(groups):
            if groups[k]:
                out.append(groups[k].pop(0))
        i += 1
    return out


def run_one(task: Task, system: str, runs: Path, cap: float) -> dict:
    d = runs / task.suite / task.id / system
    if (d / "run.json").is_file():
        return read_json(d / "run.json")
    d.mkdir(parents=True, exist_ok=True)
    u0 = weekly_usage()
    followups = task.meta.get("followups") or []
    try:
        if system == "mises":
            from .mises_runner import run_task
            row = run_task(task, d, CACHE / "homes", cap, followups=followups)
        else:
            from .baseline_runner import run_task
            row = run_task(task, d, cap, followups=followups)
    except Exception as e:  # noqa: BLE001
        row = {"system": system, "suite": task.suite, "id": task.id,
               "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-3000:]}
    row["usage_before"], row["usage_after"] = u0, weekly_usage()
    write_json(d / "run.json", row)
    return row


def cmd_run(a) -> int:
    tasks = pilot_order(load_tasks(a.suite))
    if a.ids:
        keep = set(a.ids.split(","))
        tasks = [t for t in tasks if t.id in keep]
    if a.limit:
        tasks = tasks[:a.limit]
    cap = a.cap or CAPS[a.suite]
    systems = a.system.split(",")
    jobs = [(t, s) for t in tasks for s in systems]
    print(f"{len(jobs)} jobs, cap {cap}s, parallel {a.parallel}", flush=True)
    log = a.runs / "usage.jsonl"
    a.runs.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps({"event": "start", "suite": a.suite, "systems": systems,
                            "n": len(jobs), **(weekly_usage() or {})}) + "\n")
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        futs = {ex.submit(run_one, t, s, a.runs, cap): (t, s) for t, s in jobs}
        for fu in cf.as_completed(futs):
            t, s = futs[fu]
            r = fu.result()
            print(f"[{s}] {t.suite}/{t.id}: {r.get('stop') or r.get('error')} "
                  f"{r.get('seconds')}s ${r.get('usd')} wk {((r.get('usage_after') or {}).get('seven_day'))}",
                  flush=True)
            with open(log, "a") as f:
                f.write(json.dumps({"event": "done", "suite": t.suite, "id": t.id, "system": s,
                                    "seconds": r.get("seconds"), "usd": r.get("usd"),
                                    **(r.get("usage_after") or {})}) + "\n")
    return 0


def cmd_score(a) -> int:
    from .score import score_dir
    for suite in (a.suite.split(",") if a.suite else ["cadbench", "cadtestbench", "functional"]):
        if not (a.runs / suite).is_dir():
            continue
        by_id = {t.id: t for t in load_tasks(suite)}
        dirs = sorted(p for p in (a.runs / suite).glob("*/*") if (p / "run.json").is_file())
        with cf.ThreadPoolExecutor(a.parallel) as ex:
            futs = {ex.submit(score_dir, by_id[p.parent.name], p, a.force): p for p in dirs
                    if p.parent.name in by_id}
            for fu in cf.as_completed(futs):
                p = futs[fu]
                r = fu.result()
                brief = {k: r.get(k) for k in ("valid", "iou", "f1", "chamfer", "pass_rate", "rs",
                                               "score", "strict", "error") if k in r}
                print(f"{suite}/{p.parent.name}/{p.name}: {brief}", flush=True)
    return 0


def cmd_tasks(a) -> int:
    for suite in ("cadbench", "cadtestbench", "functional"):
        ts = load_tasks(suite)
        print(suite, len(ts))
        write_json(CACHE / f"tasks_{suite}.json", [t.to_json() for t in ts])
    return 0


def cmd_report(a) -> int:
    from .report import build_report
    build_report(a.runs, a.out, label=a.label)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tasks")
    r = sub.add_parser("run")
    r.add_argument("--suite", required=True)
    r.add_argument("--system", default="mises,baseline")
    r.add_argument("--ids")
    r.add_argument("--limit", type=int)
    r.add_argument("--cap", type=float)
    r.add_argument("--parallel", type=int, default=2)
    r.add_argument("--runs", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--runs", type=Path, required=True)
    s.add_argument("--suite")
    s.add_argument("--force", action="store_true")
    s.add_argument("--parallel", type=int, default=4)
    p = sub.add_parser("report")
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--label", default="")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "score": cmd_score, "tasks": cmd_tasks, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
