"""Aggregate scored runs into benchmarks.json, benchmarks.md and a chart PNG.

Headline metric per suite (0..1, higher is better), invalid/missing outputs counted as 0:
- cadbench: volumetric IoU after alignment (also F1@1%, Chamfer over valid, validity rate);
- cadtestbench: CADTest pass rate (also requirement satisfaction RS, all-pass rate PR);
- functional: mean requirement pass (also strict pass rate), per category.
Only tasks scored for BOTH systems are compared (paired); n is that count.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

from .common import read_json, write_json

SYSTEMS = ("mises", "baseline")
SUITE_TITLES = {"cadbench": "CADBench (image→part)", "cadtestbench": "CADTestBench (text→part)",
                "functional": "Mises-Functional (engineering)",
                "cadbench_test": "CADBench, held-out (image→part)",
                "cadtestbench_test": "CADTestBench, held-out (text→part)",
                "physics_dev": "Mises-Physics, dev", "physics_test": "Mises-Physics, held-out"}
METRIC = {"cadbench": "volume IoU", "cadtestbench": "CADTest pass rate",
          "functional": "requirements passed", "physics": "requirements passed"}


def family(suite: str) -> str:
    return suite.split("_")[0]


def bootstrap_ci(diffs: list[float], n: int = 4000, seed: int = 0) -> tuple[float, float]:
    """95% percentile bootstrap CI of the mean of paired differences."""
    import random
    if not diffs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    k = len(diffs)
    means = sorted(sum(diffs[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return (means[int(0.025 * n)], means[int(0.975 * n) - 1])


def _mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else None


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def collect(runs: Path) -> dict:
    data: dict = {}
    for suite in SUITE_TITLES:
        root = runs / suite
        if not root.is_dir():
            continue
        rows = {}
        for tdir in sorted(root.iterdir()):
            per = {}
            for s in SYSTEMS:
                sc, rn = read_json(tdir / s / "score.json"), read_json(tdir / s / "run.json")
                if sc is not None and rn is not None:
                    per[s] = {"score": sc, "run": rn}
            if len(per) == len(SYSTEMS):
                rows[tdir.name] = per
        data[suite] = rows
    return data


def headline(suite: str, sc: dict) -> float:
    suite = family(suite)
    if suite == "cadbench":
        return float(sc.get("iou") or 0.0)
    if suite == "cadtestbench":
        return float(sc.get("pass_rate") or 0.0)
    return float(sc.get("score") or 0.0)


def summarise(suite: str, rows: dict) -> dict:
    out = {"n": len(rows)}
    fam = family(suite)
    for s in SYSTEMS:
        sc = [r[s]["score"] for r in rows.values()]
        rn = [r[s]["run"] for r in rows.values()]
        d = {"headline": _mean([headline(suite, x) for x in sc]),
             "valid_rate": _mean([1.0 if x.get("valid") else 0.0 for x in sc]),
             "median_seconds": _median([x.get("seconds") for x in rn]),
             "median_agent_seconds": _median([x.get("agent_seconds") or x.get("seconds")
                                              for x in rn]),
             "mean_usd": _mean([x.get("usd") for x in rn]),
             "total_usd": sum(x.get("usd") or 0 for x in rn),
             "timeouts": sum(1 for x in rn if x.get("timed_out")),
             "errors": sum(1 for x in rn if x.get("error"))}
        if fam == "cadbench":
            d["iou"] = d["headline"]
            d["f1"] = _mean([x.get("f1") or 0.0 for x in sc])
            d["chamfer_valid"] = _mean([x.get("chamfer") for x in sc if x.get("valid")])
            fams = {}
            for tid, r in rows.items():
                fams.setdefault(tid.split("-")[0], []).append(headline(suite, r[s]["score"]))
            d["by_family"] = {k: _mean(v) for k, v in sorted(fams.items())}
        elif fam == "cadtestbench":
            d["pass_rate"] = d["headline"]
            d["rs"] = _mean([x.get("rs") or 0.0 for x in sc])
            d["pr"] = _mean([1.0 if x.get("pr") else 0.0 for x in sc])
            types: dict = {}
            for x in sc:
                for k, (p, t) in (x.get("by_type") or {}).items():
                    a = types.setdefault(k, [0, 0])
                    a[0] += p
                    a[1] += t
            d["by_type"] = {k: v[0] / v[1] for k, v in sorted(types.items()) if v[1]}
        else:
            d["req_pass"] = d["headline"]
            d["strict"] = _mean([1.0 if x.get("strict") else 0.0 for x in sc])
            cats = {}
            for r in rows.values():
                cats.setdefault(r[s]["score"].get("category", "?"), []).append(
                    headline(suite, r[s]["score"]))
            d["by_category"] = {k: _mean(v) for k, v in sorted(cats.items())}
            if fam == "physics":
                d["metrics"] = {tid: r[s]["score"].get("metrics") for tid, r in rows.items()}
        out[s] = d
    wins = sum(1 for r in rows.values()
               if headline(suite, r["mises"]["score"]) > headline(suite, r["baseline"]["score"]) + 1e-9)
    losses = sum(1 for r in rows.values()
                 if headline(suite, r["baseline"]["score"]) > headline(suite, r["mises"]["score"]) + 1e-9)
    diffs = [headline(suite, r["mises"]["score"]) - headline(suite, r["baseline"]["score"])
             for r in rows.values()]
    lo, hi = bootstrap_ci(diffs)
    out["paired"] = {"mises_better": wins, "baseline_better": losses,
                     "ties": len(rows) - wins - losses,
                     "mean_diff": _mean(diffs), "ci95_mean_diff": [lo, hi]}
    if fam in ("cadtestbench", "functional", "physics"):
        key = "pr" if fam == "cadtestbench" else "strict"
        out["paired"]["strict_mises_only"] = sum(1 for r in rows.values() if r["mises"]["score"].get(key)
                                                 and not r["baseline"]["score"].get(key))
        out["paired"]["strict_baseline_only"] = sum(1 for r in rows.values()
                                                    if r["baseline"]["score"].get(key)
                                                    and not r["mises"]["score"].get(key))
    return out


def chart(summary: dict, path: Path, model: str, date: str, label: str = "",
          only: list[str] | None = None, title: str | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager  # noqa: F401

    bg, ink, muted, grid = "#1a1a19", "#f0efec", "#c3c2b7", "#3a3a37"
    col = {"mises": "#3987e5", "baseline": "#d95926"}
    suites = [s for s in (only or SUITE_TITLES) if s in summary and summary[s]["n"]]
    fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    w = 0.36
    for i, s in enumerate(suites):
        for j, sysname in enumerate(SYSTEMS):
            v = summary[s][sysname]["headline"] or 0.0
            x = i + (j - 0.5) * (w + 0.03)
            ax.bar(x, v, w, color=col[sysname], zorder=3, linewidth=0)
            ax.text(x, v + 0.015, f"{100 * v:.0f}%", ha="center", va="bottom", color=ink,
                    fontsize=22, fontweight="bold")
            ax.text(x, -0.035, "Mises" if sysname == "mises" else "Baseline", ha="center",
                    va="top", color=muted, fontsize=14)
    ax.set_xticks(range(len(suites)))
    ax.set_xticklabels([f"{SUITE_TITLES[s]}\n{METRIC[family(s)]} · n = {summary[s]['n']}"
                        for s in suites], color=ink, fontsize=13 if len(suites) > 3 else 15)
    ax.tick_params(axis="x", length=0, pad=34)
    ax.set_ylim(0, 1.08)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0", "25%", "50%", "75%", "100%"], color=muted, fontsize=13)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", color=grid, linewidth=1, zorder=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    fig.text(0.06, 0.93, title or "Mises vs the same model with a plain build123d tool", color=ink,
             fontsize=26, fontweight="bold")
    fig.text(0.06, 0.885, f"{model} · Think: High · one attempt per task · {date}"
             + (f" · {label}" if label else ""), color=muted, fontsize=15)
    handles = [plt.Rectangle((0, 0), 1, 1, color=col[s]) for s in SYSTEMS]
    ax.legend(handles, ["Mises", "Baseline (Claude Code + build123d exec)"], loc="upper right",
              frameon=False, labelcolor=ink, fontsize=14, bbox_to_anchor=(1.0, 1.13), ncol=2)
    fig.subplots_adjust(left=0.06, right=0.98, top=0.8, bottom=0.17)
    fig.savefig(path, facecolor=bg)
    plt.close(fig)


def _pct(v):
    return "–" if v is None else f"{100 * v:.1f}%"


def _num(v, nd=1):
    return "–" if v is None else f"{v:.{nd}f}"


HELD_OUT = ["cadbench_test", "cadtestbench_test", "physics_test", "functional"]


def tables(summary: dict) -> list[str]:
    lines = ["| Suite | n | Metric | Mises | Baseline | Mises − baseline (95% CI) | Mises better / worse / tie |",
             "|---|---|---|---|---|---|---|"]
    for s, sm in summary.items():
        p = sm["paired"]
        ci = p.get("ci95_mean_diff") or [0, 0]
        lines.append(f"| {SUITE_TITLES[s]} | {sm['n']} | {METRIC[family(s)]} | "
                     f"{_pct(sm['mises']['headline'])} | {_pct(sm['baseline']['headline'])} | "
                     f"{100 * p['mean_diff']:+.1f} pts ({100 * ci[0]:+.1f} to {100 * ci[1]:+.1f}) | "
                     f"{p['mises_better']} / {p['baseline_better']} / {p['ties']} |")
    for s, sm in summary.items():
        fam = family(s)
        lines += ["", f"### {SUITE_TITLES[s]} (n = {sm['n']})", "", "| | Mises | Baseline |",
                  "|---|---|---|"]
        keys = [("valid_rate", "valid output", _pct)]
        if fam == "cadbench":
            keys += [("iou", "volume IoU", _pct), ("f1", "F1 @ 1% bbox", _pct),
                     ("chamfer_valid", "Chamfer (unit box, valid only)", lambda v: _num(v, 4))]
        elif fam == "cadtestbench":
            keys += [("pass_rate", "CADTest pass rate", _pct), ("rs", "requirement satisfaction", _pct),
                     ("pr", "all tests pass", _pct)]
        else:
            keys += [("req_pass", "mean requirement pass", _pct), ("strict", "strict pass", _pct)]
        keys += [("median_agent_seconds", "median agent time (s)", lambda v: _num(v, 0)),
                 ("median_seconds", "median wall time incl. harness (s)", lambda v: _num(v, 0)),
                 ("mean_usd", "mean cost per task (USD, list price)", lambda v: _num(v, 3)),
                 ("timeouts", "time-cap hits", lambda v: str(v)), ("errors", "harness errors", str)]
        for k, name, f in keys:
            lines.append(f"| {name} | {f(sm['mises'].get(k))} | {f(sm['baseline'].get(k))} |")
        for sub in ("by_family", "by_type", "by_category"):
            if sub in sm["mises"]:
                for k in sm["mises"][sub]:
                    lines.append(f"| {sub.split('_')[1]}: {k} | {_pct(sm['mises'][sub].get(k))} "
                                 f"| {_pct(sm['baseline'][sub].get(k))} |")
        if "metrics" in sm["mises"]:
            lines += ["", "| task | Mises | Baseline |", "|---|---|---|"]
            for tid in sm["mises"]["metrics"]:
                fm = lambda d: ", ".join(f"{k} {v}" for k, v in (d or {}).items())  # noqa: E731
                lines.append(f"| {tid} | {fm(sm['mises']['metrics'][tid])} | "
                             f"{fm(sm['baseline']['metrics'].get(tid))} |")
    return lines


def build_report(runs: Path, out: Path, label: str = "", history: dict | None = None,
                 preamble: str = "") -> dict:
    out.mkdir(parents=True, exist_ok=True)
    data = collect(runs)
    summary = {s: summarise(s, rows) for s, rows in data.items() if rows}
    model = None
    for rows in data.values():
        for r in rows.values():
            model = model or r["mises"]["run"].get("model")
    date = dt.date.today().isoformat()
    per_task = {s: {tid: {k: {"headline": headline(s, v[k]["score"]),
                              "seconds": v[k]["run"].get("seconds"),
                              "agent_seconds": v[k]["run"].get("agent_seconds"),
                              "usd": v[k]["run"].get("usd"),
                              "valid": v[k]["score"].get("valid"),
                              "stop": v[k]["run"].get("stop")} for k in SYSTEMS}
                    for tid, v in rows.items()} for s, rows in data.items()}
    doc = {"date": date, "model": model, "label": label, "summary": summary, "tasks": per_task}
    if history:
        doc["history"] = history
    write_json(out / "benchmarks.json", doc)
    held = [s for s in HELD_OUT if s in summary]
    chart(summary, out / "benchmarks.png", model or "?", date, label, only=held or None,
          title="Mises vs the same model with a plain build123d tool (held-out tasks)" if held else None)
    lines = [f"# Mises CAD benchmarks ({date})", "", preamble] if preamble else \
        [f"# Mises CAD benchmarks ({date})", ""]
    lines += [f"Model: `{model}` for both systems, Think: High, one build per task. Invalid or "
              "missing outputs score 0. Only tasks run by both systems are compared (paired).", ""]
    lines += tables(summary)
    (out / "benchmarks.md").write_text("\n".join(lines) + "\n")
    return doc


if __name__ == "__main__":
    import sys
    print(json.dumps(build_report(Path(sys.argv[1]), Path(sys.argv[2]))["summary"], indent=1)[:4000])
