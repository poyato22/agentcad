"""Round 3 analysis, exactly as fixed in results/round3_preregistration.md.

    python -m evals.bench.round3_analysis RUNS_R3 OUT.json
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

import numpy as np

CONTRASTS = [("mises@sonnet", "baseline@sonnet"), ("mises@sonnet", "baseline@opus"),
             ("mises@opus", "baseline@opus"), ("mises@opus", "baseline@fable")]
SYSTEMS = ["mises@opus", "mises@sonnet", "baseline@opus", "baseline@fable", "baseline@sonnet"]
PUBLISHED = ["published@opus47", "published@gemini31", "published@gpt54"]


def load(root: Path) -> dict:
    data: dict = {}
    for tdir in sorted((root / "cadbench_t2").iterdir()):
        for sdir in tdir.iterdir():
            sc, rn = sdir / "score.json", sdir / "run.json"
            if sc.is_file() and rn.is_file():
                data.setdefault(sdir.name, {})[tdir.name] = {
                    "score": json.loads(sc.read_text()), "run": json.loads(rn.read_text())}
    return data


def boot_ci(d: np.ndarray, n: int = 10000, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n, len(d)))
    m = d[idx].mean(axis=1)
    return [float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]


def perm_p(d: np.ndarray, n: int = 100_000, seed: int = 0) -> float:
    rng = np.random.default_rng(seed)
    obs = abs(d.mean())
    signs = rng.choice([-1.0, 1.0], size=(n, len(d)))
    return float(((np.abs((signs * d).mean(axis=1)) >= obs - 1e-12).sum() + 1) / (n + 1))


def vec(data: dict, system: str, ids: list[str], key: str = "official_iou") -> np.ndarray:
    return np.array([float(data[system][i]["score"].get(key) or 0.0) for i in ids])


def contrast(data, a, b, ids, key="official_iou") -> dict:
    x, y = vec(data, a, ids, key), vec(data, b, ids, key)
    d = x - y
    return {"a": a, "b": b, "n": len(ids), "a_mean": float(x.mean()), "b_mean": float(y.mean()),
            "mean_diff": float(d.mean()), "ci95": boot_ci(d), "p_two_sided": perm_p(d),
            "wins": int((d > 1e-9).sum()), "losses": int((d < -1e-9).sum()),
            "ties": int((np.abs(d) <= 1e-9).sum())}


def summarise(data, system, ids, tiers) -> dict:
    v = vec(data, system, ids)
    rows = [data[system][i] for i in ids]
    usd = [r["run"].get("usd") for r in rows if r["run"].get("usd") is not None]
    t = [r["run"].get("agent_seconds") or r["run"].get("seconds") for r in rows
         if r["run"].get("agent_seconds") or r["run"].get("seconds")]
    out = {"n": len(ids), "official_iou_mean": float(v.mean()), "official_iou_median": float(np.median(v)),
           "official_iou_mean_ci95": boot_ci(v),
           "official_siou_mean": float(vec(data, system, ids, "official_siou").mean()),
           "harness_iou_mean": float(vec(data, system, ids, "iou").mean()),
           "harness_f1_mean": float(vec(data, system, ids, "f1").mean()),
           "vsr": float(np.mean([bool(r["run"].get("exported")) and "official_error" not in r["score"]
                                 and (r["score"].get("official_iou") or 0) > 0 for r in rows])),
           "strict_valid_rate": float(np.mean([bool(r["score"].get("valid")) for r in rows])),
           "timeouts": sum(bool(r["run"].get("timed_out")) for r in rows),
           "errors": sum(bool(r["run"].get("error")) for r in rows),
           "mean_usd_list": float(np.mean(usd)) if usd else None,
           "total_usd_list": float(np.sum(usd)) if usd else None,
           "median_seconds": float(st.median(t)) if t else None}
    fams: dict = {}
    for fam in ("B", "F", "M"):
        s = [i for i in ids if i.startswith(fam + "-")]
        if s:
            fams[fam] = {"n": len(s), "mean": float(vec(data, system, s).mean()),
                         "median": float(np.median(vec(data, system, s)))}
    out["by_family"] = fams
    cells: dict = {}
    for k, s in tiers.items():
        s2 = [i for i in s if i in ids]
        if s2:
            cells[k] = {"n": len(s2), "median": float(np.median(vec(data, system, s2)))}
    out["by_cell_median"] = cells
    return out


def tiers_of() -> dict:
    from .datasets import fetch_cadbench
    out: dict = {}
    for t in fetch_cadbench(split="test2"):
        fam, lab = t.meta["family"], t.meta["label"]
        out.setdefault(fam if fam == "M" else f"{fam}-{lab}", []).append(t.id)
    return out


def main(root: Path, dest: Path) -> None:
    data = load(root)
    tiers = tiers_of()
    all_ids = sorted(i for s in tiers.values() for i in s)
    complete = [s for s in SYSTEMS if s in data and all(i in data[s] for i in all_ids)]
    ids = [i for i in all_ids if all(i in data[s] for s in SYSTEMS if s in data)]
    res = {"n_common": len(ids), "complete_systems": complete,
           "systems": {s: summarise(data, s, ids, tiers) for s in SYSTEMS if s in data}}
    cons = [contrast(data, a, b, ids) for a, b in CONTRASTS if a in data and b in data]
    order = sorted(range(len(cons)), key=lambda k: cons[k]["p_two_sided"])
    m, running = len(cons), 0.0
    for rank, k in enumerate(order):
        running = max(running, min(1.0, (m - rank) * cons[k]["p_two_sided"]))
        cons[k]["p_holm"] = running
        ci = cons[k]["ci95"]
        cons[k]["verdict"] = ("beats" if running < 0.05 and ci[0] > 0 else
                              "loses" if running < 0.05 and ci[1] < 0 else
                              "no detectable difference")
    res["primary_contrasts"] = cons
    res["secondary_harness_metric_contrasts"] = [contrast(data, a, b, ids, "iou")
                                                 for a, b in CONTRASTS if a in data and b in data]
    b_ids = [i for i in ids if i.startswith("B-")]
    pub = {}
    for p in PUBLISHED:
        if p in data:
            # a task with no published output is left out of that model's contrast, not scored 0:
            # the repo does not say whether it failed or was simply not uploaded
            have = [i for i in b_ids if i in data[p] and not data[p][i]["run"].get("error")]
            pub[p] = {"summary": summarise(data, p, have, tiers),
                      "vs_mises_opus": contrast(data, "mises@opus", p, have) if "mises@opus" in data else None,
                      "vs_mises_sonnet": contrast(data, "mises@sonnet", p, have) if "mises@sonnet" in data else None,
                      "baseline_opus_vs": contrast(data, "baseline@opus", p, have) if "baseline@opus" in data else None}
    res["published_family_B_only"] = pub
    Path(dest).write_text(json.dumps(res, indent=1))
    for s, v in res["systems"].items():
        print(f"{s:18s} n={v['n']} mean {v['official_iou_mean']:.3f} {np.round(v['official_iou_mean_ci95'],3).tolist()} "
              f"median {v['official_iou_median']:.3f} siou {v['official_siou_mean']:.3f} "
              f"harness {v['harness_iou_mean']:.3f} vsr {v['vsr']:.2f} ${v['mean_usd_list']} t {v['median_seconds']} "
              f"fam { {k: round(x['mean'], 3) for k, x in v['by_family'].items()} }")
    for c in cons:
        print(c["a"], "vs", c["b"], round(c["mean_diff"], 4), np.round(c["ci95"], 4).tolist(),
              "p", round(c["p_two_sided"], 4), "holm", round(c["p_holm"], 4), c["wins"], c["losses"], c["ties"], c["verdict"])
    for p, v in pub.items():
        print(p, json.dumps({k: (round(x["mean_diff"], 3), np.round(x["ci95"], 3).tolist(), x["wins"], x["losses"])
                             for k, x in v.items() if k != "summary" and x}), round(v["summary"]["official_iou_mean"], 3),
              round(v["summary"]["vsr"], 2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
