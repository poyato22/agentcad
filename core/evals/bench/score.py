"""Score one run directory (RUNS/<suite>/<task>/<system>/) for its suite; writes score.json."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from .common import HERE, Task, read_json, write_json


def score_cadbench(task: Task, d: Path) -> dict:
    from . import geometry as g
    step = d / "result.step"
    if not step.is_file():
        return {"valid": False, "iou": 0.0, "f1": 0.0, "chamfer": None, "error": "no result"}
    v, tris = g.validity(step)
    out = {"validity": v.as_dict(), "valid": v.valid}
    if len(tris) == 0:
        return {**out, "iou": 0.0, "f1": 0.0, "chamfer": None}
    gt = g.load_stl_bytes(Path(task.meta["gt_stl"]).read_bytes())
    m = g.compare(tris, gt)
    # an invalid shape scores 0 IoU / F1 (CADBench counts invalid outputs as failures);
    # its raw numbers are kept for diagnosis
    if not v.valid:
        out["raw"] = m
        return {**out, "iou": 0.0, "f1": 0.0, "chamfer": None}
    return {**out, **m}


_CQ_LOCK = threading.Lock()   # parallel CadQuery env launches were seen to hang; one at a time


def score_cadtestbench(task: Task, d: Path) -> dict:
    step = d / "result.step"
    out = d / "cadtests.json"
    args = ["uv", "run", "--no-project", "--with", "cadquery", "python", "-I",
            str(HERE / "cadtest_adapter.py"), str(step), task.meta["cadtests"], str(out)]
    with _CQ_LOCK:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=600, cwd=d)
            err = p.stderr
        except subprocess.TimeoutExpired:
            err = "cadtest adapter timed out"
    r = read_json(out)
    if r is None:
        return {"pass_rate": 0.0, "rs": 0.0, "pr": False, "error": err[-800:]}
    from . import geometry as g
    valid = step.is_file() and g.validity(step)[0].valid
    return {"pass_rate": r["pass_rate"], "rs": r["rs"], "pr": r["pr"], "passed": r["passed"],
            "total": r["total"], "load_error": r["load_error"], "by_type": r["by_type"],
            "valid": bool(valid)}


def score_functional(task: Task, d: Path) -> dict:
    from .functional import tasks as ft
    r = ft.score(task.id, d)
    from . import geometry as g
    r["valid"] = (d / "result.step").is_file() and g.validity(d / "result.step")[0].valid
    return r


SCORERS = {"cadbench": score_cadbench, "cadtestbench": score_cadtestbench,
           "functional": score_functional}


def score_dir(task: Task, d: Path, force: bool = False) -> dict:
    f = d / "score.json"
    if f.is_file() and not force:
        return json.loads(f.read_text())
    try:
        r = SCORERS[task.suite](task, d)
    except Exception as e:  # noqa: BLE001
        r = {"error": f"{type(e).__name__}: {e}", "valid": False}
    write_json(f, r)
    return r


if __name__ == "__main__":
    t = Task.from_json(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(score_dir(t, Path(sys.argv[2]), force=True), indent=1)[:3000])
