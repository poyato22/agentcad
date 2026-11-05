"""Score the CadQuery programs CADBench published for frontier models, on OUR held-out tasks.

The CADBench repo ships each model's generated code per file_id (tested_vlms/*), so those systems
can be compared on the exact same tasks and metric at no model cost. Available for family B
(DeepCAD) only: bench0 for Claude Opus 4.7, Gemini 3.1 Pro and GPT-5.4 (one run each). The
repo has no bench0F or bench2 outputs, so family F and M cannot be compared this way.

The programs are third-party code: each runs in a fresh process under the macOS sandbox (no
network, no new processes, writes only into its own directory), with a 120 s limit, in its own
CadQuery environment. A program that fails counts as a failure (0), as in CADBench.

    python -m evals.bench.published_cadbench RUNS REPO_DIR
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from mises.platform import sandboxed_command

from .common import CACHE, write_json

SOURCES = {
    "published@opus47": "cadbench_claude_jsons/bench0_claude",
    "published@gemini31": "cadbench_gemini_jsons/bench0_gemini31_thinking_run2",
    "published@gpt54": "cadbench_gpt_jsons/bench0_gpt",
}
RUNNER = r'''
import sys, json, traceback
from pathlib import Path
job = Path(sys.argv[1])
try:
    import cadquery as cq
    ns = {}
    exec((job / "code.py").read_text(), ns)
    res = ns.get("result", ns.get("solid"))
    if res is None:
        raise RuntimeError("no result variable")
    cq.exporters.export(res, str(job / "result.step"))
    cq.exporters.export(res, str(job / "result.stl"))
    (job / "exec.json").write_text(json.dumps({"ok": True}))
except BaseException:
    (job / "exec.json").write_text(json.dumps({"ok": False, "error": traceback.format_exc()[-1500:]}))
'''
FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


def extract(gen: str) -> str:
    m = FENCE.findall(gen or "")
    return m[-1] if m else (gen or "")


def cq_python() -> str:
    """A kept CadQuery venv (uv's --with environments are temporary)."""
    venv = CACHE / "envs" / "cq"
    py = venv / "bin" / "python"
    if not py.is_file():
        subprocess.run(["uv", "venv", "--python", "3.13", str(venv)], check=True,
                       capture_output=True, text=True, timeout=300)
        subprocess.run(["uv", "pip", "install", "--python", str(py), "cadquery"], check=True,
                       capture_output=True, text=True, timeout=1200)
    return str(py)


def main(runs: Path, repo: Path) -> None:
    py = cq_python()
    tools = CACHE / "published_runner"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "runner.py").write_text(RUNNER)
    tasks = [d.name for d in sorted((runs / "cadbench_t2").iterdir()) if d.name.startswith("B-")]
    for system, rel in SOURCES.items():
        by_id = {}
        for level in ("easy", "medium", "hard"):
            for row in json.load(open(repo / "CADBenchEval/tested_vlms" / rel / f"{level}.json")):
                by_id[row["file_id"]] = row.get("generated")
        for tid in tasks:
            d = runs / "cadbench_t2" / tid / system
            if (d / "run.json").is_file():
                continue
            d.mkdir(parents=True, exist_ok=True)
            fid = tid.split("-", 1)[1]
            row = {"system": system, "id": tid, "model": system.split("@")[1], "usd": None,
                   "source": "published CADBench output"}
            if fid not in by_id:
                row["error"] = "no published output"
            else:
                (d / "code.py").write_text(extract(by_id[fid]))
                argv = sandboxed_command([py, "-I", str(tools / "runner.py"), str(d.resolve())],
                                         d.resolve(), read_dirs=[tools])
                try:
                    subprocess.run(argv, capture_output=True, text=True, timeout=120)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    row["exec"] = json.loads((d / "exec.json").read_text())
                except (OSError, ValueError):
                    row["exec"] = {"ok": False, "error": "crashed or timed out"}
                row["exported"] = (d / "result.step").is_file()
            write_json(d / "run.json", row)
            print(system, tid, row.get("exec", {}).get("ok"), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
