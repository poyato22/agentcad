"""Write results/benchmarks.{md,json,png}: held-out TEST first, DEV after, plus the iteration
history (each DEV round's summary) so nothing is overwritten.

    cd core && uv run --with matplotlib python -m evals.bench.final_report RUNS_ROOT ../results
RUNS_ROOT holds main/ (round 0), final/ (round 1), dev2/ (round 2 DEV) and report/ (the
assembled set: DEV from final/ + dev2/physics_dev, TEST from test/).
"""
from __future__ import annotations

import sys
from pathlib import Path

from .report import _pct, build_report, collect, summarise

PREAMBLE = """\
**Headline (held-out TEST, never looked at while tuning AgentCAD):** AgentCAD leads on image→part (CADBench), \
is level-to-slightly-ahead on text→part (CADTestBench) and on the physics-graded suite, at 4–8× the cost \
per task and 1.5–2.5× the agent time. Baseline = the same Claude Code model (Sonnet 5.5, Think High) with \
a plain build123d prompt and one sandboxed exec tool; one attempt per task for both.

How to read: *n* tasks, paired; *AgentCAD − baseline* is the mean per-task difference with a 95% paired \
bootstrap CI; win/loss counts are per task. Splits: DEV sets were used to find and test AgentCAD fixes; \
TEST sets were drawn after (next items of the same seed-pinned rankings, disjoint from DEV) and run once \
with the final AgentCAD. AgentCAD-Physics (20 tasks) was written before any tuning, split 5 DEV / 15 TEST. \
AgentCAD-Functional (20) was never tuned on; its AgentCAD runs are from the first pass (pre-fix AgentCAD).
"""

HISTORY_NOTES = {
    "round0_first_pass": "main @ 4bf7af07 + harness; all DEV suites, both systems",
    "round1_after_2_fixes": "chat-attached images checkable by reference_photo_check; multi-solid "
                            "warning. AgentCAD re-run on all 60 CADBench + the 19 CADTestBench it failed",
    "round2_dev": "units rule + unit-slip guard, schema titles dropped, zero-solid warning, "
                  "photo mismatch hints (reverted: no gain), designer verify-every-requirement "
                  "(reverted: no gain). AgentCAD re-run on DEV CADBench 60 + CADTestBench 50",
}


def _summ(runs: Path, suites: list[str]) -> dict:
    d = collect(runs)
    return {s: summarise(s, d[s]) for s in suites if d.get(s)}


def _slim(sm: dict) -> dict:
    return {s: {"n": v["n"], "mises": round(v["mises"]["headline"], 4),
                "baseline": round(v["baseline"]["headline"], 4), "paired": v["paired"],
                "mises_cost": v["mises"]["mean_usd"]} for s, v in sm.items()}


def main(root: Path, out: Path) -> None:
    hist = {
        "round0_first_pass": _slim(_summ(root / "main", ["cadbench", "cadtestbench", "functional"])),
        "round1_after_2_fixes": _slim(_summ(root / "final", ["cadbench", "cadtestbench", "functional"])),
        "round2_dev": _slim(_summ(root / "dev2", ["cadbench", "cadtestbench", "physics_dev"])),
        "notes": HISTORY_NOTES,
    }
    doc = build_report(root / "report", out, label="Claude Sonnet 5.5 · held-out tasks",
                       history=hist, preamble=PREAMBLE)
    lines = (out / "benchmarks.md").read_text().splitlines()
    lines += ["", "## DEV iteration history (AgentCAD headline / baseline headline, paired wins-losses)",
              "", "| Round | What changed | CADBench IoU | CADTestBench pass | CADTestBench all-pass wins | Physics/Functional |",
              "|---|---|---|---|---|---|"]
    for key in ("round0_first_pass", "round1_after_2_fixes", "round2_dev"):
        h = hist[key]
        cb, ct = h.get("cadbench"), h.get("cadtestbench")
        other = h.get("functional") or h.get("physics_dev")
        f = lambda x: (f"{_pct(x['mises'])} / {_pct(x['baseline'])} "  # noqa: E731
                       f"({x['paired']['mises_better']}-{x['paired']['baseline_better']})") if x else "–"
        strict = (f"{ct['paired'].get('strict_mises_only', '–')} AgentCAD-only / "
                  f"{ct['paired'].get('strict_baseline_only', '–')} baseline-only") if ct else "–"
        lines.append(f"| {key} | {HISTORY_NOTES[key]} | {f(cb)} | {f(ct)} | {strict} | {f(other)} |")
    (out / "benchmarks.md").write_text("\n".join(lines) + "\n")
    print("wrote", out, list(doc["summary"]))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
