"""The sandboxed dynamics worker (Plan 10 C3): python -I worker.py <job.json>.

Like the CAD worker it never imports mises: it loads its siblings (mjmodel.py, traj.py,
engine.py) by path, runs the job and writes result.json ({ok, result | error, nonce}) next to
the job. A bad model can crash this process, never the server.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"mises_dyn_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    job_path = Path(argv[1])
    out_path = job_path.parent / "result.json"
    nonce = os.environ.get("MISES_NONCE", "")
    try:
        mjmodel = _load("mjmodel")
        traj = _load("traj")
        engine = _load("engine")
        engine.mjmodel, engine.trajmod = mjmodel, traj
        job = json.loads(job_path.read_text())
        out = {"ok": True, "result": engine.run(job)}
    except Exception as e:  # noqa: BLE001 - reported to the host, never raised past here
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:2000]}
    out["nonce"] = nonce
    out_path.write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
