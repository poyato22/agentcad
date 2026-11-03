# Mises — 2-minute demo script (draft v1)

**Audience:** portfolio, 1315, a16z Speedrun / academy reviewers — technical founders/investors who have seen "AI makes a 3D shape" demos and are unimpressed by them.
**One-line message:** *Other AI tools draw shapes. Mises engineers machines — and proves they work.*
**Hero build:** a 1.6 kg **trotting quadruped robot dog** (12 actuated DOF, belt-driven knees, printed PETG/PA-CF + aluminium), because it exercises everything: concept design, mechanisms, packaging electronics, dynamics, FEM, topology optimization, BOM.
**Production rule:** every shot is real Mises output from one real build, captured from the app; long waits are cut, not faked. Numbers on screen come from the actual runs (placeholders in ⟨ ⟩ get filled after the build/benchmarks).

| Time | Picture | Voice-over / on-screen text |
|---|---|---|
| 0:00–0:06 | Black → fast 6-shot montage: zebra-striped shell, MuJoCo dog trotting, CalculiX stress field, topology-optimized bracket, belt drive spinning, BOM total. | (no VO) Title card: **Mises** — *AI that engineers, not just draws.* |
| 0:06–0:16 | Home screen → New build "Robot Dog". Type the prompt: *"Design a 1.6 kg quadruped robot dog that can trot at 0.5 m/s. Belt-driven knees, printable, under $400."* Think → **High**. | VO: "I describe what I need — the way I'd brief an engineer." |
| 0:16–0:30 | Concept stage: 3 pitched directions (silhouette + sketch cards). Circle the head on one sketch, comment *"lower, more aggressive stance"*. Revised sketch v2 appears → **Approve**. | VO: "Before any CAD, it pitches directions. I mark up the sketch, it revises, I approve." |
| 0:30–0:48 | Build view: the lead spawns sub-agents (legs · body · electronics) — announcements stream in the dock; parts land in the assembly in color; mechanism library inserts a GT2 **belt-driven knee** (belt spinning); electronics estimator packs battery, ESP32, servo driver into the body (x-ray). Assembly health flags a red overlap → agent fixes it (red box disappears). | VO: "A lead agent splits the work. It uses real mechanisms — gears, belts, bearings — packs the electronics, and checks every part for clashes." On-screen chips: *12 DOF · 43-mechanism library · 18 materials* |
| 0:48–1:10 | **Studies → Whole assembly → Walk.** Immersive playback: the dog trots (MuJoCo), torque chart per joint vs. actuator limit. Knee bar turns red: ⟨peak 3.2 N·m vs 2.0 N·m⟩ → agent's note: *"knee servo underpowered — switching to 2:1 belt reduction"* → re-run, bar green, ⟨stability margin 90 mm⟩. | VO: "This is the difference. Mises simulates the robot walking — real dynamics — and it caught that the knee would stall. It fixed the drivetrain and re-ran the study." On-screen: **"Simulation in the loop."** |
| 1:10–1:25 | **Check part stresses** → peak joint loads become a CalculiX study on the femur: stress field, ⟨safety factor 3.1⟩. Then **Generative design** on the hip bracket: density converges live, organic bracket, ⟨−58 % mass, CalculiX-verified⟩. Section-slider sweep through the leg. | VO: "Peak loads from the gait flow straight into finite-element analysis, and topology optimization cuts the bracket's weight in half — verified, not guessed." |
| 1:25–1:40 | **Benchmarks** (motion-graphic chart): same model, with and without Mises — ⟨CADBench IoU / valid-solid rate⟩, ⟨CADTestBench pass rate⟩, ⟨Mises-Functional: requirements met⟩. Bars animate; Mises wins on the functional suite by the widest margin. | VO: "Same Claude model, with and without the Mises harness. Better geometry — and far more designs that actually meet their engineering requirements." Small print: model, date, subset sizes, link to method. |
| 1:40–1:52 | Outputs: STEP export, auto drawing sheet, BOM with prices ⟨$362⟩, print preview, and a quick cut to two other builds (ARC rocket: flight sim apogee; egg-drop: drop-test g-force) to show range. | VO: "It ships manufacturable files — STEP, drawings, a priced bill of materials — and the same harness does rockets, mechanisms, anything with physics." |
| 1:52–2:00 | Closing: the dog trotting, slow push-in; logo + one line + your name/link. | VO: "Mises. AI that engineers." Text: *built by Hartigan · github / site* |

## Capture plan (after the 2026-11-04 usage reset)
1. **Real build:** run the hero prompt in Mises on Claude Code (Opus, High; Max for the concept stage), let it complete including Walk study, FEM, topopt; iterate on Mises itself where the build exposes weaknesses. Save the build.
2. **Screen capture:** Playwright-driven capture of the real app at 2560×1600 against the saved build (deterministic, no cursor jitter), plus immersive views; cut waits; speed-ramp long agent work (labelled "sped up").
3. **Benchmarks:** baseline = plain Claude Code + build123d with no harness vs. Mises, same model and attempt budget, on CADBench (60), CADTestBench (50) and the 20-task Mises-Functional suite; iterate Mises on failures and re-run; publish method + numbers honestly (including any losses).
4. **Video:** HyperFrames composition — captured clips + titles, chips, benchmark chart, captions; music bed; optional voice-over (your voice recommended for applications; TTS fallback).

## Decisions (2026-11-03)
- **Design + edit in Claude Design** (it now lives in Claude, made from the Design artifact type): build the animated title cards, chips, benchmark chart and the overall edit there; drive it via Claude in Chrome if needed. Real Mises screen captures are imported as media. HyperFrames is the fallback renderer if a final MP4 export from Claude Design isn't possible.
- **Voice-over: the user's own voice.** Mises team writes the final VO script and a timing sheet; the user records; cuts are timed to the recording.
- Open: name/links for the end card.
