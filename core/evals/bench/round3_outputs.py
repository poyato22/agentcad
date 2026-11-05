"""Write the round-3 outputs from the analysis file: the clean JSON for the video's chart, and the
stats copied into results/. Numbers come only from round3_stats.json, never typed by hand.

    python -m evals.bench.round3_outputs STATS.json CTB.json VIDEO.json RESULTS_DIR
"""
import json
import shutil
import sys
from pathlib import Path

LABEL = {"mises@opus": "AgentCAD + Opus 5.5", "baseline@opus": "Opus 5.5 alone",
         "mises@sonnet": "AgentCAD + Sonnet 5.5", "baseline@fable": "Fable 5.1 alone",
         "baseline@sonnet": "Sonnet 5.5 alone"}
CLAIM = ("On 60 CADBench image-to-CAD tasks that no system had seen, AgentCAD on Claude Opus 5.5 "
         "scored the highest of the five systems we ran: {a:.1f}% volumetric IoU on CADBench's own "
         "metric, ahead of Opus 5.5 alone ({b:.1f}%), Fable 5.1 alone ({c:.1f}%) and Sonnet 5.5 "
         "alone ({d:.1f}%).")


def pct(x):
    return round(100 * x, 1)


def main(stats, ctb, video, results):
    d = json.loads(Path(stats).read_text())
    s = d["systems"]
    con = {(c["a"], c["b"]): c for c in d["primary_contrasts"]}
    ao, bo = con[("mises@opus", "baseline@opus")], con[("mises@sonnet", "baseline@opus")]
    ss = con[("mises@sonnet", "baseline@sonnet")]
    out = {
        "title": "Single image to CAD: 60 unseen CADBench tasks",
        "metric": "Volumetric IoU, CADBench's own metric; mean over all 60 tasks; higher is better",
        "n_tasks": d["n_common"],
        "date": "2026-11-05",
        "claim_exact_wording": CLAIM.format(a=pct(s["mises@opus"]["official_iou_mean"]),
                                            b=pct(s["baseline@opus"]["official_iou_mean"]),
                                            c=pct(s["baseline@fable"]["official_iou_mean"]),
                                            d=pct(s["baseline@sonnet"]["official_iou_mean"])),
        "bars": [{"system": LABEL[k], "iou_pct": pct(s[k]["official_iou_mean"]),
                  "ci95_pct": [pct(x) for x in s[k]["official_iou_mean_ci95"]],
                  "median_pct": pct(s[k]["official_iou_median"]),
                  "cost_usd_per_task_list": round(s[k]["mean_usd_list"], 3),
                  "median_seconds": round(s[k]["median_seconds"], 1),
                  "uses_agentcad": k.startswith("mises")} for k in LABEL],
        "paired_differences": [
            {"label": f"{LABEL[c['a']]} vs {LABEL[c['b']]}", "diff_pts": pct(c["mean_diff"]),
             "ci95_pts": [pct(x) for x in c["ci95"]], "wins": c["wins"], "losses": c["losses"],
             "ties": c["ties"], "p_holm": c["p_holm"], "verdict": c["verdict"]}
            for c in d["primary_contrasts"]],
        "footnote_required": (
            f"One attempt per task, effort High, CADBench families B, F, M. AgentCAD + Opus 5.5 vs "
            f"Opus 5.5 alone: +{pct(ao['mean_diff'])} points, 95% CI +{pct(ao['ci95'][0])} to "
            f"+{pct(ao['ci95'][1])}, {ao['wins']} wins, {ao['losses']} losses, {ao['ties']} tie. "
            f"Systems given a clean 3D mesh instead of a picture score higher on CADBench."),
        "do_not_say": [
            "'the best possible' or 'the best CAD AI': no submission to CADGenBench, the public "
            "leaderboard; and mesh-input systems score higher on CADBench",
            f"'beats Opus even on Sonnet': false, AgentCAD + Sonnet is {pct(bo['mean_diff'])} "
            f"points against Opus alone ({bo['wins']} wins, {bo['losses']} losses)",
            "'86.9% accurate': it is mean overlap with the true shape, on this metric and these "
            "60 tasks",
        ],
        "chart_note": ("The per-system error bars overlap for AgentCAD + Opus and Opus alone, but "
                       "the tasks are paired, so the paired difference is the correct test. If "
                       "error bars are drawn, show the paired difference with them."),
        "also_true": {
            "lift_on_sonnet": (f"AgentCAD lifts Sonnet 5.5 by +{pct(ss['mean_diff'])} points "
                               f"({pct(ss['a_mean'])}% vs {pct(ss['b_mean'])}%; 95% CI "
                               f"+{pct(ss['ci95'][0])} to +{pct(ss['ci95'][1])}; {ss['wins']} wins, "
                               f"{ss['losses']} losses, {ss['ties']} tie)"),
            "cost": (f"AgentCAD + Opus costs ${s['mises@opus']['mean_usd_list']:.2f} a task at list "
                     f"price against ${s['baseline@opus']['mean_usd_list']:.2f} for Opus alone"),
        },
        "source": "results/round3_stats.json, plan fixed in results/round3_preregistration.md",
    }
    Path(video).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    res = Path(results)
    shutil.copy(stats, res / "round3_stats.json")
    shutil.copy(ctb, res / "round3_cadtestbench_published.json")
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:5])
