"""Same 50 held-out CADTestBench ids: round-2 AgentCAD and Sonnet 5.5 baseline (from the committed
results) vs the ReAct programs the benchmark authors published, all through the same adapter.

    python -m evals.bench.round3_ctb PUBLISHED_DIR RESULTS_JSON OUT.json
"""
import json
import sys
from pathlib import Path

import numpy as np

from .round3_analysis import boot_ci, perm_p


def main(pub: Path, results: Path, dest: Path) -> None:
    res = json.loads(results.read_text())
    tasks = res["tasks"]["cadtestbench_test"]
    sm = res["summary"]["cadtestbench_test"]
    out = {"n": len(tasks), "agentcad_sonnet": {k: sm["mises"][k] for k in ("pass_rate", "rs", "pr")},
           "baseline_sonnet55": {k: sm["baseline"][k] for k in ("pass_rate", "rs", "pr")}}
    ids = sorted(tasks)
    ours = np.array([tasks[i]["mises"]["headline"] for i in ids])
    base = np.array([tasks[i]["baseline"]["headline"] for i in ids])
    for name in ("published@gpt52", "published@claude46sonnet"):
        rows = [json.loads((pub / i / name / "score.json").read_text()) for i in ids]
        p = np.array([r.get("pass_rate") or 0.0 for r in rows])
        d = ours - p
        d2 = base - p
        out[name] = {"pass_rate": float(p.mean()),
                     "rs": float(np.mean([r.get("rs") or 0.0 for r in rows])),
                     "pr": float(np.mean([bool(r.get("pr")) for r in rows])),
                     "exec_ok": sum(bool(r["exec"].get("ok")) for r in rows),
                     "agentcad_minus": {"mean_diff": float(d.mean()), "ci95": boot_ci(d),
                                        "p": perm_p(d), "wins": int((d > 1e-9).sum()),
                                        "losses": int((d < -1e-9).sum())},
                     "baseline_sonnet55_minus": {"mean_diff": float(d2.mean()), "ci95": boot_ci(d2),
                                                 "p": perm_p(d2), "wins": int((d2 > 1e-9).sum()),
                                                 "losses": int((d2 < -1e-9).sum())}}
    dest.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
