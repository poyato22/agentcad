# BENCH4: AgentCAD on CADPrompt, a public text-to-CAD benchmark (2026-11-09)

**Result: on 60 CADPrompt tasks, AgentCAD + Sonnet 5.5 and Sonnet 5.5 alone are level.** The pre-registered primary test found no detectable difference: point-cloud distance improved by +0.0015 (95% CI -0.0034 to +0.0075, p = 0.63; 3 wins, 1 loss, 56 ties). AgentCAD cost 6.3x more per task ($0.132 vs $0.021) and took about 2x the agent time.

The plan was committed before any run (`results/bench4_preregistration.md`, commits 9686c7bb and 3ff4c5a2). Numbers are in `results/bench4_cadprompt_stats.json`.

## Benchmark and why

- **CADPrompt** (Alrashedy et al., ICLR 2025, [arXiv 2410.05340](https://arxiv.org/abs/2410.05340), data at github.com/Kamel773/CAD_Code_Generation). It has 200 DeepCAD objects, each with a text prompt, expert CadQuery code and a ground-truth STL.
- **Published baselines** cover GPT-4, Gemini 1.5 Flash and CodeLlama 70B (Table 2). CADTestBench is built on the same 200 prompts.
- **The data is freely downloadable.** The repo has no LICENSE file. Its README asks for a citation and says the shapes come from DeepCAD. It is used here for local evaluation only, nothing from it is committed, and nothing was submitted anywhere.
- **Alternatives considered:**
  - Text2CAD-Bench: no dataset URL yet.
  - BenchCAD: no data link.
  - CADGenBench: private ground truth, so it can only be scored by public submission, which is the user's decision.
  - Text-to-CadQuery: no dataset link and no general-LLM baselines.
- **The tasks are fresh.** These are the 100 CADPrompt objects that rounds 1 and 2 did not use as CADTestBench tasks: ranks 100 to 199 of the same seed-0 ranking, which reproduces those 100 ids exactly. This run covers the first 60 of them. No AgentCAD build had seen them.

## Results (n = 60, paired, one attempt each, Sonnet 5.5, effort High)

| | AgentCAD + Sonnet 5.5 | Sonnet 5.5 alone |
|---|---|---|
| Point-cloud distance, mean (95% CI) | 0.0404 (0.0309 to 0.0521) | 0.0418 (0.0324 to 0.0532) |
| Point-cloud distance, median (IQR) | 0.026 (0.018) | 0.027 (0.024) |
| Hausdorff, median (IQR) | 0.085 (0.119) | 0.085 (0.156) |
| IoGT, median (IQR) | 0.992 (0.076) | 0.992 (0.183) |
| CADBench aligned volume IoU, mean | 79.3% | 79.5% |
| Compile rate / valid solid | 100% / 100% | 100% / 100% |
| Cost per task (list) | $0.132 | $0.021 |
| Median agent time | 25 s | 12 s |
| Timeouts / harness errors | 0 / 0 | 0 / 0 |

| Contrast (positive = AgentCAD better) | Mean difference (95% CI) | p | Wins / losses / ties | Verdict |
|---|---|---|---|---|
| **Point-cloud distance (primary)** | +0.0015 (-0.0034 to +0.0075) | 0.63 | 3 / 1 / 56 | no detectable difference |
| CADBench volume IoU | -0.1 pts (-2.3 to +2.3) | 0.93 | 4 / 11 / 45 | no detectable difference |
| Hausdorff distance | +0.010 (-0.014 to +0.045) | 0.70 | 9 / 9 / 42 | no detectable difference |
| IoGT | +0.018 (-0.010 to +0.056) | 0.38 | 15 / 13 / 32 | no detectable difference |

Ties: a difference of 0.005 or less for the distance metrics, 0.001 for IoU and IoGT.

**Losses, listed:**
- On the primary metric, AgentCAD lost on 00670105: distance 0.204 vs 0.126, volume IoU 0.48 vs 0.85. That prompt describes a "pentagonal prism" with a rectangular lower part, and the two systems read it differently.
- On volume IoU, AgentCAD lost 11 tasks. The largest were 00670105 (-37.7 pts), 00998356 (-9.2), 00998088 (-8.6), 00997229 (-7.3) and 00995733 (-6.9). The rest were under 6 pts each.
- Its wins were 00996329 (+48.1 pts), 00982481 (+28.6) and two of 0.6 pts or less.

## Next to the published table (indicative only, not a fair head-to-head)

| System (paper, n = 200, CadQuery) | IoGT | Point-cloud dist. | Hausdorff | Compile |
|---|---|---|---|---|
| GPT-4 few-shot + CADCodeVerify (best published, not using ground truth) | 0.944 | 0.127 | 0.419 | 96.5% |
| GPT-4 zero-shot, generated | 0.935 | 0.153 | 0.484 | 92.0% |
| Gemini 1.5 Flash few-shot + CADCodeVerify | 0.939 | 0.147 | 0.492 | 85.0% |
| *Ours, n = 60: AgentCAD + Sonnet 5.5* | *0.992* | *0.026* | *0.085* | *100%* |
| *Ours, n = 60: Sonnet 5.5 alone* | *0.992* | *0.027* | *0.085* | *100%* |

Do not read this as "beats GPT-4 by 5x", for four reasons:
- The authors' evaluation code is not public. The metric here is re-implemented from the paper's text: 1,000 points, unit cube, ICP from the identity. A different sampling or ICP setup alone could shift the distances by a lot. The ground truth scored against itself gives about 0.02 here, which is the noise floor.
- The task sets differ: 60 of their 200 objects.
- The models differ, and are two years apart.
- Our prompts drop the "Write Python code using CADQuery" framing, and both of our systems write build123d.

## What this means for AgentCAD's evidence

- CADPrompt is at ceiling for Sonnet 5.5. Every output compiled, and the median distance (0.026) is near the metric's own noise floor (about 0.02). Most tasks are simple extrusions with all dimensions given, so there is little left for AgentCAD's checking loop to fix.
- This agrees with the earlier text-to-part result on CADTestBench (the same 200 prompts, the other 100 objects): +1.0 pt, CI 0.0 to +2.6.
- AgentCAD's measured lift is on image-to-part (CADBench, round 3), not on text-to-part with explicit dimensions.
- **Supported claim:** "On 60 fresh CADPrompt tasks, AgentCAD + Sonnet 5.5 matched Sonnet 5.5 alone (no detectable difference on any metric), with every output valid, at 6.3x the cost."
- No "better" or "best" claim is supported by this run.

## Limits

- n = 60 of the 100 fresh tasks, sized by the usage budget. One run per system per task.
- The CADPrompt metric is a re-implementation, as described above. The secondary CADBench IoU is a port of CADBench's code.
- The data has no licence file, so it is used for local evaluation only.
- Run cost: $9.19 at list price for 120 runs. The account-wide usage gauge moved from 79% to 80% over the pilot and main run, shared with other tasks.

## Reproduce

```
cd core
export MISES_BENCH_CACHE=<dir outside the repo>
export MISES_BENCH_USAGE_FILE=~/Documents/Claude/Projects/Mises-demo/usage.txt MISES_BENCH_USAGE_STOP=82
.venv/bin/python -m evals.bench.cli run --suite cadprompt --system mises@sonnet,baseline@sonnet --limit 60 --parallel 2 --runs RUNS
.venv/bin/python -m evals.bench.cli score --suite cadprompt --parallel 2 --runs RUNS
.venv/bin/python -m evals.bench.bench4_analysis RUNS ../results/bench4_cadprompt_stats.json
```
