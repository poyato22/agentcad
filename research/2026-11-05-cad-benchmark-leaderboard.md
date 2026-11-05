# CAD benchmark leaderboard: published scores and how AgentCAD compares (2026-11-05)

Scope: the best published scores on CADBench, CADTestBench and related suites, with metric, n and model, and which comparisons with AgentCAD are fair.

Sourcing: I read the arXiv papers' full HTML, and the CADBench and CADTestBench evaluation code, published programs and metric docs. CADGenBench and CAD-Bench Lab numbers come from the pages cited, not from a paper. Where I could not verify something, the text says so.

## Conclusion: the strongest true claim

**Exact wording, supported by the data:**

> On 60 CADBench image-to-CAD tasks that no system had seen, AgentCAD on Claude Opus 5.5 scored the highest of the five systems we ran: 86.9% volumetric IoU on CADBench's own metric, ahead of Opus 5.5 alone (83.6%), Fable 5.1 alone (70.3%) and Sonnet 5.5 alone (60.4%).

- **The lead over Opus 5.5 alone is small but real:** +3.3 points (95% CI +1.6 to +5.1, Holm-adjusted p = 0.00015; 40 wins, 19 losses, 1 tie). The per-system confidence intervals overlap, so the paired test is what carries this.
- **"Best possible" is not earned.** Three things stand in the way:
  - Systems given a clean 3D mesh instead of a picture score higher on CADBench (CADFit, 0.859 aggregate).
  - AgentCAD has not been submitted to CADGenBench, the public leaderboard, whose top row (86.9%, a different score) is also a tooled Opus 5.5.
  - On text-to-part, AgentCAD is level with the plain model, not clearly ahead.
- **"Beats Opus even on the cheaper Sonnet" is false.** AgentCAD on Sonnet 5.5 (74.1%) loses to plain Opus 5.5 (83.6%) by 9.5 points (10 wins, 50 losses), and costs more: $0.26 against $0.16 a task.
- **AgentCAD's largest lift is on Sonnet:** +13.7 points over plain Sonnet 5.5 (CI +9.4 to +18.3; 46 wins, 13 losses, 1 tie). This is the second replication of that lift. The earlier held-out set gave +8.5 points on the previous harness metric, where this set gives +14.9.

**Measured results, n = 60 (the plan was committed before any run: `results/round3_preregistration.md`):**

| System | Mean IoU (95% CI) | Median IoU | B / F / M mean | Cost per task (list) | Median time |
|---|---|---|---|---|---|
| **AgentCAD + Opus 5.5** | **86.9%** (81.3–91.9) | 96.5% | 81.0 / 82.1 / 97.5 | $0.58 | 68 s |
| Opus 5.5 alone | 83.6% (77.8–89.0) | 93.3% | 76.7 / 78.1 / 96.0 | $0.16 | 40 s |
| AgentCAD + Sonnet 5.5 | 74.1% (68.3–79.8) | 81.2% | 72.3 / 64.9 / 85.2 | $0.26 | 34 s |
| Fable 5.1 alone | 70.3% (64.3–76.0) | 77.1% | 72.1 / 57.7 / 81.1 | $0.19 | 16 s |
| Sonnet 5.5 alone | 60.4% (53.8–66.8) | 67.0% | 59.3 / 50.4 / 71.4 | $0.04 | 13 s |

No system had a failure, a timeout or an invalid shape. No other vendor's models were run, because no API keys are configured.

**Same 20 family-B tasks against the programs CADBench published (mean official IoU, failures as 0):**

| System | Mean IoU | Valid shapes |
|---|---|---|
| AgentCAD + Opus 5.5 | 81.0% | 100% |
| Opus 5.5 alone | 76.7% | 100% |
| Published Gemini 3.1 Pro | 51.4% | 85% |
| Published GPT-5.4 (13 tasks with an output) | 39.7% | 77% |
| Published Opus 4.7 | 35.4% | 70% |

- AgentCAD + Opus beats published Opus 4.7 by +45.7 points (CI +30.8 to +59.6; 19 wins, 1 loss).
- **Most of that gap is the model generation and iterating, not AgentCAD:** plain Opus 5.5 with an exec tool is already +41.4 points. The published runs made one call with no feedback.
- A few published failures may depend on the CadQuery version (we ran 2.8).

**Limits of the main result:**
- 60 tasks from 3 of CADBench's 6 families, single-view only. Families E, A and O were not run, so 86.9% cannot be set against the 0.412 aggregate published for Opus 4.7.
- One run per system per task. Model behaviour varies between runs.
- The metric is a port of CADBench's code, not their pipeline. Across the 300 runs it needed no fallback, and it agrees closely with this harness's separate IoU.
- Both systems see the same prompt and image, but AgentCAD's advantage comes with 3.6× the cost on Opus.

## 1. Which comparisons are fair

| Comparison | Same tasks? | Same metric? | Same input? | Fair? |
|---|---|---|---|---|
| AgentCAD vs plain Sonnet 5.5, Opus 5.5, Fable 5.1 on our new CADBench slice | Yes (60) | Yes (CADBench's own, ported) | Yes | **Yes: this is the main result** |
| AgentCAD vs the programs CADBench published for Opus 4.7, Gemini 3.1 Pro, GPT-5.4 | Yes, family B only (20) | Yes | Yes (single view) | **Yes, on those 20 tasks** |
| AgentCAD vs the programs CADTestBench published for ReAct GPT-5.2 and Claude 4.6 Sonnet | Yes (our 50) | Yes (same adapter) | Prompt reworded for us (see §3) | **Mostly** |
| AgentCAD's mean or median vs CADBench's published table | No: 60 tasks against 3,000 per family, and 3 of the 6 families | Yes if the medians use the official metric | Yes | Indicative only |
| AgentCAD vs mesh-to-CAD systems (CADFit 0.859) | Different | Same metric | **No: they get a clean 3D mesh** | **No** |
| AgentCAD vs CADGenBench's leaderboard (top 86.9%) | No: never submitted | No: a composite score | No: drawing plus text | **No. Only a submission settles it** |
| AgentCAD vs Zoo or Adam | No | No | No | **No: no published scores on these benchmarks** |

## 2. CADBench (image or mesh to CAD program)

[arXiv 2605.10873](https://arxiv.org/abs/2605.10873), [code](https://github.com/anniedoris/CADBench). 18,000 samples in 6 families (3,000 each), 5 input modalities.

**Metric as implemented in their code:**
- CGAL alpha wrap of both meshes.
- Both normalised to [-1, 1] on the longest side.
- Inertia-based alignment over 4 principal-axis pairings, then exact boolean IoU.
- Failed programs count as 0.
- Paper tables report the median per split. Their aggregate column is the mean across splits (not verified beyond the table).

**Aggregate IoU, ideal inputs (Table 2):**

| System | Input | IoU | Surface IoU | Chamfer | Valid shape rate |
|---|---|---|---|---|---|
| CADFit | clean mesh | **0.859** | 0.679 | 0.038 | 0.981 |
| CADEvolve | clean mesh | 0.707 | 0.498 | 0.062 | 0.967 |
| Cadrille | clean mesh | 0.687 | 0.517 | 0.056 | 0.939 |
| CAD-Recode | clean mesh | 0.673 | 0.503 | 0.056 | 0.912 |
| **Claude Opus 4.7** | single-view image | **0.412** | 0.108 | 0.101 | 0.798 |
| Gemini 3.1 Pro | single-view image | 0.382 | 0.106 | 0.087 | 0.729 |
| CAD-Coder | single-view image | 0.354 | 0.115 | 0.157 | 0.950 |
| Kimi K2.6 | single-view image | 0.224 | 0.042 | 0.111 | 0.616 |
| GPT-5.4 | single-view image | 0.158 | 0.029 | 0.109 | 0.513 |

**Per split, median IoU on the families we also ran (Table 5):**

| System | B low | B med | B high | F low | F med | F high | M |
|---|---|---|---|---|---|---|---|
| CADFit (mesh) | 0.986 | 0.966 | 0.919 | 0.983 | 0.955 | 0.868 | 0.915 |
| Claude Opus 4.7 | 0.642 | 0.547 | 0.444 | 0.582 | 0.432 | 0.318 | 0.505 |
| Gemini 3.1 Pro | 0.705 | 0.588 | 0.476 | 0.637 | 0.473 | 0.252 | 0.493 |
| CAD-Coder | 0.664 | 0.479 | 0.351 | 0.563 | 0.387 | 0.215 | 0.433 |
| GPT-5.4 | 0.618 | 0.474 | 0.307 | 0.493 | 0.235 | 0.000 | 0.000 |

Other limits on the table comparison:
- Their aggregate includes families E, A and O, which are harder. It cannot be compared with a B/F/M-only score.
- Their models made one call and got no execution feedback (not verified for every system).
- The paper reports that specialised mesh-to-CAD models substantially outperform code-generating vision-language models under idealised inputs.

## 3. CADTestBench (text to CAD, executable tests)

[arXiv 2605.07807](https://arxiv.org/abs/2605.07807), [code](https://github.com/dimitrismallis/CADTestBench). 200 prompts, detailed partition with 3,037 tests.

- **RS:** the share of requirement groups fully passing.
- **PR:** the share of samples with every test passing.
- **IR:** the invalid ratio.
- Invalid outputs count as failures.

**Published, detailed partition, n=200 (Table 3):**

| Method | Planner | IR | RS | PR |
|---|---|---|---|---|
| **CADTests + Log** (tests used as feedback) | Claude 4.6 Sonnet | 0 | **0.897** | **0.625** |
| CADTests (tests used as feedback) | Claude 4.6 Sonnet | 0.005 | 0.882 | 0.590 |
| ReAct | Claude 4.6 Sonnet | 0 | 0.874 | 0.580 |
| CADTests + Log | GPT-5.2 | 0.015 | 0.852 | 0.515 |
| ReAct | GPT-5.2 | 0 | 0.835 | 0.480 |
| 10-shots | Claude 4.6 Sonnet | 0.155 | 0.744 | 0.510 |
| CadCodeVerify | GPT-4.1 | 0.025 | 0.794 | 0.410 |
| Text2CAD (finetuned) | n/a | 0.005 | 0.405 | 0.025 |

The CADTests baselines read the benchmark's own tests while generating. Neither AgentCAD nor our baseline does, so those two rows are a stronger setup rather than a like-for-like method.

**Same 50 tasks, same adapter (zero model cost).** Run on our held-out sample, using the programs the authors ship. Their output paths name them as the ReAct runs.

| System | Tests passed | RS | PR |
|---|---|---|---|
| AgentCAD, Sonnet 5.5 (round 2) | 94.2% | 0.932 | 0.72 |
| Plain Sonnet 5.5 baseline (round 2) | 93.2% | 0.927 | 0.70 |
| Published ReAct, Claude 4.6 Sonnet | 85.7% | 0.848 | 0.58 |
| Published ReAct, GPT-5.2 | 84.8% | 0.828 | 0.48 |

- On this subset the published PRs match the paper (0.58, 0.48), and RS is close to it (0.848 vs 0.874, 0.828 vs 0.835). That is evidence the adapter measures what theirs does.
- AgentCAD beats published ReAct Claude 4.6 Sonnet by +8.5 pts on tests passed (CI +3.0 to +15.5; 12 wins, 1 loss), and published ReAct GPT-5.2 by +9.4 pts (CI +4.7 to +14.6; 20 wins, 1 loss).
- **Most of that gain is the newer model.** Plain Sonnet 5.5 alone is +7.5 pts over ReAct Claude 4.6 Sonnet.
- AgentCAD over the Sonnet 5.5 baseline is only +1.0 pt (CI 0.0 to +2.6), which is not a clear win.
- Our prompts were reworded for both of our systems ("Create ..." in place of "Write Python code using CADQuery to create ...", plus one units line). The published runs saw the original wording and wrote CadQuery; ours wrote build123d and went through STEP. Three published Claude programs failed to run under our CadQuery 2.8 environment and count as failures; a version mismatch could explain some of those.
- The benchmark also contains label quirks. For example, some tests read "hexagon side length 1.5" as a 1.5 circumscribed diameter (CadQuery's `polygon()`). These cap all systems.

## 4. Related suites (nothing here was run on AgentCAD)

**CADGenBench** ([repo](https://github.com/huggingface/cadgenbench), [metrics](https://raw.githubusercontent.com/huggingface/cadgenbench/main/docs/metrics.md), [mirror of the board](https://benchlm.ai/md/benchmarks/cadgenbenchgeneration.md), 28 Oct 2026). Image plus text to STEP, 49 generation fixtures, private ground truth.
- Score: `0.4·shape + 0.4·interface + 0.2·topology`, or 0 if invalid. Shape is the mean of F1 and IoU. It is not a volume IoU.
- Top rows:
  - build123d-mcp + Claude Opus 5.5 (max effort): **86.9%**
  - Godela: 84.5%
  - build123d-mcp + Opus 5 xhigh: 59.6%
  - GPT-5.6 Sol chain: 45.1%
  - Archie in Forge: 42.3%
  - Claude Fable 5: 37.3%
  - Claude Opus 4.8: 27.4%
- Rows are self-submitted. The research note of 2026-11-02 found the top row's method undocumented in its repo.
- **This is the public leaderboard most likely to contradict any "best" claim, because the best row is a well-tooled Opus 5.5, the same class of system as AgentCAD on Opus.** AgentCAD has not been submitted. Only a submission can place it, and that is the user's decision, because the upload is public.

**Text2CAD-Bench** ([arXiv 2605.18430](https://arxiv.org/abs/2605.18430)). 600 text to CadQuery tasks in L1 to L4, one-shot and few-shot. CD ×10³ on 30k points in a unit box, IoU on 256³ voxels. Zero-shot IoU, L1/L2/L3:

| Model | L1 | L2 | L3 |
|---|---|---|---|
| GPT-5.2 | 0.59 | 0.50 | 0.23 |
| Claude 4.5 Sonnet | 0.54 | 0.48 | 0.25 |

Text2CAD, CADFusion and Text2CADQuery all sit at 0.08 or below. No dataset URL was available when last checked.

**BenchCAD** ([arXiv 2605.10865](https://arxiv.org/abs/2605.10865)). Vision2Code IoU (Table 10):

| Model | IoU |
|---|---|
| CADEvolve v3 | 0.750 |
| OpenAI o3 | 0.500 (only 5.6% of its programs execute) |
| Gemini 3.1 Pro (thinking) | 0.279 |
| Claude Opus 4.7 | 0.274 |
| Claude Sonnet 4.6 (thinking-high) | 0.242 |

The task is mechanical part families, on a different metric and task set from CADBench.

**MUSE** ([arXiv 2605.28579](https://arxiv.org/abs/2605.28579)). Multi-part assemblies from text.

| Model | Joint success |
|---|---|
| GPT-6 Astra | 57.55% (Sandbox 99.06, Geometry valid 89.62, Node F1 89.77, Functional 86.79) |
| GPT-5.5 | 34.91% |
| Claude Opus 4.7 | 23.58% |
| Gemini 3.1 Pro | 22.64% |

The closest public test of AgentCAD's assembly claim. Not run.

**Hephaestus-CCX** ([arXiv 2605.17448](https://arxiv.org/abs/2605.17448)). Engineering briefs checked with FEA, 20 single-part and 30 multi-part.
- First attempts: 0 strict passes in 400.
- With one FEA-feedback retry, mean requirement pass:

| Model | Single-part | Multi-part |
|---|---|---|
| GPT-5.5 high | 28.2 → 38.1% | 8.3 → 39.2% (1 of 30 strict) |
| Opus 4.7 xhigh | 32.7 → 31.4% | 6.6 → 28.6% |

Harder than our Mises-Physics suite. No public checkers found so far.

**CADPrompt / CADCodeVerify** ([arXiv 2410.05340](https://arxiv.org/abs/2410.05340)). 200 prompts, medians with IQR, older models. CADCodeVerify with GPT-4 few-shot: IoGT 0.944, point-cloud distance 0.127, compile rate 96.5%. IoGT is an overlap of bounding boxes and rewards a big enough box, so it cannot be compared with IoU.

**CAD-Coder** ([arXiv 2505.19713](https://arxiv.org/abs/2505.19713)). Text to CadQuery on the Text2CAD test set. Mean CD ×10³ 6.54, median 0.17, IR 1.45%; Text2CAD 29.29 / 0.37 / 3.75; GPT-4o 133.52 / 45.91 / 93.00. Those are finetuned models on sequence data, and the task set differs from all of the above. It also appears in the CADBench table at 0.354.

**Text2CAD** ([arXiv 2409.17106](https://arxiv.org/abs/2409.17106)). Median CD 0.37, mean CD 26.41, IR 0.93%.

**CAD-Recode** ([arXiv 2412.14042](https://arxiv.org/abs/2412.14042)). Point cloud to CadQuery, IoU 92.0 on DeepCAD with a 1M training set. Different input.

**Zoo and Adam.**
- I found no score for Zoo or Adam on any benchmark above.
- The only numbers are from [CAD-Bench Lab](https://evals-for-ai-cads.vercel.app/): Zoo Text-to-CAD 71.8, Adam 64.4, Claude Opus 4.7 → CadQuery 62.1, human 86.0.
- That page says "live (9 runs · 1 agents × 9 tasks) · synthetic baseline for the rest", so **these are not measurements and should not be cited.**
- [Zoo's docs](https://docs.zoo.dev/research/zookeeper) publish no benchmark numbers that I could find.
