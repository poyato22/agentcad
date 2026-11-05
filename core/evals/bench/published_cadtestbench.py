"""Score the programs CADTestBench ships for its paper baselines (the ReAct runs of GPT-5.2 and
Claude 4.6 Sonnet per their output paths, detailed partition) on the same 50 held-out sample ids and with the same adapter as rounds 1-2,
at no model cost. The programs are third-party code: each runs in a fresh process under the macOS
sandbox (no network, no new processes, writes only into its own directory), 120 s limit.

    python -m evals.bench.published_cadtestbench OUT_DIR RESULTS_JSON
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from mises.platform import sandboxed_command

from .common import CACHE, HERE, read_json, write_json
from .datasets import fetch_cadtestbench
from .published_cadbench import cq_python

MODELS = {"published@gpt52": "GPT-5.2", "published@claude46sonnet": "Claude-4.6-Sonnet"}
RAW = "https://raw.githubusercontent.com/dimitrismallis/CADTestBench/main/baselines"
RUNNER = r'''
import sys, json, traceback
from pathlib import Path
job = Path(sys.argv[1])
try:
    import cadquery as cq
    ns = {"__name__": "__main__"}
    src = "\n".join(ln for ln in (job / "code.py").read_text().splitlines()
                    if not ln.startswith("cq.exporters.export("))   # their run's own STL dump
    exec(src, ns)
    res = ns.get("final_result")
    if res is None:
        raise RuntimeError("no final_result")
    cq.exporters.export(res, str(job / "result.step"))
    (job / "exec.json").write_text(json.dumps({"ok": True}))
except BaseException:
    (job / "exec.json").write_text(json.dumps({"ok": False, "error": traceback.format_exc()[-1500:]}))
'''


def main(out: Path, results_json: Path) -> None:
    keep = set(read_json(results_json)["tasks"]["cadtestbench_test"])
    tasks = [t for t in fetch_cadtestbench(split="test") if t.id in keep]
    py = cq_python()
    tools = CACHE / "published_runner_ctb"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "runner.py").write_text(RUNNER)
    for system, folder in MODELS.items():
        for t in tasks:
            d = (out / t.id / system).resolve()
            if (d / "score.json").is_file():
                continue
            d.mkdir(parents=True, exist_ok=True)
            url = f"{RAW}/{folder}/Detailed/generated_models/{t.id}/gpt_generated.py"
            row = {"id": t.id, "system": system}
            try:
                for stale in ("exec.json", "result.step", "cadtests.json"):
                    (d / stale).unlink(missing_ok=True)
                (d / "code.py").write_bytes(urllib.request.urlopen(url, timeout=60).read())
                argv = sandboxed_command([py, "-I", str(tools / "runner.py"), str(d)], d,
                                         read_dirs=[tools])
                subprocess.run(argv, capture_output=True, text=True, timeout=120)
                row["exec"] = read_json(d / "exec.json", {"ok": False, "error": "crashed"})
            except Exception as e:  # noqa: BLE001
                row["exec"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            subprocess.run(["uv", "run", "--no-project", "--with", "cadquery", "python", "-I",
                            str(HERE / "cadtest_adapter.py"), str(d / "result.step"),
                            t.meta["cadtests"], str(d / "cadtests.json")],
                           capture_output=True, text=True, timeout=600, cwd=d)
            r = read_json(d / "cadtests.json") or {}
            row.update({k: r.get(k) for k in ("pass_rate", "rs", "pr", "passed", "total")})
            write_json(d / "score.json", row)
            print(system, t.id, row["exec"].get("ok"), row.get("pass_rate"), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
