"""The BASELINE's only tool: a tiny sandboxed build123d executor, served over MCP (stdio).

    python -I baseline_exec_mcp.py --jobs DIR

Tool `run_build123d(code)`: the code runs in a fresh process under the same macOS sandbox
AgentCAD gives its own CAD worker (no network, no new processes, writes only into the job dir),
with a 120 s timeout. The code must assign `result`: a build123d shape (one part) or a dict
{name: shape} (several parts, placed in world coordinates). The tool answers with the error
(traceback tail) or a summary: solids, volume, bounding box, OCCT validity. Every successful
run is exported to DIR/job_<n>/result.step; the last successful one is the answer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RUNNER = r'''
import json, sys, traceback
from pathlib import Path
job = Path(sys.argv[1])
out = {"ok": False}
try:
    import build123d as b3
    from build123d import *  # noqa: F401,F403 - the script's namespace, as in a build123d file
    ns = {"__name__": "__main__"}
    exec("from build123d import *\nimport math\n" + (job / "code.py").read_text(), ns)
    res = ns.get("result")
    if res is None:
        raise RuntimeError("the code must assign the final shape to `result`")
    def as_shape(s):
        if hasattr(s, "part") and not isinstance(s, b3.Shape):
            s = s.part                       # a BuildPart context
        if not isinstance(s, b3.Shape):
            raise TypeError(f"result must be a build123d Shape, got {type(s).__name__}")
        return s
    if isinstance(res, dict):
        items = [(str(k), as_shape(v)) for k, v in res.items()]
    else:
        items = [("part", as_shape(res))]
    comp = job / "components"; comp.mkdir(exist_ok=True)
    for k, s in items:
        s.label = k
        b3.export_step(s, str(comp / (k.replace("/", "_") + ".step")))
    shape = items[0][1] if len(items) == 1 else b3.Compound(children=[s for _, s in items])
    b3.export_step(shape, str(job / "result.step"))
    b3.export_stl(shape, str(job / "result.stl"))
    bb = shape.bounding_box()
    out = {"ok": True, "parts": [k for k, _ in items], "solids": len(shape.solids()),
           "volume_mm3": round(shape.volume, 4), "valid": bool(shape.is_valid),
           "bbox_min": [round(v, 4) for v in tuple(bb.min)],
           "bbox_max": [round(v, 4) for v in tuple(bb.max)]}
except BaseException:
    out = {"ok": False, "error": traceback.format_exc()[-2500:]}
(job / "out.json").write_text(json.dumps(out))
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True, type=Path)
    a = ap.parse_args()
    jobs = a.jobs.resolve()
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "runner.py").write_text(RUNNER)

    from mcp.server.mcpserver import MCPServer
    from mises.platform import sandboxed_command

    mcp = MCPServer("bench")

    @mcp.tool()
    def run_build123d(code: str) -> str:
        """Run build123d Python code in a sandbox. Assign the final shape to `result` (one
        part) or a dict {name: shape} for several parts in place. Returns the error, or the
        solids, volume, bounding box and validity of `result`. The last successful run is your
        answer."""
        n = len(list(jobs.glob("job_*"))) + 1
        job = jobs / f"job_{n:03d}"
        job.mkdir()
        (job / "code.py").write_text(code)
        argv = sandboxed_command([sys.executable, "-I", str(jobs / "runner.py"), str(job)], job,
                                 read_dirs=[jobs])
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return json.dumps({"ok": False, "error": "timed out after 120 s"})
        try:
            out = json.loads((job / "out.json").read_text())
        except (OSError, ValueError):
            out = {"ok": False, "error": (p.stderr or p.stdout or "crashed")[-2500:]}
        if out.get("ok"):
            (jobs / "LAST_OK").write_text(job.name)
        return json.dumps(out)

    mcp.run()


if __name__ == "__main__":
    main()
