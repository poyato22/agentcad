# Mises CAD benchmarks (2026-11-04)

**Headline (held-out TEST, never looked at while tuning Mises):** Mises leads on image→part (CADBench), is level-to-slightly-ahead on text→part (CADTestBench) and on the physics-graded suite, at 4–8× the cost per task and 1.5–2.5× the agent time. Baseline = the same Claude Code model (Sonnet 5.5, Think High) with a plain build123d prompt and one sandboxed exec tool; one attempt per task for both.

How to read: *n* tasks, paired; *Mises − baseline* is the mean per-task difference with a 95% paired bootstrap CI; win/loss counts are per task. Splits: DEV sets were used to find and test Mises fixes; TEST sets were drawn after (next items of the same seed-pinned rankings, disjoint from DEV) and run once with the final Mises. Mises-Physics (20 tasks) was written before any tuning, split 5 DEV / 15 TEST. Mises-Functional (20) was never tuned on; its Mises runs are from the first pass (pre-fix Mises).

Model: `claude-code/sonnet` for both systems, Think: High, one build per task. Invalid or missing outputs score 0. Only tasks run by both systems are compared (paired).

| Suite | n | Metric | Mises | Baseline | Mises − baseline (95% CI) | Mises better / worse / tie |
|---|---|---|---|---|---|---|
| CADBench (image→part) | 60 | volume IoU | 71.9% | 62.6% | +9.3 pts (+4.0 to +14.5) | 42 / 18 / 0 |
| CADTestBench (text→part) | 50 | CADTest pass rate | 93.8% | 92.9% | +1.0 pts (-0.4 to +2.8) | 3 / 3 / 44 |
| Mises-Functional (engineering) | 20 | requirements passed | 100.0% | 98.8% | +1.2 pts (+0.0 to +3.1) | 2 / 0 / 18 |
| CADBench, held-out (image→part) | 60 | volume IoU | 72.2% | 63.6% | +8.5 pts (+4.9 to +12.6) | 46 / 14 / 0 |
| CADTestBench, held-out (text→part) | 50 | CADTest pass rate | 94.2% | 93.2% | +1.0 pts (+0.0 to +2.6) | 2 / 0 / 48 |
| Mises-Physics, dev | 5 | requirements passed | 100.0% | 100.0% | +0.0 pts (+0.0 to +0.0) | 0 / 0 / 5 |
| Mises-Physics, held-out | 15 | requirements passed | 100.0% | 98.3% | +1.7 pts (+0.0 to +5.0) | 1 / 0 / 14 |

### CADBench (image→part) (n = 60)

| | Mises | Baseline |
|---|---|---|
| valid output | 98.3% | 100.0% |
| volume IoU | 71.9% | 62.6% |
| F1 @ 1% bbox | 70.3% | 56.0% |
| Chamfer (unit box, valid only) | 0.0131 | 0.0178 |
| median agent time (s) | 32 | 12 |
| median wall time incl. harness (s) | 72 | 12 |
| mean cost per task (USD, list price) | 0.274 | 0.033 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| family: B | 74.1% | 68.7% |
| family: F | 56.0% | 49.2% |
| family: M | 85.7% | 70.0% |

### CADTestBench (text→part) (n = 50)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| CADTest pass rate | 93.8% | 92.9% |
| requirement satisfaction | 92.2% | 91.3% |
| all tests pass | 66.0% | 70.0% |
| median agent time (s) | 17 | 11 |
| median wall time incl. harness (s) | 58 | 11 |
| mean cost per task (USD, list price) | 0.138 | 0.021 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| type: dimensions_ratios | 90.6% | 89.3% |
| type: geometry_types | 100.0% | 100.0% |
| type: solid_shell_validity | 98.5% | 98.5% |
| type: spatial_arrangement | 91.1% | 88.9% |
| type: topology_checks | 98.1% | 96.2% |
| type: volumetric_checks | 88.0% | 87.2% |

### Mises-Functional (engineering) (n = 20)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| mean requirement pass | 100.0% | 98.8% |
| strict pass | 100.0% | 90.0% |
| median agent time (s) | 41 | 23 |
| median wall time incl. harness (s) | 81 | 23 |
| mean cost per task (USD, list price) | 0.231 | 0.048 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| category: edit | 100.0% | 100.0% |
| category: fits | 100.0% | 100.0% |
| category: manufacturability | 100.0% | 100.0% |
| category: mechanisms | 100.0% | 93.8% |
| category: structural | 100.0% | 100.0% |

### CADBench, held-out (image→part) (n = 60)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| volume IoU | 72.2% | 63.6% |
| F1 @ 1% bbox | 71.2% | 62.1% |
| Chamfer (unit box, valid only) | 0.0120 | 0.0166 |
| median agent time (s) | 34 | 14 |
| median wall time incl. harness (s) | 73 | 14 |
| mean cost per task (USD, list price) | 0.266 | 0.033 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| family: B | 74.3% | 63.3% |
| family: F | 56.9% | 50.0% |
| family: M | 85.3% | 77.7% |

### CADTestBench, held-out (text→part) (n = 50)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| CADTest pass rate | 94.2% | 93.2% |
| requirement satisfaction | 93.2% | 92.7% |
| all tests pass | 72.0% | 70.0% |
| median agent time (s) | 16 | 11 |
| median wall time incl. harness (s) | 58 | 11 |
| mean cost per task (USD, list price) | 0.143 | 0.023 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| type: dimensions_ratios | 90.6% | 89.6% |
| type: geometry_types | 100.0% | 100.0% |
| type: solid_shell_validity | 98.3% | 98.3% |
| type: spatial_arrangement | 100.0% | 100.0% |
| type: topology_checks | 97.1% | 94.2% |
| type: volumetric_checks | 91.1% | 90.3% |

### Mises-Physics, dev (n = 5)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| mean requirement pass | 100.0% | 100.0% |
| strict pass | 100.0% | 100.0% |
| median agent time (s) | 76 | 50 |
| median wall time incl. harness (s) | 119 | 50 |
| mean cost per task (USD, list price) | 0.483 | 0.127 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| category: dynamics | 100.0% | 100.0% |
| category: modal | 100.0% | 100.0% |
| category: rocketry | 100.0% | 100.0% |
| category: stability | 100.0% | 100.0% |
| category: structural | 100.0% | 100.0% |

| task | Mises | Baseline |
|---|---|---|
| PH01_bracket_200N | mass_g 20.75, vm_p995_mpa 19.3, sf 14.26 | mass_g 20.28, vm_p995_mpa 41.8, sf 6.6 |
| PH03_sensor_mast | mass_g 93.1, f 367.1185, f1_hz 367.1 | mass_g 86.1, f 370.7835, f1_hz 370.8 |
| PH05_two_link_arm | shoulder_nm 1.214, elbow_nm 0.466, links_mass_g 60.3, d 0.1855297000768995, upper_arm_defl_mm 0.186 | shoulder_nm 1.241, elbow_nm 0.469, links_mass_g 81.1, d 0.14051680019139665, upper_arm_defl_mm 0.141 |
| PH08_robot_base | structure_kg 1.336, tip_angle_deg 17.43, com_height_mm 398.1 | structure_kg 1.331, tip_angle_deg 17.03, com_height_mm 408.2 |
| PH11_rocket_c6 | static_margin_cal 1.41, cp_mm 120.6, cg_mm 85.3, n_fins 3, structure_g 89.0, liftoff_g 113.1, apogee_m 263.4 | static_margin_cal 1.162, cp_mm 151.1, cg_mm 118.6, n_fins 3, structure_g 90.5, liftoff_g 114.6, apogee_m 243.6 |

### Mises-Physics, held-out (n = 15)

| | Mises | Baseline |
|---|---|---|
| valid output | 100.0% | 100.0% |
| mean requirement pass | 100.0% | 98.3% |
| strict pass | 100.0% | 93.3% |
| median agent time (s) | 57 | 23 |
| median wall time incl. harness (s) | 97 | 23 |
| mean cost per task (USD, list price) | 0.291 | 0.081 |
| time-cap hits | 0 | 0 |
| harness errors | 0 | 0 |
| category: buckling | 100.0% | 100.0% |
| category: dynamics | 100.0% | 100.0% |
| category: mechanisms | 100.0% | 100.0% |
| category: modal | 100.0% | 100.0% |
| category: rocketry | 100.0% | 100.0% |
| category: stability | 100.0% | 100.0% |
| category: structural | 100.0% | 95.0% |

| task | Mises | Baseline |
|---|---|---|
| PH02_ribbed_plate | mass_g 105.7, f 718.7017, f1_hz 718.7 | mass_g 105.7, f 689.7413, f1_hz 689.7 |
| PH04_column_buckling | mass_g 124.3, b 6.169281, buckling_factor 6.17 | mass_g 139.1, b 8.618575, buckling_factor 8.62 |
| PH06_flywheel | mass_g 634.0, inertia_kgm2 0.001645 | mass_g 625.2, inertia_kgm2 0.001644 |
| PH07_pendulum | T 0.9989571667398363, period_s 0.999, mass_g 263.2 | T 1.0000026012119865, period_s 1.0, mass_g 376.0 |
| PH09_gear_3to1 | z_pinion 18, z_gear 54, ratio 3.0, centre_distance_mm 54.0, sweep_overlap_mm3 0.0 | z_pinion 18, z_gear 54, ratio 3.0, centre_distance_mm 54.0, sweep_overlap_mm3 0.0 |
| PH10_flexure_hinge | k 0.5043994490755526, k_nm_per_rad 0.504 | k 0.5341661072561295, k_nm_per_rad 0.534 |
| PH12_rocket_b6 | static_margin_cal 2.168, cp_mm 151.8, cg_mm 104.1, n_fins 4, structure_g 78.7, liftoff_g 98.5, apogee_m 107.5 | static_margin_cal 2.485, cp_mm 163.0, cg_mm 113.3, n_fins 4, structure_g 83.7, liftoff_g 103.5, apogee_m 99.6 |
| PH13_spring_beam | k_n_per_mm 50.9, k 50.89652292350401 | k_n_per_mm 50.49, k 50.49439000518811 |
| PH14_torsion_tube | k 200.00968636766663, k_nm_per_rad 200.0 | k 28.6125656887605, k_nm_per_rad 28.6 |
| PH15_shelf_bracket | mass_g 56.7, vm_p995_mpa 76.0, defl_mm 0.249 | mass_g 49.6, vm_p995_mpa 36.9, defl_mm 0.153 |
| PH16_crank_arm | mass_g 120.4, vm_p995_mpa 97.9, defl_mm 1.257 | mass_g 134.3, vm_p995_mpa 92.4, defl_mm 1.279 |
| PH17_tuning_bar | f 441.6668, f1_hz 441.7 | f 440.4387, f1_hz 440.4 |
| PH18_balanced_lever | imbalance_mm 0.039, mass_g 858.5 | imbalance_mm 0.028, mass_g 702.2 |
| PH19_lamp_base | structure_kg 1.946, tip_angle_deg 15.38, com_height_mm 128.3 | structure_kg 1.931, tip_angle_deg 14.76, com_height_mm 127.8 |
| PH20_drone_arm | mass_g 11.04, f 705.6113, f1_hz 705.6 | mass_g 13.83, f 1025.143, f1_hz 1025.1 |

## DEV iteration history (Mises headline / baseline headline, paired wins-losses)

| Round | What changed | CADBench IoU | CADTestBench pass | CADTestBench all-pass wins | Physics/Functional |
|---|---|---|---|---|---|
| round0_first_pass | main @ 4bf7af07 + harness; all DEV suites, both systems | 63.8% / 62.6% (29-31) | 91.9% / 92.9% (2-5) | 0 Mises-only / 4 baseline-only | 100.0% / 98.8% (2-0) |
| round1_after_2_fixes | chat-attached images checkable by reference_photo_check; multi-solid warning. Mises re-run on all 60 CADBench + the 19 CADTestBench it failed | 71.9% / 62.6% (42-18) | 93.8% / 92.9% (3-3) | 1 Mises-only / 3 baseline-only | 100.0% / 98.8% (2-0) |
| round2_dev | units rule + unit-slip guard, schema titles dropped, zero-solid warning, photo mismatch hints (reverted: no gain), designer verify-every-requirement (reverted: no gain). Mises re-run on DEV CADBench 60 + CADTestBench 50 | 72.2% / 62.6% (41-19) | 91.8% / 92.9% (4-5) | 2 Mises-only / 5 baseline-only | 100.0% / 100.0% (0-0) |

## Honest notes
- **Where Mises leads clearly:** image→part. On held-out CADBench IoU is +8.5 pts (95% CI +4.9 to +12.6), Mises wins 46 of 60 tasks, and it leads in every family (B 74 vs 63, F 57 vs 50, M 85 vs 78). The gain comes from checking the model against the attached image.
- **Close to level:** text→part. On held-out CADTestBench the pass rate is +1.0 pt (CI 0.0 to +2.6) and all-tests-pass is 72% vs 70%. On DEV the baseline led on all-tests-pass in every round (70% vs 62–66%). Many CADTestBench failures for both systems come from label conventions, for example "hexagon side length 1.5" being graded as a 1.5 mm circumscribed diameter, not from modelling errors.
- **Saturated:** Mises-Physics and Mises-Functional. On Physics TEST, Mises is 15/15 strict and the baseline 14/15. The baseline missed the torsion-tube stiffness target, 28.6 vs 200 N·m/rad. This model can hand-calculate these briefs, so the suites need harder tasks before they can separate the two systems. Physics DEV was 5/5 for both.
- **Cost and time (held-out):** Mises costs $0.27 against $0.03 per task on CADBench, $0.14 against $0.02 on CADTestBench and $0.29 against $0.08 on Physics, all at list price. Median agent time is 34 s against 14 s, 16 s against 11 s, and 57 s against 23 s. Each Mises turn re-reads about 95k tokens of tool schemas and rules. Dropping the generated schema titles removed about 15% of the schema text. No fast path for simple prompts was shipped.
- **Reverted:** two DEV changes were reverted because they did not help. The designer "verify every requirement" pass gave 91.8% vs 93.8% at +4% cost, and the photo-check mismatch hints gave 72.1% vs 71.9%.
- **Harness:** graders are independent of both systems. They are our own CalculiX static, modal and buckling decks, checked against hand calculations within 1–3%, plus mass and tipping models, Barrowman with a 1-D flight model, and the official CADTests through CadQuery. Every functional and physics reference design passes its grader.
- **Usage:** about $102 list-price-equivalent across all rounds. Weekly utilisation went from 0% to 30% on the account, which other sessions share.
