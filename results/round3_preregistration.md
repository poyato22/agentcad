# Round 3 comparison plan (written and committed before any round-3 run)

**Question:** on image-to-part CAD, how does AgentCAD compare with the strongest plain models available through Claude Code, and with published systems on the same tasks?

**Tasks:** a new CADBench sample: 60 single-view tasks, 20 from each of the B, F and M families, with B and F split 7 easy, 7 medium and 6 hard. This is the third slice of the same seed-pinned ranking, disjoint from the DEV and TEST sets. No system has run on it, and nobody has looked at these tasks.

**Systems (one attempt per task, Think/effort High, 1200 s cap):**
- `baseline@sonnet`, `baseline@opus`, `baseline@fable`: Claude Code with a plain build123d brief and one sandboxed exec tool.
- `mises@sonnet`, `mises@opus`: AgentCAD at main @ 92d0b09b, driven over its HTTP API.
- `published@opus47`, `published@gemini31`, `published@gpt54`: the programs CADBench itself published for these models. They exist for family B only, so these contrasts cover the 20 B tasks and nothing else.

No other vendors' models were run, because no API keys are configured.

**Primary metric:** CADBench's own aligned volumetric IoU (`cadbench_official.py`, a port of their `perform_evaluation`). I report the mean over all tasks, with failures scored 0.

**Secondary metrics, reported whichever way they come out:**
- the median of that IoU, as the paper reports it;
- surface IoU;
- valid shape rate;
- this harness's earlier IoU (for continuity with rounds 1 and 2);
- per-family and per-tier breakdowns;
- cost and agent time.

**Primary contrasts (paired, per task, 60 tasks):**
1. `mises@sonnet` vs `baseline@sonnet`
2. `mises@sonnet` vs `baseline@opus`
3. `mises@opus` vs `baseline@opus`
4. `mises@opus` vs `baseline@fable`

**Statistics:** each contrast gets a 95% paired bootstrap CI (10,000 resamples, seed 0) on the mean difference, plus a two-sided paired sign-flip permutation p-value (100,000 draws). Holm correction runs across the four contrasts. Win/loss/tie counts are also reported.

**Decision rule, fixed now:**
- "Beats" means the Holm-adjusted p is below 0.05 and the CI excludes 0.
- Anything else is "no detectable difference at this sample size". I will not call that a win.

**What will not change after seeing results:** the tasks, the metric, the rule for failures, and the contrasts. No task is dropped, and nothing is tuned on this set. A run that fails for infrastructure reasons (a network or usage-limit error, not a model or product failure) may be repeated once and will be listed by task id. Any run that hits the cap counts as a failure.

**Stopping rule:** a 5-task pilot of each system projects the weekly usage. If the full round would pass about +12%, the coordinator decides what to cut before it runs.
