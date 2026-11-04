"""Score the reference answers: every checker should give its reference full marks.

    cd core && uv run python -m evals.bench.functional.score_refs OUT_DIR [task ...]
"""
import json
import sys
import time
from pathlib import Path

from . import reference, tasks


def main(argv):
    out = Path(argv[0])
    ids = argv[1:] or [*reference.SINGLE, *reference.EDITS]
    for tid in ids:
        d = out / tid
        t0 = time.time()
        try:
            reference.build(tid, d)
        except Exception as e:  # noqa: BLE001
            print(f"{tid}: reference build failed: {type(e).__name__}: {e}")
            continue
        r = tasks.score(tid, d)
        print(f"{tid}: {r['passed']}/{r['n_req']} ({time.time() - t0:.1f}s)")
        for q in r["requirements"]:
            if not q["passed"]:
                print(f"    FAIL {q['name']}: {q['detail']}")
        (d / "score.json").write_text(json.dumps(r, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
