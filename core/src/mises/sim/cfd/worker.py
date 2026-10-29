"""CFD worker process: python -I -m mises.sim.cfd.worker <job_dir>

Reads <job_dir>/input.json (normalized wind settings) and tris.npy (the part's triangles, mm),
writes progress.json while it runs and result.json (+ result.glb, fields.npz, slice PNGs) at
the end. It runs sandboxed (writes only inside job_dir, no network) with a hard timeout; the
parent kills its process group on cancel.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _write(path: Path, obj) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, default=float))
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    import numpy as np

    from .wind import run_study

    job = Path(argv[1])
    s = json.loads((job / "input.json").read_text())
    tris = np.load(job / "tris.npy", allow_pickle=False)
    last = [0.0]

    def progress(k: int, n: int, step: int, steps: int) -> None:
        now = time.monotonic()
        if now - last[0] < 0.5 and step < steps:
            return
        last[0] = now
        _write(job / "progress.json", {"angle": k, "angles": n, "step": step, "steps": steps,
                                       "pct": round(100 * (k + step / max(steps, 1)) / n, 1)})

    res = run_study(tris, s, job, progress=progress)
    _write(job / "result.json", res)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"CFD_ERROR: {type(e).__name__}: {e}\n")
        sys.exit(1)
