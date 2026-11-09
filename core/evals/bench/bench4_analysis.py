"""BENCH4 analysis (CADPrompt), exactly as fixed in results/bench4_preregistration.md.

    python -m evals.bench.bench4_analysis RUNS OUT.json

Primary: CADPrompt point-cloud distance (lower is better; a missing shape scores sqrt(3)),
AgentCAD + Sonnet 5.5 vs Sonnet 5.5 alone, paired over the tasks both systems ran.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from .round3_analysis import boot_ci, perm_p

A, B = "mises@sonnet", "baseline@sonnet"
TIE = 0.005      # |difference| in unit-cube units counted as a tie (point sampling noise)

#: CADPrompt Table 2 (Best Refine), median (IQR), n = 200; copied from arXiv 2410.05340v2
PUBLISHED = [
    # system, IoGT, point-cloud distance, Hausdorff, compile rate
    ("GPT-4 zero-shot, generated", (0.935, 0.043), (0.153, 0.146), (0.484, 0.405), 0.920),
    ("GPT-4 zero-shot, CADCodeVerify", (0.941, 0.034), (0.132, 0.137), (0.455, 0.354), 0.940),
    ("GPT-4 few-shot, generated", (0.939, 0.030), (0.155, 0.140), (0.494, 0.368), 0.960),
    ("GPT-4 few-shot, CADCodeVerify", (0.944, 0.028), (0.127, 0.135), (0.419, 0.356), 0.965),
    ("Gemini 1.5 Flash zero-shot, CADCodeVerify", (0.914, 0.082), (0.138, 0.165), (0.497, 0.384), 0.845),
    ("Gemini 1.5 Flash few-shot, CADCodeVerify", (0.939, 0.052), (0.147, 0.160), (0.492, 0.358), 0.850),
    ("CodeLlama 70B few-shot, CADCodeVerify", (0.935, 0.957), (0.185, 1.620), (0.582, 1.366), 0.735),
]


def load(root: Path) -> dict:
    data: dict = {}
    for tdir in sorted((root / "cadprompt").iterdir()):
        for sdir in tdir.iterdir():
            sc, rn = sdir / "score.json", sdir / "run.json"
            if sc.is_file() and rn.is_file():
                run = json.loads(rn.read_text())
                if str(run.get("error", "")).startswith("skipped"):
                    continue
                data.setdefault(sdir.name, {})[tdir.name] = {
                    "score": json.loads(sc.read_text()), "run": run}
    return data


def _get(row: dict, key: str) -> float:
    s = row["score"]
    if key in ("pcd", "hausdorff"):
        return float(s.get(key, np.sqrt(3)) if s.get("compiled") else np.sqrt(3))
    if key == "compiled":
        return float(bool(s.get("compiled")))
    return float(s.get(key) or 0.0)


def vec(data, system, ids, key):
    return np.array([_get(data[system][i], key) for i in ids])


def med_iqr(v: np.ndarray) -> list[float]:
    q1, q2, q3 = np.percentile(v, [25, 50, 75])
    return [float(q2), float(q3 - q1)]


def summarise(data, system, ids) -> dict:
    rows = [data[system][i] for i in ids]
    out = {"n": len(ids)}
    for k in ("pcd", "hausdorff", "iogt", "official_iou"):
        v = vec(data, system, ids, k)
        out[k] = {"mean": float(v.mean()), "mean_ci95": boot_ci(v), "median_iqr": med_iqr(v)}
    out["compile_rate"] = float(vec(data, system, ids, "compiled").mean())
    out["valid_rate"] = float(np.mean([bool(r["score"].get("valid")) for r in rows]))
    usd = [r["run"].get("usd") for r in rows if r["run"].get("usd") is not None]
    secs = [r["run"].get("seconds") for r in rows if r["run"].get("seconds") is not None]
    out["usd_mean"] = float(np.mean(usd)) if usd else None
    out["usd_total"] = float(np.sum(usd)) if usd else None
    out["seconds_median"] = float(np.median(secs)) if secs else None
    out["cap_hits"] = sum(1 for r in rows if r["run"].get("stop") == "timeout")
    out["errors"] = sorted(i for i, r in zip(ids, rows) if r["run"].get("error"))
    return out


def contrast(data, ids, key, lower_better: bool, tie: float) -> dict:
    x, y = vec(data, A, ids, key), vec(data, B, ids, key)
    d = (y - x) if lower_better else (x - y)        # positive = AgentCAD better
    return {"metric": key, "orientation": "positive = AgentCAD better", "n": len(ids),
            "agentcad_mean": float(x.mean()), "baseline_mean": float(y.mean()),
            "mean_improvement": float(d.mean()), "ci95": boot_ci(d), "p_two_sided": perm_p(d),
            "median_improvement": float(np.median(d)),
            "wins": int((d > tie).sum()), "losses": int((d < -tie).sum()),
            "ties": int((np.abs(d) <= tie).sum()), "tie_threshold": tie,
            "per_task": {i: float(v) for i, v in zip(ids, d)}}


def verdict(c: dict) -> str:
    lo, hi = c["ci95"]
    if c["p_two_sided"] < 0.05 and lo > 0:
        return "AgentCAD better"
    if c["p_two_sided"] < 0.05 and hi < 0:
        return "AgentCAD worse"
    return "no detectable difference at this sample size"


def analyse(root: Path) -> dict:
    data = load(root)
    ids = sorted(set(data.get(A, {})) & set(data.get(B, {})))
    prim = contrast(data, ids, "pcd", True, TIE)
    prim["verdict"] = verdict(prim)
    sec = [contrast(data, ids, "official_iou", False, 1e-3),
           contrast(data, ids, "hausdorff", True, TIE),
           contrast(data, ids, "iogt", False, 1e-3),
           contrast(data, ids, "compiled", False, 0.5)]
    for c in sec:
        c["verdict"] = verdict(c)
    return {"benchmark": "CADPrompt (arXiv 2410.05340), ranks 100-199 of the seed-0 ranking",
            "ids": ids, "systems": {s: summarise(data, s, ids) for s in (A, B)},
            "primary": prim, "secondary": sec,
            "published_table2_median_iqr": [
                {"system": s, "iogt": a, "pcd": b, "hausdorff": c, "compile_rate": r}
                for s, a, b, c, r in PUBLISHED]}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    out = analyse(Path(argv[0]))
    Path(argv[1]).write_text(json.dumps(out, indent=1))
    p = out["primary"]
    print(f"n={p['n']} pcd improvement {p['mean_improvement']:+.4f} CI {p['ci95']} "
          f"p={p['p_two_sided']:.4g} W/L/T {p['wins']}/{p['losses']}/{p['ties']} -> {p['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
