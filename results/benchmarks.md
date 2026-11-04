# Mises CAD benchmarks (2026-11-04)

Model: `claude-code/sonnet` for both systems, Think: High, one build per task. Invalid or missing outputs score 0. Only tasks run by both systems are compared.

| Suite | n | Metric | Mises | Baseline | Mises better / worse / tie |
|---|---|---|---|---|---|
| CADBench (image→part) | 60 | headline | 71.9% | 62.6% | 42 / 18 / 0 |
| CADTestBench (text→part) | 50 | headline | 93.8% | 92.9% | 3 / 3 / 44 |
| Mises-Functional (engineering) | 20 | headline | 100.0% | 98.8% | 2 / 0 / 18 |

## CADBench (image→part) (n = 60)

| | Mises | Baseline |
|---|---|---|
| valid output | 98.3% | 100.0% |
| volume IoU | 71.9% | 62.6% |
| F1 @ 1% bbox | 70.3% | 56.0% |
| Chamfer (unit box, valid only) | 0.0131 | 0.0178 |
| median wall time (s) | 72 | 12 |
| mean cost per task (USD, list price) | 0.274 | 0.033 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| family: B | 74.1% | 68.7% |
| family: F | 56.0% | 49.2% |
| family: M | 85.7% | 70.0% |

## CADTestBench (text→part) (n = 50)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| CADTest pass rate | 93.8% | 92.9% |
| requirement satisfaction | 92.2% | 91.3% |
| all tests pass | 66.0% | 70.0% |
| median wall time (s) | 58 | 11 |
| mean cost per task (USD, list price) | 0.138 | 0.021 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| type: dimensions_ratios | 90.6% | 89.3% |
| type: geometry_types | 100.0% | 100.0% |
| type: solid_shell_validity | 98.5% | 98.5% |
| type: spatial_arrangement | 91.1% | 88.9% |
| type: topology_checks | 98.1% | 96.2% |
| type: volumetric_checks | 88.0% | 87.2% |

## Mises-Functional (engineering) (n = 20)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| mean requirement pass | 100.0% | 98.8% |
| strict pass | 100.0% | 90.0% |
| median wall time (s) | 81 | 23 |
| mean cost per task (USD, list price) | 0.231 | 0.048 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| category: edit | 100.0% | 100.0% |
| category: fits | 100.0% | 100.0% |
| category: manufacturability | 100.0% | 100.0% |
| category: mechanisms | 100.0% | 93.8% |
| category: structural | 100.0% | 100.0% |

## Before and after the Mises fixes

Two fixes came out of the first full pass (branch `demo-bench`):
1. **Attached images are now checkable** (`8ed341f7`). Before the fix, 8 of 60 image→part builds rendered anything and none compared the model to the input image, because `reference_photo_check` only read saved references. The chat now names each attached image's id, and the tool reads an attached image from the build's own chat.
2. **Split parts are flagged** (`af7ef9cf`). The build summary now warns when a part comes out as more than one solid. CADTestBench 00996962 was a groove cut that split the plate in two, and it had been accepted without comment.

Re-runs: Mises only, on the affected subsets: all 60 CADBench tasks, and the 19 CADTestBench tasks where Mises had failed at least one test. The baseline was not re-run; its first-pass outputs are reused.

| | Mises before | Mises after | Baseline |
|---|---|---|---|
| CADBench volume IoU (n=60) | 63.8% | **71.9%** | 62.6% |
| CADBench F1 @ 1% (n=60) | 59.2% | **70.3%** | 56.0% |
| CADBench paired wins / losses vs baseline | 29 / 31 | 42 / 18 | |
| CADBench valid outputs | 100% | 98.3% (1 invalid) | 100% |
| CADBench mean cost per task | $0.157 | $0.274 | $0.033 |
| CADTestBench pass rate, 19 re-run tasks | 78.6% | 83.7% | 81.2% |
| CADTestBench pass rate, all 50 | 91.9% | 93.8% | 92.9% |
| CADTestBench all-tests-pass, all 50 | 62.0% | 66.0% | **70.0%** |

## Where the baseline wins or ties
- **Cost and time.** Mises costs 4 to 8 times as much per task at list price ($0.14 to $0.27 against $0.02 to $0.05) and takes 3 to 6 times longer (median 58 to 81 s against 11 to 23 s). One single-tool-call build still reads about 237k input tokens, which is the Mises system prompt plus its tool schemas over two turns. This is the largest weakness, and these fixes do not address it. The photo-check loop makes image builds about 75% more expensive.
- **CADTestBench all-tests-pass.** The baseline is ahead, 70% against 66%. In 00670259 the prompt gave sizes in metres and Mises converted them to millimetres (its rules say "every size in mm"), while the shared instruction said to use the numbers exactly as given. The baseline followed the instruction. This conflict in unit handling is left unfixed on purpose: I could not separate a benchmark convention from a real product bug.
- **Mises-Functional is nearly saturated.** Mises scores 20/20 strict and the baseline 18/20. The baseline lost K3 (it put the rack tip on the pitch line, 13.5 instead of 12.0 from the pinion axis) and K4 (it added no hinge stop). Both are mechanisms. The suite is too easy for this model to separate the two systems, so it needs harder tasks.

## Method and caveats
- Both systems: `claude-sonnet-5-5` (Mises' default with a signed-in Claude Code, `claude-code/sonnet`), Think/effort High, one build per prompt, a time cap of 1200 s (1800 s for functional), and no time-cap hits. Mises ran on its own core per prompt (fresh `MISES_HOME`) over HTTP. Its approvals were handled by policy (reversible approved, irreversible rejected); none were requested. The baseline was `claude -p` with no built-in tools and one sandboxed `run_build123d` MCP tool (the same macOS sandbox Mises uses for its CAD worker), capped at 60 turns.
- CADBench: single-view grayscale image→part, 20 tasks each from the M, F and B families. B and F are stratified 7 easy, 7 medium and 6 hard, with picks pinned by seed. Scoring normalises both shapes to a unit bounding box, then aligns them (24 axis rotations plus Procrustes/ICP). IoU uses 96³ ray-parity voxels, F1 uses τ = 1% of the box, and Chamfer is the mean of the two directed mean distances on 30k samples. Invalid shapes score 0. IoU is noisy for thin plates at this grid. Licences: repo MIT, Fusion 360 Gallery is for non-commercial research only, DeepCAD and MCB are MIT. The data was used for internal evaluation only, cached outside the repo and not committed.
- CADTestBench (MIT): 50 prompts from the detailed partition, pinned by seed. The "Write Python code using CADQuery to" framing was replaced by "Create" for both systems, plus one line on units. The official cadtests run on the exported STEP through CadQuery (`importStep`), with the official pass_rate, RS and PR definitions.
- Mises-Functional: 20 tasks (fits, mechanisms swept through motion, CalculiX linear-static load cases, manufacturability, two-message edit regression). Checkers score the fraction of requirements passed. Each checker gives its hand-written reference answer full marks, and known-bad variants fail the intended requirements (`core/evals/bench/test_functional.py`).
- The 19-task CADTestBench re-run subset was chosen because Mises had failed on those tasks, so part of the gain may be regression to the mean. The CADBench re-run covers all 60 tasks.
- Usage: about $44 list-price-equivalent for every pass together. Weekly utilisation went from 0% to 9% over the session, on an account shared with other sessions. The pilot (5 prompts per suite) projected about 15 to 20% for the full set, under the 60% ceiling, so the full set ran.
