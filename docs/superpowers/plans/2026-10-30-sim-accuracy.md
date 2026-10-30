# Simulation Accuracy Plan (Plan 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Make Mises simulations trustworthy enough to replace more hand engineering, within honest limits. This plan adds four things:
- nonlinear and contact stress through the CalculiX already installed;
- a real airflow solver (OpenFOAM) beside the built-in low-fidelity one;
- material calibration from the user's own test coupons and load tests;
- fatigue estimates and print-direction-aware (anisotropic) materials.

It also fixes the two loose ends from Plan 5:
- the approval card's `r` key in WebKit;
- stress-histogram scaling at tiny values.

**Base:** `main` at d7a4927.

**Spec:** Plan 5's Global Constraints still bind, including these:
- the Gate on every call;
- never auto-approve;
- untrusted text is data;
- the sandbox: deny-fork, deny reads of the Mises home, safe_io;
- "guide only" and the fidelity/error-band labels;
- GPL/LGPL solvers run only as separate processes;
- external tools are optional, detected by the system check, and installed only by a user's click.

## Global Constraints (additions)
- **Validated accuracy:** every new solver mode ships with an analytic or published benchmark test and a recorded validated error band. That band is shown on the result exactly like CFD's `model_error_band`.
- **Calibration is per material and per printer profile:** fitted parameters keep their source (the coupon tests) and a confidence. A calibrated material is labelled "calibrated (n tests)". An uncalibrated one stays "datasheet / estimate".
- **OpenFOAM is optional:** it runs through Docker or OrbStack if present; the system check detects it. If it's missing, the built-in solver is used and labelled. Never require Docker.

## Tasks
### S1: Nonlinear and contact stress (core)
Use CalculiX `*STATIC` with NLGEOM, `*PLASTIC` (bilinear: yield plus tangent modulus, from material data), and `*CONTACT PAIR` (surface-to-surface, friction μ) between assembly instances or a part and a rigid support.
- **Settings:** add these in the existing `/sims/types` schema as a "nonlinear" option on static: large deflection on/off, plasticity on/off, contact pairs picked by face ids or between assembly instances, load steps/increments.
- **Results:** add plastic strain and contact pressure channels; extend the probe/section accordingly.
- **Benchmarks:** an elastic-plastic cantilever tip load against the analytic plastic hinge load; Hertz contact (sphere on flat) peak pressure within 15%; large-deflection cantilever against elastica within 5%.
- **Assembly studies:** support `project_id` targets, which C7 refuses today. Export the assembly with solved transforms through C2, and add contact between instances.

### S2: OpenFOAM airflow (core)
Add a `cfd` solver option, `"openfoam"`.
- **Detection:** detect `docker` or `orb` plus a pinned OpenFOAM image tag. The system check adds a row. Install is a user-click `docker pull` of the pinned image, added to the allowlist.
- **Run:** the case is generated from the part (snappyHexMesh around the STL, simpleFoam with k-omega SST, a domain from the existing settings). It runs in the container with no network (`--network none`), mounts only the job dir, and has CPU and memory limits, a timeout and cancel.
- **Parse:** read forces/forceCoeffs into Cd/Cl/moments, and surface pressure and slices into the existing GLB/PNG formats.
- **Benchmarks:** a sphere at Re 1e4–1e5 within 10%, and a flat plate.
- **Labelling:** label the result "OpenFOAM k-ω SST · ±X% (validated)".
- **Fallback:** the built-in LBM stays as the fallback.
- **Testing:** use a fake docker runner in tests. The real image may be pulled ONLY in a QA step the user approves; don't pull in unit tests.

### S3: Material calibration, anisotropy, fatigue (core)
- **Calibration:** a calibration record per material and printer profile. Inputs are coupon tests (tensile bar: break force, cross-section, optional elongation; three-point bend: span, load, deflection) and part load tests (known load, measured deflection, at a probe point on a part plus a study). It fits E, yield/UTS and the knock-down by least squares, and a part load test updates E by inverse comparison against the study. It stores n and the scatter, and a confidence.
- **Anisotropy:** an orthotropic material for FDM, with in-plane vs between-layer (Z) properties and a print-direction setting on studies, mapped to the CalculiX `*ELASTIC, TYPE=ENGINEERING CONSTANTS` with an orientation. Failure uses the Z strength for tension across layers.
- **Fatigue:** an S-N curve per material (Basquin; datasheet or estimate, labelled), a load cycle definition (min/max load or fully reversed, count) and a Goodman mean-stress correction. It reports the cycles to failure and a fatigue safety factor at the hotspot, with a "fatigue estimate" label.
- **Tools and routes:** `calibration_*` and the fatigue options in the sim schema. Engineer prompt: use calibrated materials when present, and ask for fatigue cycles for rotating or vibrating parts (drones).
- **Benchmarks:** a synthetic coupon set recovers the known E/yield within 2%; orthotropic cantilevers bending about the layer axis vs across it match the analytic ratio; a fatigue hand calculation.

### S4: App: controls and calibration UI, plus the loose ends (app)
- **Studies page:** a nonlinear/contact section (contact pair picker in the viewer, steps); new result channels; an OpenFOAM solver choice with an install card from the system check; anisotropy (print direction gizmo); fatigue inputs and results.
- **Materials page:** a calibration section (or in the material editor) to enter coupon and load-test results, see the fitted values and scatter plot, and apply them. Calibrated materials show their badge everywhere.
- **Loose ends:**
  - The approval card's `r`/`a` keys must work in WebKit (the real app). Reproduce in Playwright WebKit and fix.
  - The stress histogram scales its axis to the data (tiny values visible).
- **Tests:** unit tests plus e2e against fixtures re-dumped from the real core.

### S5: Integration, real-solver QA, final review
- Merge; contract checks against the real core; full suites; rebuild.
- Real-solver QA with real CalculiX for every benchmark.
- OpenFOAM QA only if the user approves the image pull at that moment; otherwise record it as unverified.
- Final whole-branch review, then one fix wave.
