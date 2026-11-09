# BENCH4 comparison plan: CADPrompt (written and committed before any BENCH4 run)

**Question:** on CADPrompt, a public text-to-CAD benchmark with published baselines, does AgentCAD build closer shapes than the same model alone?

**Benchmark:** CADPrompt (Alrashedy et al., "Generating CAD Code with Vision-Language Models for 3D Designs", ICLR 2025, arXiv 2410.05340).
- 200 DeepCAD objects, each with a natural-language prompt, expert CadQuery code and a ground-truth STL.
- Data: github.com/Kamel773/CAD_Code_Generation (main, last push 2025-02-28), downloaded as a tarball into the bench cache, outside the repo. Nothing from it is committed.
- Licence: the repo has no LICENSE file. Its README asks users to cite the paper and states the STLs come from DeepCAD (code MIT, data from Onshape public documents). I treat it as usable for local evaluation with citation, and not for redistribution. No result is submitted anywhere.
- Others report on it: the paper's own GPT-4, Gemini 1.5 Flash and CodeLlama 70B baselines (Table 2). CADTestBench (arXiv 2605.07807) is built on the same 200 prompts.

**Tasks:** CADTestBench's sample ids are the CADPrompt object ids. Ranking the 200 ids with the harness's seed-pinned hash (sha256 of "0:<id>") reproduces exactly the 100 ids run as CADTestBench DEV and TEST in rounds 1 and 2. BENCH4 uses ranks 100 to 199, the other 100 objects. No AgentCAD build has seen them, and nothing is tuned on them.
- Prompt: the "with specific measurements" file, with the "Write Python code using CADQuery to" framing made neutral ("Create ...") and the harness's units line added, exactly as CADTestBench was handled. Both systems get the same text.

**Systems (one attempt per task, effort High, 1,200 s cap):**
- `mises@sonnet`: AgentCAD at branch bench4 (main @ fe7b06ce plus harness-only changes), Claude Code Sonnet 5.5, driven over its HTTP API.
- `baseline@sonnet`: Claude Code Sonnet 5.5 with the plain build123d brief and one sandboxed exec tool.
- Sonnet 5.5 is chosen for cost. The published baselines are GPT-4, Gemini 1.5 Flash and CodeLlama, which cannot be run here (no API keys), so no other model would make the published comparison like-for-like.
- Seeds: task ranking seed 0; point-sampling seeds 0 (prediction) and 1 (ground truth). Model sampling is not seedable through Claude Code; one run per system per task.

**Primary metric:** CADPrompt's point-cloud distance (Eq. 8), as re-implemented in `core/evals/bench/cadprompt_metric.py` from the paper's text: 1,000 surface points per shape, both normalised to a unit cube, ICP of the prediction onto the ground truth from the identity, symmetric mean nearest-neighbour distance. A task with no shape scores sqrt(3), the paper's rule. Lower is better. The authors did not publish their evaluation code, so the sampling, normalisation and ICP settings are my reading of the paper.

**Secondary metrics, reported whichever way they come out:** Hausdorff distance (Eq. 9); IoGT (Eq. 10, as bounding-box overlap over the ground truth's box); compile rate; the median and IQR of each, as the paper reports them; CADBench's aligned volumetric IoU (`cadbench_official.py`, failures 0); valid-solid rate; cost and agent time.

**Primary contrast (paired, per task):** `mises@sonnet` vs `baseline@sonnet` on point-cloud distance. The difference is baseline minus AgentCAD, so positive means AgentCAD is closer.

**Statistics:** 95% paired bootstrap CI (10,000 resamples, seed 0) on the mean difference; two-sided paired sign-flip permutation p (100,000 draws). There is one primary contrast, so there is no multiplicity correction; the secondary contrasts are descriptive. Win/loss/tie counts: a tie is a difference of 0.005 or less in unit-cube units (about the metric's sampling noise). Every loss is listed by task id.

**Decision rule, fixed now:** "AgentCAD better" means p < 0.05 and the CI excludes 0 on the positive side. "AgentCAD worse" is the mirror case. Anything else is "no detectable difference at this sample size", and I will not call it a win.

**Published numbers:** the paper's medians (n = 200, older models, CadQuery, its own unpublished pipeline) are shown next to ours as indicative only. Different tasks (our 100 of their 200), different models and a re-implemented metric mean that table is not a fair head-to-head.

**Pilot and size, decided on cost only:**
- Pilot: the first 8 tasks in rank order, both systems. Pilot tasks stay in the main set, because nothing changes between pilot and main. Pilot outputs are not scored until the main run's size is fixed and committed.
- Budget: the account's weekly usage gauge (`~/Documents/Claude/Projects/Mises-demo/usage.txt`, account-wide, shared with four other tasks) read 79% when this plan was written. The harness will not start a run at 82% or more (`MISES_BENCH_USAGE_STOP=82`), well under the hard rule of 85%. The main size N is the largest of 100, 60, 40, 30 or 20 (rank-order prefixes) whose projected cost from the pilot stays under that stop.
- If the stop is reached during the main run, the analysis uses every task that both systems finished, and the report says how many and which were not run.

**What will not change after seeing results:** the tasks, the metric, the failure rule and the contrast. No task is dropped. A run that fails for infrastructure reasons (a network or usage-limit error, not a model or product failure) may be repeated once and will be listed by task id. A run that hits the cap counts as a failure.
