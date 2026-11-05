"""Put the round-3 section at the top of results/benchmarks.md and add round 3 to benchmarks.json,
keeping every earlier round. Run from the repo root."""
import json

SEC = """
## Latest: round 3 (2026-11-05), the strongest fair comparison

60 new CADBench image-to-CAD tasks that no system had run, five systems, one attempt each, effort High, on CADBench's own metric. The plan was committed before any run (`results/round3_preregistration.md`). Numbers are in `results/round3_stats.json`.

| System | Mean IoU (95% CI) | Cost per task (list) |
|---|---|---|
| **AgentCAD + Opus 5.5** | **86.9%** (81.3–91.9) | $0.58 |
| Opus 5.5 alone | 83.6% (77.8–89.0) | $0.16 |
| AgentCAD + Sonnet 5.5 | 74.1% (68.3–79.8) | $0.26 |
| Fable 5.1 alone | 70.3% (64.3–76.0) | $0.19 |
| Sonnet 5.5 alone | 60.4% (53.8–66.8) | $0.04 |

| Contrast | Difference (95% CI) | Wins / losses / ties | Result |
|---|---|---|---|
| AgentCAD + Sonnet vs Sonnet alone | +13.7 pts (+9.4 to +18.3) | 46 / 13 / 1 | beats |
| AgentCAD + Sonnet vs Opus alone | -9.5 pts (-13.7 to -5.3) | 10 / 50 / 0 | **loses** |
| AgentCAD + Opus vs Opus alone | +3.3 pts (+1.6 to +5.1) | 40 / 19 / 1 | beats |
| AgentCAD + Opus vs Fable alone | +16.6 pts (+11.5 to +21.7) | 53 / 7 / 0 | beats |

The published scores, which comparisons are fair and the limits are in `research/2026-11-05-cad-benchmark-leaderboard.md`. The sections below and `benchmarks.png` are from rounds 0 to 2, on Sonnet 5.5 and the earlier harness metric.
"""

p = "results/benchmarks.md"
s = open(p).read()
head, rest = s.split("\n", 1)
open(p, "w").write(head + "\n" + SEC + "\n" + rest)

p = "results/benchmarks.json"
d = json.load(open(p))
st = json.load(open("results/round3_stats.json"))
keys = ("n", "official_iou_mean", "official_iou_mean_ci95", "official_iou_median",
        "mean_usd_list", "median_seconds")
d["round3"] = {"plan": "results/round3_preregistration.md", "date": "2026-11-05",
               "systems": {k: {x: v[x] for x in keys} for k, v in st["systems"].items()},
               "primary_contrasts": st["primary_contrasts"],
               "full_stats": "results/round3_stats.json",
               "cadtestbench_published_same_50": "results/round3_cadtestbench_published.json"}
json.dump(d, open(p, "w"), indent=1)
