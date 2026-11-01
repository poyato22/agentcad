"""Sandboxed interference check of a concept massing (Plan 10 C3, E26):
python -I clash_worker.py <job dir>.

Reads <job dir>/model.step (a host-made copy), splits it into its solids and measures the
common volume of every pair whose boxes overlap. Writes result.json {nonce, solids, pairs}.
Never imports mises (it runs with -I in the build sandbox).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_SOLIDS = 60


def main(job: Path) -> int:
    nonce = os.environ.get("MISES_NONCE", "")
    out: dict = {"nonce": nonce}
    try:
        from build123d import import_step
        shape = import_step(str(job / "model.step"))
        solids = list(shape.solids())
        info = []
        for i, s in enumerate(solids):
            bb = s.bounding_box()
            c = bb.center()
            info.append({"index": i, "volume_mm3": float(s.volume),
                         "center": [round(c.X, 2), round(c.Y, 2), round(c.Z, 2)],
                         "size": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
                         "label": getattr(s, "label", "") or ""})
        pairs = []
        if len(solids) <= MAX_SOLIDS:
            boxes = [s.bounding_box() for s in solids]
            for i in range(len(solids)):
                for j in range(i + 1, len(solids)):
                    a, b = boxes[i], boxes[j]
                    if (a.max.X < b.min.X or b.max.X < a.min.X or a.max.Y < b.min.Y
                            or b.max.Y < a.min.Y or a.max.Z < b.min.Z or b.max.Z < a.min.Z):
                        continue
                    try:
                        common = solids[i].intersect(solids[j])
                        if common is None:
                            v = 0.0
                        elif hasattr(common, "volume"):
                            v = float(common.volume)
                        else:                      # a ShapeList of the common solids
                            v = sum(float(getattr(x, "volume", 0.0)) for x in common)
                    except Exception:  # noqa: BLE001 - a failed boolean is not a clash
                        continue
                    if v > 0.0:
                        pairs.append({"a": i, "b": j, "volume_mm3": v})
        else:
            out["note"] = f"{len(solids)} solids: too many to check pairwise (max {MAX_SOLIDS})"
        out.update(ok=True, solids=info, pairs=pairs)
    except Exception as e:  # noqa: BLE001 - reported to the host
        out.update(ok=False, error=f"{type(e).__name__}: {e}"[:1000])
    (job / "result.json").write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
