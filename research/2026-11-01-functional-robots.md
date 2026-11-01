# Functional robots: drivetrain, mechanisms, engineering checks, dynamics and materials (research for E17, E21, E27, E28, E29, E30, E32, E33)

Researched 2026-11-01 for Plan 10 (`docs/superpowers/plans/2026-11-01-functional-robots.md`).
Tags: **[T]** = tested here (throwaway venv `scratchpad/p10-mech/.venv`: Python 3.13, build123d 0.13.0, bd_warehouse 0.4.0, py_gearworks 0.0.24, mujoco 3.17.0, coacd 1.0.14, trimesh 5.2.0, on an Apple M5 Pro). **[V]** = vendor or datasheet figure. **[2]** = secondary or retailer source only. **[J]** = my judgment, not sourced. `core/pyproject.toml` was not modified.

The user's goal is that simulation is what makes Mises a mechanical-engineering tool rather than "another AI Blender". This document turns that into code. The agent gets real drivetrain geometry and a mechanism library whose parts carry kinematic and sizing data. It also gets an engineering-check layer that uses rigid-body dynamics (MuJoCo) to produce actuator torques, stability and joint reactions, and those reactions feed the CalculiX stress studies Mises already has.

---

## 0. Top recommendations (TL;DR)

1. **Add `mujoco` (Apache-2.0, arm64 wheel) to core. It is the dynamics engine for E30.** [T] It matches the hand calculation for a static 2-link arm exactly (1.9865 / 0.515 N·m). A 3 kg box quadruped with 8 position servos runs 3 s of squat at 1 ms steps in 0.021–0.036 s (80–140× real time). It closes a four-bar loop with an angle error of 0.002° against the analytic solution. Joint reaction forces from `mj_rnePostConstraint` are exact on the arm test. Build models with `mujoco.MjSpec` (compiles in 8 ms), not string XML. Use PyBullet and Drake only as fallbacks (§4.1).
2. **Gears: use the bd_warehouse already in core for spur, helical, rack and worm, and add `py-gearworks` (Apache-2.0) for bevel, ring (internal) and cycloid gears, plus `mesh_to` phasing and contact ratio.** [T] Every pair tested has zero interference once phased. A planetary set needs an extra ring phase of 180°/z_ring. Without it the ring and planets overlap by 34 mm³. cq_gears (Apache-2.0) is CadQuery-only and not on PyPI, so skip it. "gggears" is py_gearworks' old name.
3. **No pip package draws GT2/HTD timing pulleys. Write them as a `mises_shapes` recipe** (§1.3). It is ~40 lines: PD = N·P/π, OD = PD − 2·PLD, and the belt length/center solve [T]. Reuse bd_warehouse for bearings (608 = `"M8-22-7"` [T]), the NEMA17/23 `StepperMotor` [T], `FlexibleCoupler`, `MetricLeadScrew`, `ShaftKey`/`Keyway`, `RetainingRing`, `Sprocket` and threads.
4. **A new `mises.mech` package** (§2, §6) for drivetrain, mechanism and actuator items. Every library item emits geometry plus a **mechanism card**: ports, joints with axes and limits, couplings (ratio), ROM, sizing formulas, and the checks it needs. The agent picks mechanisms by function, not by shape.
5. **Extend the assembly model in two ways** (E27/E28). (a) Add **coupled joints**: `gear`, `rack`, `screw` and `belt` joints carrying a ratio. (b) Allow **closed loops**: four-bar, Jansen, Klann and parallel grippers all need them, and `solve.py` rejects them today. Solve loops numerically (scipy `least_squares` over the loop closure, or MuJoCo equality constraints). Export both to MJCF as `<equality joint polycoef>` and `<equality connect>`.
6. **A new `mises.dyn` package with an assembly→MJCF exporter.** Mass and inertia come from the B-rep (OCCT `GProp`), not the mesh: MuJoCo's own mesh inertia was 1.2% off in mass and up to 1.9% off in inertia [T]. Collision uses CoACD convex decomposition (MIT, 1.3 s for an L-bracket [T]) or primitive envelopes. Actuators come from catalog data (torque, speed, rotor inertia × ratio² as `armature`). Without armature and `implicitfast`, small printed links blow up [T].
7. **New checks for E29/E30**, each a tool that writes a pass/warn/fail into the spec:
   - `dyn_torque`: inverse dynamics plus a forward sim of a trajectory, giving peak and RMS torque per joint vs the actuator curve with margin.
   - `dyn_stability`: CoM against the support polygon, static tip angle, and ZMP along a trajectory.
   - `rom_sweep`: an OCCT exact sweep (already exists) plus a fast MuJoCo hull sweep.
   - `power_budget`: battery runtime and current.
   - `thermal_electronics`.
   - `cable_route`.
   - `clash_report`: E33 overlaps plus stray/far parts.
8. **Dynamics → FEM loop** (E30): the peak joint reaction per link (force + moment at each joint anchor, from MuJoCo `cfrc_int`) becomes a CalculiX load case on that link's part. The load is a `BearingLoad`/`RemoteForce` + `MomentLoad` on the bore faces of the joint where the load enters, with a `pinned` support at the opposite bore. All of these load and support types exist in `sim/settings.py` today. The safety factor is then written back into the spec.
9. **Materials** (E32): add PC, PA12, PA6-CF, TPU 95A, POM, Al 6061-T6/7075-T6, steel 1018/4140, 304, brass C360 and CF plate (§5). The `Material` dataclass has to stop applying `FDM_KNOCKDOWN` to everything. Add `process`, `uts_mpa`, `max_service_c`, `failure` (ductile/brittle), `elongation_pct`, `fatigue_limit_mpa` and `printable`. Metals and CF get knockdown 1.0 and keep the solver's own safety factor.
10. **An electronics estimator** (E17): a requirements → component pick → envelope + mass + power table (§3.6), reserved in the layout with `internals_pack`. Extend the catalog (`boards.json`, `batteries.json`, and new `drivers.json`, `radios.json`, `sensors.json`, `power.json`, `connectors.json`, `actuators.json`) with the dimension and mass rows below.

---

## 1. Drivetrain geometry in build123d (E21)

### 1.1 Library survey

| Library | Licence | Pip | What it has | Verdict |
|---|---|---|---|---|
| **bd_warehouse** 0.4.0 ([github](https://github.com/gumyr/bd_warehouse)) | Apache-2.0 | yes, **already in core** | `gear.py`: `SpurGear`, `SpurGearPlan`, `HelicalGear`, `RackGear`, `Worm`, `WormWheel`, `WormGear` (pair), `InvoluteToothProfile`. `sprocket.Sprocket` (roller chain). `bearing`: deep-groove (capped too), angular contact, cylindrical/tapered roller, `PressFitHole`. `fastener`, `thread`, `flange`, `pipe`, `shaft_key` (`ShaftKey`, `Keyway`, ISO 286 fits), `retaining_ring` (`ExternalSnapRing`, `InternalSnapRing`, `RetainingRingGroove`), `bushing`, `o_rings`. `open_builds`: `StepperMotor` (Nema17/23/23HT), `FlexibleCoupler` (8 mm, 1/4"), `MetricLeadScrew`, `AcmeAntiBacklashNutBlock8mm`, `LockCollar`, V-slot/C-beam rails and wheels | **Use** for spur, helical, rack, worm, bearings, keys, snap rings, steppers, couplers and lead screws [T] |
| **py_gearworks** 0.0.24 ([PyPI](https://pypi.org/project/py-gearworks/), [docs](https://gggears.readthedocs.io/en/latest/build123d_examples.html)); formerly *gggears* | Apache-2.0 | yes (pure Python, needs build123d) | `SpurGear`, `SpurRingGear`, `HelicalGear`, `HelicalRingGear`, `BevelGear` (incl. spiral), `CycloidGear`, `InvoluteRack`, `HelicalRack`. Profile shift, undercut, backlash, crowning. `mesh_to()` places and phases a mating gear. `get_contact_ratio()`. `PlanetaryGearset` (tooth-count and ratio helper only, no geometry) | **Add**: bevel, internal ring, cycloid, auto phasing and contact ratio [T]. It is early-stage (0.0.x), so pin the version |
| cq_gears ([github](https://github.com/meadiode/cq_gears)) | Apache-2.0 ([2](https://awesome.ecosyste.ms/projects/github.com%2Fmeadiode%2Fcq_gears)) | **not on PyPI**; CadQuery dev | spur, helical, herringbone, bevel, ring, rack, worm, crossed helical | Skip: CadQuery-only, unmaintained |
| cqparts_gears | Apache-2.0 | old | trapezoidal approximations | Skip |
| GT2/HTD pulley | — | none found | — | **Write a recipe** (§1.3) |

Neither library does gear **strength** (Lewis/AGMA bending, Hertz contact). That is in §2.3 as formulas.

### 1.2 Spike results [T]

All runs in `scratchpad/p10-mech/spike_mech*.py`. The results JSON files are next to the scripts.

| Item | How | Time | Result |
|---|---|---|---|
| Spur pair m1, 20T/40T, 6 mm | bd_warehouse `SpurGear`. Center a = m(z1+z2)/2 = 30 mm, gear 2 rotated by **180°/z2** and counter-rotated by the ratio | 0.87 s (2 gears + 5 boolean checks) | interference **0.000 mm³ at all 5 phases**, valid solids |
| Spur pair, same | py_gearworks `g1.mesh_to(g2)` | 0.33 s | center 30.000 mm, interference 0, **contact ratio 1.635** |
| Bevel 15T/30T, m1.5, 90° shafts | py_gearworks `BevelGear(cone_angle=2·atan(z1/z2))`, other gear 2·(90°−δ1) | 1.11 s | both valid, interference 0. The *default* `cone_angle` on a 16/16 pair gave an invalid solid, so always pass cone angles |
| Ring 60T, cycloid 11T | py_gearworks | <1 s | valid |
| Planetary 12/12/36 × 3 | py_gearworks sun + 3 planets placed with `mesh_to(sun, target_dir)` + `SpurRingGear` | 0.86 s (+2.56 s phase search) | sun–planet 0. Ring–planet **34.26 mm³ each until the ring is turned by 180°/36 = 5°, then 0**. Assembly condition (zs+zr)/n integer holds. Ratio (ring fixed, sun in, carrier out) = 1+zr/zs = 4 |
| Helical 20T/40T ±20°, rack 30T, worm pair | bd_warehouse `HelicalGear(m, z, pa, helix, t)`, `RackGear`, `WormGear(m=1, starts=1, q=10, z=30, …)` | 4.36 s total (helical dominates), worm pair 0.17 s | all valid, worm/wheel interference 0 |
| **GT2 20T/60T + belt** | recipe §1.3 | 0.23–0.39 s | PD 12.732 / 38.197 mm. For a target center of 80 mm the belt is 242 mm (121 teeth), so take the next stock **122T (244 mm)**, which gives an exact center of **80.997 mm**. Wrap on the small pulley is 161.9°, with **9.0 teeth in mesh** (≥6 is the usual minimum). Belt modelled as a band on the OD: overlap 0. A band at the true tooth depth overlaps 362 mm³, so teeth must be modelled or the band kept outside the OD |
| **608 bearing** | bd_warehouse `SingleRowDeepGrooveBallBearing("M8-22-7", "SKT")` | 0.09 s | 22×22×7, 14 solids (races + balls), valid. Solid-steel volume mass 13.0 g against 12 g real, so catalog mass wins (the existing rule) |
| **NEMA17 + mount** | bd_warehouse `StepperMotor("Nema17")` + plate (50×50×4, Ø22.4 pilot, 4×Ø3.4 on 31 mm) | 0.10 s | motor bbox 42.4×42.4×72 (body z −48…0, shaft to +24). Plate valid |

Takeaways for the implementation:
- **Phasing is the trap.** A meshing pair is only correct with the phase offset: half a tooth for an external pair, and 180°/z_ring for a ring. The `mech.gear_pair` function must set it, and it must run a boolean interference check at ≥3 phases before the agent sees the result. Cost is <1 s.
- **Library bearings are multi-solid compounds.** For FEM, use the existing envelope (a ring with a bore). For rendering, keep the detailed one.
- **Throughput:** each drivetrain element costs 0.1–1.5 s inside the sandboxed worker. That is fine for an agent loop.

### 1.3 Recipes to write (no library has them)

```python
# mises_shapes additions (or mises.mech.drive): all return build123d Part + a `card` dict
gt2_pulley(teeth, width=6, bore=5, flanges=True, hub_d=None, hub_h=0, setscrew="M3") -> Part
htd_pulley(teeth, pitch=3|5|8, width, bore, ...)                        # HTD 3M/5M/8M
timing_belt(pulley_a, pulley_b, center=None, teeth=None, width=6, profile="GT2")
    # solves belt teeth (stock list) <-> exact center; returns band solid + card
    # {pitch_len, teeth, center, wrap_deg, teeth_in_mesh, tension_N_suggested}
belt_length(pd1, pd2, c) = 2·sqrt(c² − (r2−r1)²) + π(r1+r2) + 2(r2−r1)·asin((r2−r1)/c)
shaft(d, length, flats=[], keyways=[], grooves=[(z, "DIN471")], chamfer=0.5)
d_shaft_bore(d=5, flat=0.5) ; clamp_hub(bore, od, slit=True, screw="M3")
bearing_seat(bearing="608", fit="press"|"slip", shoulder=True, retention="snap"|"cap"|"glue")
    # 3D-print fit: bore = D − 0.10 (press) / D + 0.10 (slip) [J, typical FDM]; shoulder ≥ 1.5 mm
motor_mount("NEMA17", plate=4, slots=True)  # slots for belt tensioning
servo_horn_interface("MG996R", spline="25T")  # or a printed horn clamp + 4 screws
coupler_rigid(d1, d2, length) ; coupler_jaw(...)  # bd_warehouse FlexibleCoupler for 8 mm / 1/4"
```

GT2 2 mm profile: pitch 2.0 mm, pitch-line differential 0.254 mm, belt thickness 1.38 mm, tooth height 0.75 mm, tooth radius 0.555 mm ([Gates PowerGrip GT design manual, V](https://www.gates.com/us/en/power-transmission/belts/synchronous-belts/powergrip-gt3-belts.html); the dimensions match the widely used open profile in [Droftarts parametric pulley, 2](https://www.thingiverse.com/thing:16627), which is CC-BY. Re-derive the numbers rather than copying its code). HTD 3M/5M/8M: pitch 3/5/8 mm, PLD 0.381/0.5715/0.686 mm. Stock GT2 closed loops: 100, 110, 112, 122, 130, 140, 150, 158, 160, 166, 170, 180, 188, 200, 220, 232, 250, 280, 300, 400 teeth [2, vendor listings].

### 1.4 Actuator models (catalog + envelope geometry)

Each actuator is a catalog item with an envelope part (exists today for SG90, MG996R and NEMA17) **plus a performance block** that `mises.dyn` reads:

```json
"perf": {"type": "servo|stepper|bldc_geared|bldc|dc_geared",
  "voltage_v": 6.0, "stall_torque_nm": 1.08, "no_load_rpm": 60, "rated_torque_nm": 0.35,
  "stall_current_a": 2.5, "idle_current_a": 0.01, "gear_ratio": null,
  "rotor_inertia_kgm2": null, "backlash_deg": 1.0, "feedback": "pot|encoder|none",
  "output_axis": [0,0,1], "output_origin_mm": [10.2,0,42.9], "rom_deg": [-90, 90]}
```

Rows to add (envelope L×W×H mm, mass g; torque at the stated voltage):

| Item | Envelope | Mass | Output | Notes / source |
|---|---|---|---|---|
| SG90 | 23×12.2×22.7 | 9 | 0.18 N·m stall @4.8 V, 0.1 s/60° | exists |
| MG996R | 40.7×19.7×42.9 | 55 | 0.92 N·m @4.8 V / 1.08 N·m @6 V, 0.17 s/60° @4.8 V, stall ~2.5 A | [V, TowerPro sheet; numbers vary by clone] |
| DS3218 (20 kg) | 40×20×40.5 | 60 | 1.9 N·m @5 V / 2.1 @6.8 V | [2] |
| Feetech STS3215 (bus servo, 12-bit encoder) | 45.2×24.7×35 | 55 | 1.9 N·m @7.4 V | [V Feetech; used in SO-100 arms] |
| Dynamixel XL430-W250-T | 28.5×46.5×34 | 57.2 | 1.4 N·m stall @11.1 V, 1.3 A; 57 rpm | [V](https://emanual.robotis.com/docs/en/dxl/x/xl430-w250/) |
| Dynamixel XM430-W350 | 28.5×46.5×34 | 82 | 4.1 N·m stall @12 V, 2.3 A; 46 rpm | [V](https://emanual.robotis.com/docs/en/dxl/x/xm430-w350/) |
| NEMA14 (35 mm) | 35×35×(28–40) | 120–180 | 0.1–0.2 N·m holding | 26 mm M3 pattern, 22 mm pilot, 5 mm shaft |
| NEMA17 34/40/48/60 mm | 42.3×42.3×L | 220/280/360/500 | 0.26/0.40/0.52/0.65 N·m holding | 31 mm M3, 22 mm × 2 mm pilot, 5 mm shaft × 24 [V, typical; bd_warehouse geometry] |
| NEMA23 56/76 | 56.4×56.4×L | 700/1000 | 1.2/1.9 N·m | 47.14 mm pattern, Ø38.1 pilot, 6.35/8 mm shaft |
| CubeMars AK80-9 | Ø98×61.5 | 485 | 9 N·m rated / 18 N·m peak, 9:1, 24–48 V | [V](https://www.cubemars.com/goods-1031-AK80-9.html) |
| MIT Mini-Cheetah-class actuator | Ø96×40 | 480 | 17 N·m peak, 6:1 planetary, ~40 rad/s | [Katz 2019 thesis, V](https://dspace.mit.edu/handle/1721.1/118671) |
| Unitree GO-M8010-6 | Ø96×43 | 530 | 23.7 N·m peak, 6.33:1 | [V Unitree] |
| Gimbal BLDC 2804 / 4108 / 5010 class | Ø33×15 / Ø46×25 / Ø56×26 | 40 / 90 / 100 | direct drive or + printed cycloid/planetary | [2] |
| N20 gearmotor (6 V, 100:1–298:1) | 12×10×34 incl. gearbox | 10 | 0.05–0.2 N·m | [2] |
| 25D / 37D metal gearmotor | Ø25×60 / Ø37×65 | 90 / 200 | 0.5–2 N·m / 1–4 N·m depending on ratio | [V Pololu](https://www.pololu.com/category/115/25d-metal-gearmotors) |

---

## 2. Mechanism library spec (E28, E27)

### 2.1 What each mechanism must carry to be useful to an agent

A mechanism is not just a shape. Each entry in `mises.mech.library` is a parametric function returning a **MechanismCard** plus parts:

```python
class Port(BaseModel):          # where it connects to the rest of the robot
    name: str                   # "input", "output", "mount", "foot"
    kind: Literal["rot", "lin", "mount", "end_effector"]
    frame: Frame                # origin + axis in mechanism coords (assembly Frame)
class MechJoint(BaseModel):     # maps 1:1 to assembly Joint (+ new coupled kinds)
    kind: Literal["fixed","revolute","slider","cylindrical","ball","universal",
                  "gear","rack","screw","belt","loop_connect"]
    a: str; b: str; frame: Frame; limits: list[float] | None
    ratio: float | None         # gear/belt: w_b = ratio * w_a; rack/screw: mm per rad
class MechanismCard(BaseModel):
    id: str; params: dict; parts: list[str]
    joints: list[MechJoint]
    dof: int                    # mobility (Grubler / Kutzbach) - computed, checked
    ports: list[Port]
    transmission: dict          # {"ratio": 4.0} or {"curve": [[in, out]...]} (linkages)
    rom: dict                   # per joint + input range for which the mechanism is valid
    sizing: dict                # named results: output torque, force, speed, efficiency
    checks: list[str]           # which checks apply: "grashof","contact_ratio","pressure_angle",
                                # "transmission_angle","buckling","backdrivable","lewis_bending"
    warnings: list[str]
```

The agent calls `mech_list(function="convert rotation to linear", constraints={...})` and gets ranked candidates with trade-offs. `mech_insert(id, params, at=Frame)` builds the parts, inserts the instances and joints, and returns the card. Then `mech_check(instance_group)` runs the kinematic and strength checks.

### 2.2 The catalogue (~40)

ω = speed, τ = torque, η = efficiency, m = module, z = teeth, p = lead.

| # | Mechanism | Params | Joints (in Mises kinds) | Transmission / sizing | Checks / notes |
|---|---|---|---|---|---|
| 1 | **Spur gear pair** | m, z1, z2, width, pa=20°, x-shift | 2 revolute + `gear` (−z1/z2) | i = z2/z1, a = m(z1+z2)/2, η≈0.98 | contact ratio ≥1.2 (pgw); Lewis bending σ = F_t/(b·m·Y); z_min 17 (no undercut) or profile shift; printed m ≥ 1 [J] |
| 2 | **Helical pair** | + β | as 1 | axial thrust F_a = F_t·tanβ | thrust bearing needed |
| 3 | **Bevel pair** (90°) | m, z1, z2 | 2 revolute (⊥ axes) + gear | δ1 = atan(z1/z2) | axial thrust on both gears; shim for mesh |
| 4 | **Worm drive** | m, starts, q, z_wheel | 2 revolute + gear | i = z/starts. Self-locking if λ < φ (≈ 5–6° on plastics) [J], η = tanλ/tan(λ+φ) | heat; thrust on worm |
| 5 | **Rack & pinion** | m, z, rack length | revolute + slider + `rack` (r = m·z/2 mm/rad) | F = τ/r | rack length ≥ stroke + 2 teeth |
| 6 | **Compound gear train** | list of (z_a, z_b) stages | n revolute + gears | i = Π z_driven/z_driver | idlers reverse; shaft spacing check |
| 7 | **Planetary (simple)** | z_s, z_p, z_r, n_p | sun/carrier/ring revolute + 2 gear couplings | Willis: (ω_s−ω_c)/(ω_r−ω_c) = −z_r/z_s. Ring fixed: i = 1+z_r/z_s | z_r = z_s+2z_p; (z_s+z_r)/n_p integer; ring phase 180°/z_r [T] |
| 8 | **Compound/split-ring planetary** (3D-printed high ratio) | two rings z_r1, z_r2 | as 7 | same planet tooth count on both rings (profile-shifted): i = (1+z_r1/z_s)·z_r2/(z_r2−z_r1) | ratio 50–200:1 in one stage, popular for printed arms |
| 9 | **Cycloidal reducer** | N lobes, pins N+1, eccentricity e, pin r | input revolute, output revolute, gear (−1/N) | i = N (N lobes, N+1 pins) | pgw `CycloidGear` or an epitrochoid recipe; balance with 2 discs at 180°; output pins sized for bending |
| 10 | **Strain-wave (harmonic)** | flexspline z_f, circular z_c = z_f+2, wave generator ellipse | in/out revolute + gear | i = z_f/(z_c−z_f) | flexspline fatigue: printed TPU/PA works at low torque only [J]; pgw ring + spur + elliptical cam recipe |
| 11 | **Differential (bevel)** | z_side, z_spider | 3 revolute + 2 gear | ω_c = (ω_L+ω_R)/2 | open diff for a rover; for a robot wrist use 12 |
| 12 | **Differential wrist (2 bevels driven)** | z | pitch = (θ1+θ2)/2, roll = (θ1−θ2)/2 | both motors share load | ROM coupling noted |
| 13 | **Open belt drive (GT2/HTD)** | z1, z2, C or belt teeth | 2 revolute + `belt` (z1/z2) | i = z2/z1, L belt, wrap, teeth in mesh | teeth in mesh ≥ 6; tensioner or slotted mount; belt pull F = τ/r × (1 + tension factor) on bearings |
| 14 | **Belt-driven linear axis** | pulley z, rail length | revolute + slider + rack-type coupling (r = PD/2) | v = ω·PD/2 | belt stretch → stiffness; endstops |
| 15 | **Lead screw (Tr8x8, Tr8x2)** | d, lead p, length, nut | revolute + slider + `screw` (p/2π mm/rad) | F = 2π·η·τ/p, η = tanλ/tan(λ+φ) (Tr8x2 ≈ 0.3–0.4, Tr8x8 ≈ 0.6–0.7) [J] | self-locking if η < 0.5; buckling (Euler) of a long screw; critical speed |
| 16 | **Ball screw (SFU1204/1605)** | d, p | as 15 | η ≈ 0.9; backdrivable → brake | buckling, critical speed n_c = f·d/L² |
| 17 | **Linear rail + carriage (MGN9/MGN12)** | rail, length, carriages | slider | load ratings C, M_x/M_y/M_z | 2 carriages for moment loads |
| 18 | **V-slot wheels (OpenBuilds)** | extrusion, wheels | slider | — | bd_warehouse geometry |
| 19 | **Slider-crank** | r crank, l rod, offset | 2 revolute + slider + **loop** | x(θ) = r cosθ + sqrt(l² − (r sinθ − e)²); stroke ≈ 2r | l/r ≥ 3 [J]; transmission angle |
| 20 | **Scotch yoke** | r | revolute + slider + slot contact (modelled as rack-like curve coupling) | x = r sinθ (pure harmonic) | slot wear; friction |
| 21 | **Four-bar (crank-rocker, double-rocker, parallelogram)** | a,b,c,d, coupler point | 4 revolute, 1 **loop** | Freudenstein; output curve sampled into `transmission.curve` | Grashof s+l ≤ p+q; transmission angle 40–140°; toggle positions |
| 22 | **Parallelogram linkage** (keeps orientation) | L, h | 4 revolute, loop | output stays parallel | singular at collinear; add a 3rd bar |
| 23 | **Watt / Chebyshev straight-line** | standard ratios | 4 revolute | near-straight path | for suspension/legs |
| 24 | **Pantograph** | scale k | 5–6 revolute, loops | output = k × input | — |
| 25 | **Theo Jansen leg** | Jansen's 11 "holy numbers" scaled | 1 input crank + 8 rev, 2 loops (12 links) | foot path sampled; stride, lift | coupled pair at 180° for walking; crank torque from foot load (virtual work) |
| 26 | **Klann leg** | Klann ratios | 6 links, loops | foot path | as 25 |
| 27 | **Geneva drive** | slots n, crank r | 2 revolute + intermittent coupling (curve) | dwell fraction (n−2)/(2n)… indexed 360/n | wheel slot clearance; impact at engagement |
| 28 | **Cam + follower (flat/roller, translating/oscillating)** | base r, lift profile (cycloidal / 3-4-5 poly) | revolute + slider/revolute + curve coupling | s(θ) | pressure angle ≤ 30° translating [J, Norton]; undercut (curvature); spring preload |
| 29 | **Ratchet & pawl** | teeth, pawl | revolute (one-way) | — | spring |
| 30 | **Hinge (pin + bushing/bearing)** | pin d, knuckles, ROM | revolute with limits | — | pin shear τ = F/(n·πd²/4); bearing stress on printed lug F/(d·t) |
| 31 | **Living hinge / flexure** (PP, PETG, TPU) | t, L | revolute (compliant, k = EI/L) | stiffness | strain ε = t/(2R) < allowable |
| 32 | **Ball joint (socket)** | ball d, cone ROM | ball with cone limit | — | snap-fit retention; wear |
| 33 | **Universal joint (Cardan)** | yoke size, max angle | 2 revolute (⊥) | ω2/ω1 fluctuates with angle β; double U-joint for CV | max β ≈ 30° [J] |
| 34 | **Servo pan-tilt** | servo, bracket | 2 revolute | — | CoM over pan axis |
| 35 | **Parallel-jaw gripper (rack + 2 jaws)** | stroke, jaw | revolute + 2 sliders + rack couplings | F_grip = τ/r per jaw | jaw deflection |
| 36 | **Linkage gripper (4-bar fingers)** | finger lengths | loops | force vs opening curve | — |
| 37 | **Quadruped leg, hip-knee coaxial at hip, belt to knee** (Mini-Cheetah style) | thigh, shank, belt ratio | hip-ab revolute, hip-flex revolute, knee revolute + belt coupling | knee motor in hip: shank inertia low | belt tension; knee ROM |
| 38 | **Quadruped leg, knee via 4-bar/pushrod** (Go1-style) | link lengths | loop | knee τ(θ) via the transmission curve | singularity near full extension |
| 39 | **Humanoid ankle, 2 linear actuators (parallel)** | rod points | 2 slider + 2 ball + universal, loops | Jacobian map | — |
| 40 | **Wheel + hub motor / wheel + gearmotor + suspension (rocker-bogie)** | wheel d, motor | revolute (+ rocker revolute) | v = ω·r, F_trac = τ/r ≤ μ·N | climb height ~ r for rocker-bogie |
| 41 | **Prop + motor mount** (drone) | prop, motor | revolute | thrust from the catalog | already in the catalog |
| 42 | **Snap fit (cantilever)** | t, L, deflection | fixed | ε = 1.5·t·y/L² ≤ ε_allow (PETG ~ 4%, PLA ~ 2%) [J, BASF snap-fit guide] | — |
| 43 | **Clamp / shaft collar / bearing housing (pillow block, flange)** | bearing | fixed + revolute | — | bd_warehouse bearings + `PressFitHole` |

Mobility check for every card: Kutzbach M = 6(n−1) − Σ(6−f_i). For planar ones use 3(n−1) − 2j1 − j2. Report it. A mismatch with the intended DOF is a fail.

### 2.3 Sizing formulas shipped in `mises.mech.sizing` (pure functions, unit-tested)

- **Gear bending (Lewis):** σ = F_t·K_v/(b·m·Y), F_t = 2τ/d. Y(z) comes from the Lewis table (Y(20)=0.32 at 20° full depth). Use the polymer allowable ≈ material allowable × 0.5 for fatigue [J]. Printed spur teeth commonly fail at m=1, b=6 mm in PLA at roughly 1–2 N·m on a 20T pinion [J]. Compute it rather than trust that.
- **Contact ratio:** py_gearworks `get_contact_ratio()`; require ≥ 1.2.
- **Belt:** effective pull F_e = τ/r. Rated GT2 6 mm working tension ≈ 100–150 N [V Gates tables, approximate]. Check that teeth-in-mesh ≥ 6, otherwise skip risk.
- **Screw:** F = 2π·η·τ/p. Back-drive happens if tanλ > μ. Euler buckling F_cr = π²EI/(K·L)². Critical speed per the THK/Hiwin formula.
- **Shaft:** τ_max = 16T/(πd³); bending σ = 32M/(πd³); combined von Mises vs yield/SF; deflection at the gear ≤ 0.01·m [J]; keyway or D-flat stress concentration Kt ≈ 2 [J].
- **Bearing:** L10 = (C/P)³ × 10⁶ rev. Static: P0 ≤ C0/s0 (s0 ≥ 1). Use the catalog C and C0 (already in bearings.json for C).
- **Pin / lug:** double shear τ = F/(2·πd²/4); printed lug bearing stress F/(d·t) ≤ allowable; tear-out (edge distance ≥ 1.5d) [J].
- **Linkages:** the transmission angle μ(θ) is computed over the input range. The output torque curve comes from virtual work: τ_out = τ_in·(dθ_in/dθ_out), with the derivative taken numerically from the loop solution.

### 2.4 Assembly-model changes needed (E27)

1. **New joint kinds** in `assembly/model.py`: `universal` (2 DOF) and the coupling kinds `gear`, `rack`, `screw`, `belt`. A coupling joint references two existing joints plus a `ratio`, so it adds a constraint, not a body pair. `solve.py` applies it after the tree solve (value_b = ratio × value_a). `motion.py` drives both. `assembly_animate` then shows real gear and belt motion.
2. **Closed loops.** Keep the tree solve for the spanning tree. Mark each extra joint as a `loop_connect` and solve the loop with `scipy.optimize.least_squares` on the free joint values (scipy is already in core through build123d). If the residual is > 1e-6 mm, report "loop cannot close at this input (toggle/out of range)". Sweep the input to produce the transmission curve and the ROM. MuJoCo does the same thing in dynamics (`<equality connect>`; four-bar error 0.002° [T]).
3. **Joint design metadata** on `Joint` (optional fields): `bearing` (catalog id or "bushing"/"printed"), `shaft` (d, material), `retention` ("snap ring", "nut", "shoulder screw", "press"), `clearance_mm`, `actuator` (catalog id + ratio), `rom_deg` (the design ROM, which can differ from the hard limits). The `assembly_check` tool flags revolute joints with no bearing or bushing that carry load, and shafts without retention.
4. **ROM sweep** already exists (`assembly_check(sweep=...)`, an OCCT exact common at each step). Add (a) a **multi-joint sweep** (a Latin-hypercube set of poses across all joints of a limb), and (b) a **fast pre-sweep in MuJoCo** with convex-hull collision (CoACD) at 1000+ poses, then confirm the hits with OCCT. MuJoCo contacts excluded by default between parent and child must be explicitly *included* for a ROM check (use a `contype`/`conaffinity` collision layer for ROM).

---

## 3. Practical engineering checks (E29, E30)

Every check is a pure function in `mises.engineering.checks` (unit-testable) plus a tool that writes a `Requirement` result (pass / warn / fail / not_checked) into the spec. That is what makes them part of "done".

### 3.1 Actuator sizing

- **Static worst case** (no sim needed, runs first): τ_static = Σ m_i·g·r_i⊥ over everything distal to the joint, at the worst pose in the ROM. The pose sweep comes from the assembly solve and the mass from `mass.py`. Multiply by an impact/dynamic factor 1.5–2 for legs [J].
- **Dynamic** (`dyn_torque`): the trajectory is given (a gait, squat, reach, or `assembly_animate` keyframes). Run inverse dynamics (`mj_inverse`) for the *ideal* torque. Then run a forward sim with the real actuator model (forcerange = stall torque, gear, kp/kv, armature) to confirm it can *track* it. Report per joint: peak, RMS (sets the thermal limit), peak speed, peak power τ·ω, and the operating points on the motor's torque–speed line τ_avail(ω) = τ_stall·(1 − ω/ω_0).
- **Margins** (defaults [J]): peak ≤ 0.6·stall for hobby servos and ≤ 0.8·peak for BLDC; RMS ≤ rated (continuous); speed ≤ 0.8·no-load at the needed torque.

Spike numbers [T] (3 kg quadruped, 0.12 m thigh and shank, squat 0.20→0.13→0.20 m):

| Squat period | Hind knee peak, servo (forward sim) | Hind knee peak, `mj_inverse` | Hip peak (servo) |
|---|---|---|---|
| 2.0 s | 0.84 N·m | 0.79 N·m | 0.12 N·m |
| 1.0 s | 1.00 | 0.79 | 0.13 |
| 0.5 s | 1.62 | 0.88 | 0.25 |
| 0.3 s | 3.25 | 1.39 | 0.50 |

The quasi-static hand estimate (W/4 × knee lever) was 0.643 N·m. That matched the *front* knees (0.638) but not the hind ones (0.84), because the CoM sits 11 mm aft. A per-leg estimate misses this; the sim catches it. The gap between the servo and `mj_inverse` columns at fast motions is PD tracking effort (overshoot fighting). That is exactly what a stiff hobby servo will draw, so **report both**. Verdict for this model: an MG996R (1.08 N·m @6 V) at the knee fails the 0.6×stall margin even for a slow squat. An XM430 (4.1 N·m) passes up to a 0.5 s squat. This is the kind of call the agent should now make with numbers.

### 3.2 Power budget and battery sizing

```
P_avg = Σ_actuators (τ_rms·ω_rms/η_motor + I_idle·V) + Σ_electronics P_typ
E_needed_Wh = P_avg × runtime_h / (DoD 0.8) / η_dcdc 0.9
I_peak = Σ I_peak(actuators, simultaneous factor) + electronics
battery: pick from the table with E ≥ E_needed and C_rating·Ah ≥ I_peak·1.25
```

Servo current comes from torque with a linear model: I ≈ I_idle + (τ/τ_stall)·(I_stall − I_idle) [J]. For BLDC use I = τ/(K_t·ratio·η). LiPo pack specific energy is about 130–170 Wh/kg. 18650/21700 Li-ion packs are about 180–230 Wh/kg but deliver less current per cell [J/2].

### 3.3 Center of mass and stability

- Assembly CoM = Σ m_i·c_i, with c_i from the B-rep centroid (`part.center()`) transformed by the instance transform. Catalog parts use their envelope centroid. Report it in the UI as a marker.
- **Static stability:** take the support polygon (convex hull of the ground contacts: feet, wheels) and the margin of the CoM projection to the hull edges. The static tip angle is atan(margin / h_CoM). The spike gives a 90 mm margin, h = 168 mm, and **28.2°** [T].
- **Along a trajectory:** compute the ZMP from MuJoCo contact forces (the CoP of `d.contact` normal forces) at each step. Margin ≥ 10–20 mm [J]. Wheeled: tip-over on a slope (atan(track/2 / h)) and under acceleration (a_max = g·margin/h).
- A tip-over test in sim: tilt the floor by increasing angles and find where the torso rolls. Cheap (<0.1 s per angle).

### 3.4 ROM collision sweep

See §2.4. Report the colliding pair, the joint values and the overlap volume. Feed the render with the colliding pose highlighted (E33).

### 3.5 Cable routing (E29)

A `cable_route` tool:
- **Input:** a list of nets (battery → BMS → switch → PDB → ESC/driver → motor; signal nets), with the gauge chosen from current (table below).
- **Path:** a graph search (A* on a voxel grid of the assembly's free space, 5 mm voxels, computed from the existing `inner_volume`/section machinery) between connector ports. Joints get a **service loop**: extra length = r_bend × ROM_rad + 20 mm, and the path must pass within 15 mm of the joint axis so bending is minimal [J].
- **Output:** a `cable_path()` sweep (the recipe exists in `mises_shapes`), lengths for the BOM, a bend-radius check (min bend ≈ 6–10× cable OD [J]), and a clash check against moving parts across the ROM.

Wire ampacity (conservative, chassis wiring in a bundle; derived from typical MIL-W-5088/silicone hobby ratings [J/2]): 26 AWG 2.2 A · 24 AWG 3.5 A · 22 AWG 5 A · 20 AWG 7.5 A · 18 AWG 10 A · 16 AWG 13 A · 14 AWG 20 A · 12 AWG 30 A · 10 AWG 45 A. Silicone wire mass is about 22 AWG 3.6 g/m, 18 AWG 8 g/m, 14 AWG 21 g/m and 12 AWG 30 g/m [2].

### 3.6 Electronics estimator (E17)

`electronics_estimate(spec)` maps requirements to components. Each pick is a catalog item with envelope, mass, power and mount pattern, plus the reason it was chosen. The result goes to `internals_pack` to reserve space, and to the BOM.

Selection rules [J]:
- **Controller:** use Raspberry Pi 5 (or Jetson) only if the spec needs vision, SLAM or ROS. Use ESP32-S3 if it needs Wi-Fi/BLE and ≤ 16 PWM. Use Teensy 4.1 for hard real-time multi-axis control. Use an STM32/flight controller for drones.
- **Servo count > 8 PWM:** add a PCA9685, or move to a bus-servo adapter (U2D2 or Feetech URT).
- **Steppers:** one TMC2209 per axis. **BLDC joints:** moteus/ODrive. **Brushed:** TB6612 below 1.2 A, DRV8871 below 3.6 A, BTS7960 below 40 A.
- **Battery:** from §3.2. Above 3S, add a power switch rated for the current (XT90-S anti-spark at > 30 A) and an e-stop for anything above 24 V or with legs.
- **5 V rail:** buck sized at 1.5× the sum (servo rails need their own 6–7.4 V BEC: never power 4+ servos from the logic rail).
- **Radio:** an ExpressLRS receiver for RC; Wi-Fi for an onboard computer; an RFD900x/SiK for long-range telemetry. Antennas must not be inside a CF or metal enclosure (CF is conductive and blocks RF). Keep ≥ λ/4 clearance: 31 mm at 2.4 GHz, 82 mm at 915 MHz.
- **GPS:** outdoor only, on top, with a sky view, the compass ≥ 50 mm from high-current wires. **IMU:** near the CoM, soft-mounted for drones.
- **Thermal:** see §3.7.

Component table (for the catalog; L×W×H mm, mass g, power):

| Category | Item | Size | Mass | Power / rating | Mount | Source |
|---|---|---|---|---|---|---|
| SBC | Raspberry Pi 5 | 85×56×17 (+~10 for Active Cooler) | 46 | 3–12 W, 5 V 5 A supply | 4×M2.5, 58×49 | [V](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-product-brief.pdf) |
| SBC | Raspberry Pi Zero 2 W | 65×30×5 | 10 | 0.6–3 W | 4×M2.5, 58×23 | [V](https://datasheets.raspberrypi.com/rpizero2/raspberry-pi-zero-2-w-product-brief.pdf) |
| SBC | Jetson Orin Nano dev kit | 103×90.5×35 | ~180 (with cooler) [2] | 7–25 W | — | [V NVIDIA] |
| MCU | ESP32-S3-DevKitC-1 | 63×25.5×9 (with headers) | 7 | 0.2–1.2 W (Wi-Fi TX peaks ~350 mA) | none (headers) | [V Espressif] |
| MCU | Teensy 4.1 | 61×18×4 | 5 [2] | ~0.5 W (100 mA @5 V) | none | [V PJRC](https://www.pjrc.com/store/teensy41.html) |
| MCU | STM32 Nucleo-64 | 70×82.5 | 30 [2] | <0.5 W | 4 holes | [V ST] |
| MCU | Arduino Nano | 45×18 | 7 | <0.3 W | exists | catalog |
| FC | 30.5×30.5 FC (F7/H7) | 36×36×8 | 8 | 1 W | M3, 30.5 | exists as stacks |
| Servo driver | PCA9685 16-ch | 62.5×25.4×3 | 9 | logic only | 4×M2.5 | [V Adafruit] |
| Stepper driver | TMC2209 StepStick | 20.3×15.2×12 (with heatsink) | 3 | 2 A rms | socket | [V Trinamic] |
| Brushed driver | TB6612FNG breakout | 20×20 | 2 | 1.2 A/ch | — | [V] |
| Brushed driver | BTS7960 module | 50×50×43 | 66 | 43 A | 4×M3 | [2] |
| BLDC FOC | moteus r4.11 | 46×53 | 14 [2] | 10–44 V, 100 A peak phase | — | [V mjbots](https://mjbots.com/products/moteus-r4-11) |
| BLDC FOC | ODrive S1 | 66×50 | ~40 [2] | 12–48 V, 40 A cont / 80 A peak | 4 holes | [V ODrive](https://odriverobotics.com/shop/odrive-s1) |
| ESC | 30 A single (BLHeli) | 45×24×10 | 25 | 30 A | — | [2] |
| ESC | 4-in-1 45 A | 36×36×6 | 15 | 4×45 A | 30.5 | exists as stacks |
| Bus adapter | ROBOTIS U2D2 | 48×18×14.6 | 9 | — | — | [V] |
| Radio | ExpressLRS RX (nano) | 10×10×3 + antenna | 0.6–1.5 | — | — | [2] |
| Radio | RFD900x | 30×57×12.8 | 14.5 | 1 W TX | — | [V RFDesign] |
| GPS | M10 GPS + compass puck (e.g. Holybro M10) | Ø50×14.5 | 32 | 0.2 W | — | [V Holybro] |
| GPS | 20×20 M10Q module | 20×20×8 | 6 | 0.15 W | — | [2] |
| IMU | BNO085 breakout | 27×20×4 | 3 | 0.05 W | 4×M2.5 | [V Adafruit] |
| IMU | MPU-6050 GY-521 | 21×16×3 | 2 | — | 2 holes | [2] |
| BMS | 3S/4S 20 A | 50×20×3.5 | 5 | 20 A | — | [2] |
| Buck | 5 V 5 A (e.g. Pololu D36V50F5) | 25.4×25.4×7 | 7 [2] | 5 A | — | [V Pololu](https://www.pololu.com/product/4092) |
| UBEC | 5–6 V 3 A | 30×18×8 | 10 | 3 A | — | [2] |
| Switch | XT90-S anti-spark / rocker 20 A | — / 21×15 | 15 / 5 | — | — | [2] |
| Connector | XT30 / XT60 / XT90 (pair) | 10×5×15 / 16×8×16 / 21×11×21 | 2 / 5 / 12 [2] | 15 / 30 / 45 A cont | — | [V AMASS] |
| Connector | JST-XH / PH / GH | 2.5 / 2.0 / 1.25 mm pitch | <1 | 3 / 2 / 1 A | — | [V JST] |

LiPo packs (typical; size L×W×H mm, mass g) [2, vendor listings: Turnigy/GNB/CNHL ranges]:

| Pack | Size | Mass | Wh |
|---|---|---|---|
| 1S 450 mAh | 55×20×7 | 12 | 1.7 |
| 2S 1000 mAh | 70×35×14 | 65 | 7.4 |
| 2S 2200 mAh | 105×34×18 | 125 | 16.3 |
| 3S 1300 mAh | 72×35×25 | 110 | 14.4 |
| 3S 2200 mAh | 105×34×24 | 188 | 24.4 |
| 3S 5000 mAh | 145×49×26 | 410 | 55.5 |
| 4S 1500 mAh | 75×36×34 | 175 | 22.2 (exists) |
| 4S 2200 mAh | 105×34×33 | 245 | 32.6 |
| 4S 5000 mAh | 145×49×33 | 545 | 74 |
| 6S 1300 mAh | 75×36×46 | 210 | 28.9 (exists) |
| 6S 5000 mAh | 155×48×50 | 750 | 111 |
| Li-ion 18650 cell (e.g. Molicel P28A) | Ø18.4×65.2 | 46–48 | 2.8 Ah, 35 A [V Molicel] |
| Li-ion 21700 cell (e.g. Molicel P45B) | Ø21.3×70.3 | 70 | 4.5 Ah, 45 A [V Molicel] |

For in-between capacities, scale linearly: about 6.7 g per Wh for LiPo packs (~150 Wh/kg), about 5 g per Wh for Li-ion packs, and about 2.2 cm³ per Wh [J, fitted from the rows above].

### 3.7 Thermal for electronics

Two tiers:
1. **Lumped (instant):** T_in = T_amb + P/(h·A_enclosure), with h = 5–10 W/m²K for natural convection and 25–50 W/m²K with a fan [J]. Example: a Pi 5 at 5 W in a 100×70×40 mm box (A ≈ 0.028 m²) gives ΔT ≈ 18–36 K. Flag it when T_in > max_service of the enclosure material (PLA 55 °C fails, PETG 68 °C is marginal) or when the board limit is close (Pi 5 throttles at 85 °C SoC [V]). Motor case temperature: P_loss = I_rms²·R + iron losses; servo continuous stall is a fail.
2. **CalculiX steady thermal** (exists, `sim/thermal.py`): heat input on the board's mount bosses, a film coefficient on the outer faces, and the peak checked against HDT. The agent runs it when tier 1 warns.

### 3.8 Clash report (E33)

`clash_report(project_id)` runs at the end of every build (in the "done" gate). It reports:
- **Overlaps:** pairs with common volume > 1 mm³ (or > 0.1% of the smaller part). It uses the existing `run_geometry(want="interference")` with a bbox prefilter, ignores declared press fits (bearing in seat, shaft in bore within the fit tolerance), and gives each its own render crop.
- **Stray parts:** connected components of the "touching or jointed" graph (bbox gap < 1 mm, or a joint). Any component other than the main one, or any instance farther than 1.5× the main component's bbox diagonal from its centroid, is "stray". Also: instances at the exact origin with no joint, while other instances of the same build sit elsewhere (the classic "forgot to place it" case).
- **Unsupported parts:** an instance with no joint, not fixed, and not touching anything.
- **Output:** a JSON list plus one full-assembly render (4 views) with overlaps tinted red and strays circled. The lead must clear it, or justify each item, before it reports done.

---

## 4. Simulation integration and accuracy (E30)

### 4.1 Engine choice

| Engine | Licence | macOS arm64 wheel (py3.13) | Fit | Verdict |
|---|---|---|---|---|
| **MuJoCo** 3.17 ([github](https://github.com/google-deepmind/mujoco), [model editing](https://mujoco.readthedocs.io/en/stable/programming/modeledit.html)) | Apache-2.0 | **yes** [T] | Fast contact dynamics, `mj_inverse`, equality loops, tendons, actuators with gear/forcerange, `MjSpec` programmatic building, URDF import | **Use** |
| Pinocchio 4.1 (`pin`) | BSD-2 | yes [T dry-run] | Fast RNEA/ABA, analytic derivatives, no contact sim | Optional, for later trajectory optimisation |
| Drake 1.58 (`drake`) | BSD-3 | yes [T dry-run], large (~200 MB+) | Rigorous multibody + optimisation (contact-implicit, IK) | Not now: heavy |
| PyBullet | zlib | **no wheel**, source build only [T] | Older, slower contact | Skip |
| dm_control | Apache-2.0 | its deps failed to resolve with binary-only [T] | MJCF composer | Not needed: `MjSpec` covers it |

Support libraries: **CoACD** 1.0.14 (MIT) convex decomposition [T]; **trimesh** 5.2 (MIT) [T]; obj2mjcf (MIT) optional.

### 4.2 Assembly → MJCF exporter (`mises.dyn.export`)

```
export_mjcf(project_id, *, root="auto", floor=True, collision="hulls"|"boxes"|"none",
            actuators="catalog", rom_contacts=False) -> MjBundle{xml, meshes/, map.json}
```

1. **Tree:** the assembly joints form a graph. Pick the root as the fixed instance (fixed base) or the heaviest/torso instance with a `<freejoint>` (mobile robot). Do a BFS to make the spanning tree. Bodies are instance *groups* merged across `fixed` joints, so each MuJoCo body is a rigid cluster and its inertia is the combined B-rep inertia. Loop-closing joints become `<equality connect>` (revolute loops) or `<equality weld>` with relaxed DOF. Coupling joints (`gear`/`belt`/`rack`/`screw`) become `<equality joint joint1 joint2 polycoef="0 ratio 0 0 0">` or a fixed `<tendon>`.
2. **Frames:** the body pose comes from the solved world transforms (mm → m). The joint `pos`/`axis` come from the assembly `Frame` (origin + axis, or a resolved `face_id`) expressed in the child body frame. Limits come from `Joint.limits` (deg → rad), with `range` and `limited`.
3. **Mass properties from the B-rep.** In the build worker, compute OCCT `BRepGProp.VolumeProperties_s` (volume, CoM, `MatrixOfInertia` about the CoM) × the density of the part's material, and store it in `metrics.json` as `inertia_kgm2` and `com_mm`. Catalog parts use the catalog mass, with the envelope's inertia scaled to that mass. Write `<inertial pos mass fullinertia>` (`explicitinertial`). The spike shows mesh-derived inertia drifts 1–2% [T]. Infill matters: for an FDM part, use the shell+infill effective density from `fdm_factors`. It is a mass, not a strength, factor, and it is approximate.
4. **Geometry:** a visual mesh (STL from the worker at 0.1 mm tolerance) with `contype=0`. Collision: `hulls` (CoACD, threshold 0.05, cached per part hash), `boxes` (the OBB of each part, the cheapest), or a primitive for feet (sphere) and wheels (cylinder). Parent–child pairs are excluded by default. With `rom_contacts=True`, put links in a ROM collision layer so adjacent covers are checked.
5. **Actuators:** read the joint's `actuator` (catalog). For a servo, use `<position kp kv forcerange=±stall>` with `armature = rotor_inertia·ratio²` (default 1e-4–5e-3 kg·m² when unknown [J]) and `damping`. For BLDC, use `<motor gear=ratio ctrlrange=±peak>`. Steppers get a position actuator plus a torque-speed check afterwards. Always use `integrator="implicitfast"`, timestep 1 ms. The four-bar went NaN in 6.5 ms without armature/implicit [T].
6. **Contacts:** floor friction 0.8–1.0 for rubber feet and 0.3–0.5 for PLA on wood [J]. Use the defaults for `solref`/`solimp`, documented as approximate.
7. **`map.json`:** the MuJoCo body/joint/actuator ↔ Mises instance/joint ids, so results are written back by id.

Exporting URDF as well is cheap from the same tree and is useful to users (ROS). Do it later.

### 4.3 What gets computed (tools)

| Tool | What | Cost [T-based estimate] |
|---|---|---|
| `dyn_build(project_id)` | export + compile + validate (mass totals vs `mass.py`, every joint mapped, no initial penetration > 1 mm) | <1 s plus CoACD on first run (~1 s per concave part, cached) |
| `dyn_torque(project_id, trajectory)` | inverse dynamics + forward tracking. Trajectory: keyframes per joint, a task-space target (IK via `mj_jac` damped least squares), or a named gait (`squat`, `stand_up`, `trot(period, step)`, `reach(target)`) | 3 s of motion ≈ 0.02–0.04 s for 8 DOF [T]; 100 DOF humanoid still < 1 s |
| `dyn_stability(project_id, trajectory=None)` | static margin + tip angle; ZMP/CoP margin along a trajectory; floor-tilt tip test | <0.1 s |
| `dyn_reactions(project_id, trajectory)` | per joint per step: the force/moment transmitted (`mj_rnePostConstraint` → `cfrc_int`, shifted to the joint anchor). Peak per link, with the time and pose | ms |
| `dyn_rom_sweep(project_id, joints, n=2000)` | hull-collision sweep via `mj_forward` + `d.ncon` | ~ms per pose |
| `dyn_animate(project_id, trajectory)` | writes the trajectory as an assembly animation (E5) so the user sees it play | — |

### 4.4 Dynamics → FEM loop (integrated)

```
dyn_reactions → for each link L, peak step t*:
   joint A (where the load enters): F_A, M_A in L's part frame (world→part via instance transform)
   joint B (where L is held): support
 → LoadCase(name=f"{L} @ {gait} t={t*:.2f}s",
            supports=[Support(kind="pinned", face=<bore face at B>, axis=<joint B axis>)],
            loads=[BearingLoad(face=<bore at A>, newtons=|F_A⊥|, direction=nearest axis) or
                   RemoteForce(face=<bore at A>, newtons=|F_A|, direction, point=anchor A),
                   MomentLoad(face=<bore at A>, n_mm=|M_A·axis-free part|, axis=...)])
 → sim_run (static, existing CalculiX path) → SF, deflection
 → spec requirement "min_safety_factor" per link (pass/fail) → if fail: study_run on the
   link's VARS (wall, rib depth, section) with the same load case
```

Notes:
- The reaction at a revolute joint has no moment about the joint axis (that is the actuator torque). The actuator torque goes in as a `MomentLoad` on the actuator mount face of the link that carries the motor.
- `Direction` today is an axis enum (`+x`…`-z`). A dynamic reaction is an arbitrary vector, so **add a `direction_vec: Vec3`** to `ForceLoad`/`RemoteForce`/`BearingLoad`, or decompose into up to 3 axis-aligned loads (works today, but is clunky).
- Verified [T]: on the 2-link arm, `cfrc_int` shifted to the anchors gives 9.81 N / 1.9865 N·m (link 1) and 4.905 N / 0.515 N·m (link 2). Both are exact.
- Impacts: gait touchdown peaks depend on the contact softness (`solref`). Report them as "estimate", and clamp at the `loads_impact` F = m·g·k model already in `engineering/loads.py` for consistency.

### 4.5 Accuracy notes the agent must state [J]

- Rigid bodies: link compliance and backlash are ignored. Add backlash later as a joint `frictionloss`/deadband. Hobby-servo tracking is approximated with PD, kp ~ stall/0.1 rad.
- Contact: MuJoCo uses soft contacts, so forces at impact are model-dependent. Static and quasi-static cases are accurate (exact in the spike).
- Mass: B-rep × density is exact for solid parts. FDM infill and fasteners/wiring (+5–10% [J]) are approximate, so add a "wiring + fasteners" lump.

---

## 5. Materials (E32)

### 5.1 What exists

`core/src/mises/sim/materials.py`: a frozen `Material` dataclass (E, ν, yield, density, HDT, k, α, knockdown, orthotropic FDM Z data, S-N, calibration). The built-ins are PLA, PETG, ABS, ASA and PA-CF (PAHT-CF). `FDM_KNOCKDOWN = 0.7` applies to every material. `mass.py` reads densities from here, then the slicer table (TPU). Custom materials are saved as JSON.

### 5.2 Shape changes needed

```python
Process = Literal["fdm", "sls_mjf", "machined", "sheet", "laminate", "cast"]
@dataclass(frozen=True)
class Material:
    ...existing...
    process: Process = "fdm"
    uts_mpa: float | None = None          # ultimate (yield_mpa stays the design strength)
    elongation_pct: float | None = None
    failure: Literal["ductile", "brittle"] = "ductile"   # brittle -> max-principal vs UTS
    max_service_c: float | None = None    # continuous use temp; HDT stays for short-term
    fatigue_limit_mpa: float | None = None  # metals: endurance/5e8 strength
    hyperelastic: dict | None = None      # TPU: {"model": "neo_hooke", "c10": ..., "d1": ...}
    printable: bool = True                # agent's per-part pick: printed vs bought/machined
    cost_per_kg: float | None = None
    notes: str = ""
# knockdown default: FDM_KNOCKDOWN for process=="fdm", 0.85 sls_mjf [J], 1.0 otherwise
```

`with_fdm` and `with_anisotropy` must refuse non-FDM materials. The deck writer already handles `*ELASTIC` (iso/engineering constants). Add `*HYPERELASTIC, NEO HOOKE` for TPU, valid only in nonlinear studies. A linear TPU study gets a "linear model, small strain only" warning. Add a `material_pick(part_id, requirements)` helper for the agent: stiffness/mass index E/ρ, strength/mass σ/ρ, temperature, process available, cost. It returns the ranked candidates with the reason.

### 5.3 Property table

Printed values are XY (in-plane) unless noted. Z = across layers. Metals are typical minimums for the temper. For FDM, use these as the base values and the code applies the knockdown.

| Name | Process | E (MPa) | ν | Yield / design σ (MPa) | UTS (MPa) | ρ (g/cm³) | HDT / max service (°C) | k (W/mK) | α (1/K) | Z data / notes | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PLA | fdm | 2300 | 0.35 | 51 | — | 1.24 | HDT 55 | 0.13 | 6.8e-5 | exists | Prusament TDS |
| PETG | fdm | 1500 | 0.38 | 47 | — | 1.27 | 68 | 0.20 | 6.0e-5 | exists (Z 0.38) | Prusament TDS |
| ABS | fdm | 1900 | 0.35 | 35 | — | 1.04 | 80 | 0.17 | 9.0e-5 | exists | generic |
| ASA | fdm | 2000 | 0.35 | 40 | — | 1.07 | 90 | 0.17 | 9.5e-5 | exists | generic |
| **PC** (PC Blend) | fdm | 1900 | 0.37 | 55 (est.) | 63 (est.) | 1.22 | HDT ~110 / 100 | 0.20 | 6.5e-5 | Z/XY ~0.6 (typ.) | modulus [2 Filament2Print](https://filament2print.com/en/pc-polycarbonate/3457-prusament-pc-blend.html). Strength: **verify against the official Prusament PC Blend TDS** (not found in this search) |
| **PA12** | sls_mjf | 1800 | 0.39 | 48 (no clear yield) | 48 | 1.01 | HDT 95 @1.82 MPa / 80 | 0.25 | 9e-5 | MJF near-isotropic (Z ≈ XY) | [V HP 3D HR PA12 datasheet](https://www.hp.com/us-en/printers/3d-printers/products/materials/pa12.html) |
| **PA6-CF** (Bambu) | fdm | 4430 (Z 2170) | 0.35 | 102 XY / **48 Z** (use Z as design) | 102 | 1.17 [2] | HDT ~186 @0.45 MPa [2] / 120 | 0.30 | 4e-5 | E_z/E 0.49, Z/XY 0.47; dried and annealed, loses 30–50% when wet [V] | [V Bambu PA6-CF TDS](https://polyalkemi.no/wp-content/uploads/2024/01/Bambu_PA6-CF_Technical_Data_Sheet-v2.pdf) |
| PA-CF (PAHT-CF) | fdm | 3860 | 0.35 | 64 (Z) | 88 | 1.06 | 170 | 0.30 | 4e-5 | exists | Bambu TDS |
| **TPU 95A** | fdm | 26 (old UM TDS) – 67 (new UM XY) | 0.48 | 8.6 (at yield) | 24–39 | 1.22 | ~60–80 (soft) | 0.20 | 1.5e-4 | Z break 6.4 MPa. **Hyperelastic**: Neo-Hooke C10 ≈ E/6 ≈ 4–11 MPa [J]; elongation 580% | [V UM TDS 2017](https://www.utwente.nl/en/rpl/Data%20sheets/Ultimaker%203/TPU%2095A.pdf), [2 Wevolver current](https://www.wevolver.com/specs/ultimaker-tpu-95a) |
| **POM** (Delrin 150, machined) | machined | 3100 | 0.35 | 72 | 72 | 1.42 | HDT ~110 @1.8 MPa / 90 cont. | 0.31 | 1.1e-4 | low friction (gears, bushings); hard to print (warps) | [V DuPont Delrin 150 datasheet](https://www.dupont.com/products/delrin-150.html) (approx.) |
| **Al 6061-T6** | machined/sheet | 68900 | 0.33 | 276 | 310 | 2.70 | 150 (strength drops above) | 167 | 2.36e-5 | fatigue 96.5 MPa @5e8 | [V ASM/MatWeb](https://asm.matweb.com/search/SpecificMaterial.asp?bassnum=ma6061t6) |
| **Al 7075-T6** | machined | 71700 | 0.33 | 503 | 572 | 2.81 | 120 | 130 | 2.34e-5 | fatigue 159 MPa @5e8; not weldable | [V ASM/MatWeb](https://asm.matweb.com/search/SpecificMaterial.asp?bassnum=MA7075T6) |
| **Steel 1018 (cold drawn)** | machined | 205000 | 0.29 | 370 | 440 | 7.87 | 400 | 51.9 | 1.2e-5 | shafts, pins | [V MatWeb/AZoM](https://www.azom.com/article.aspx?ArticleID=6115) |
| **Steel 4140** annealed / Q&T (~HRC 30) | machined | 205000 | 0.29 | 417 / 655 | 655 / 1020 | 7.85 | 400 | 42.6 | 1.22e-5 | two entries; shafts, gears | [V AZoM 4140](https://www.azom.com/article.aspx?ArticleID=6769) |
| **304 stainless (annealed)** | machined/sheet | 193000 | 0.29 | 215 | 505 | 8.00 | 800 | 16.2 | 1.73e-5 | low yield; work-hardens | [V ASM/MatWeb](https://asm.matweb.com/search/SpecificMaterial.asp?bassnum=mq304a) |
| **Brass C360 (half hard)** | machined | 97000 | 0.31 | 310 | 385 | 8.50 | 200 | 115 | 2.05e-5 | bushings, inserts | [V Copper.org C36000](https://alloys.copper.org/alloy/C36000) |
| **CF plate (3K twill, epoxy, quasi-iso)** | laminate | 50000 (in-plane) | 0.30 | **brittle**: 450 (design 300) | 450–600 | 1.55 | Tg ~120 / 90 | 5 (in-plane) | 2e-6 | **plate only**: out-of-plane/interlaminar ~50 MPa; model as 2D shell or flag; conductive (blocks RF, galvanic with Al) | [J/2, typical vendor sheets e.g. [Dragonplate](https://dragonplate.com/)] |

Metal knockdown 1.0, with the spec's `min_safety_factor` (default 1.5 for metals, 2.0 for printed [J]) as the margin. CF plate uses `failure="brittle"` (Rankine on max principal) with SF ≥ 3 [J].

---

## 6. Recommended module designs

### 6.1 Dependencies (core/pyproject.toml, for the implementer)

```
"mujoco>=3.17",          # Apache-2.0; arm64 wheel; dynamics (mises.dyn)
"py-gearworks==0.0.24",  # Apache-2.0; bevel/ring/cycloid + mesh_to + contact ratio (pinned: 0.0.x)
"coacd>=1.0.14",         # MIT; convex decomposition for MuJoCo collision
"trimesh>=5.2",          # MIT; mesh IO for coacd (or read STL with numpy and drop trimesh)
# scipy already present via build123d (loop closure least_squares, ConvexHull)
```

All four are permissive. gmsh (GPL), CalculiX (GPL) and OpenFOAM (GPL) stay out-of-process, as today. Like the CAD worker, MuJoCo must run in a **child process** (the existing `sim/proc.py` pattern) so a bad model cannot crash the server, and the sandbox profile must allow it.

### 6.2 Packages

```
core/src/mises/mech/
  __init__.py
  card.py          # Port, MechJoint, MechanismCard (pydantic)
  drive.py         # gear_pair, helical_pair, bevel_pair, worm_pair, rack_pinion, ring_gear,
                   # planetary(zs, zp, zr, n, m, width) [auto phase + assembly condition],
                   # cycloid_reducer, strain_wave, gt2_pulley, htd_pulley, timing_belt,
                   # shaft, d_bore, clamp_hub, coupler, bearing_seat, motor_mount, servo_mount
  linkage.py       # four_bar, slider_crank, scotch_yoke, jansen, klann, pantograph, watt,
                   # chebyshev, parallelogram; loop solve (scipy) + transmission curve
  joints.py        # hinge, ball_socket, universal, flexure, snap_fit, pillow_block
  grippers.py      # parallel_rack, linkage_gripper
  legs.py          # quadruped_leg(kind="belt_knee"|"linkage_knee"), humanoid_ankle_2rod
  sizing.py        # lewis, contact ratio, belt, screw, shaft, bearing L10, pin/lug, snap-fit
  library.py       # registry: id -> builder, function tags, params schema, checks
  tools.py         # MCP tools (below)
core/src/mises/dyn/
  export.py        # assembly -> MjSpec (tree, loops, couplings, inertia, hulls, actuators)
  inertia.py       # B-rep GProp in the worker -> metrics.json {mass_kg, com_mm, inertia_kgm2}
  traj.py          # keyframes, IK (damped LS), named motions (squat, stand, trot, reach)
  run.py           # child-process runner (sim/proc.py pattern), results -> JSON + charts
  checks.py        # torque vs actuator curve, ZMP/margin, reactions, rom sweep
  fem_bridge.py    # reactions -> sim LoadCase on link parts (bore faces from joint frames)
  tools.py
core/src/mises/engineering/checks.py   # static torque, power budget, battery pick,
                                       # thermal lumped, cable route, clash report
core/src/mises/engineering/electronics.py  # requirements -> component picks (§3.6 rules)
core/src/mises/catalog/data/{actuators,drivers,radios,sensors,power,connectors}.json (+ rows)
```

### 6.3 Tools (MCP / ToolRegistry, same `@tool` pattern and safety classes)

| Tool | Safety | Args → Output |
|---|---|---|
| `mech_list` | READ | `function`, `constraints` (torque, ratio, stroke, size) → ranked mechanism ids + trade-offs |
| `mech_insert` | LOCAL_MUTATE | `project_id`, `mech_id`, `params`, `at: Frame`, `attach_to` → parts built, instances + joints (incl. couplings/loops) inserted, card |
| `mech_check` | READ | `project_id`, `group` → mobility, Grashof/transmission angle, contact ratio, Lewis SF, belt teeth-in-mesh, screw buckling, bearing L10 |
| `drivetrain_plan` | READ | joint → required τ/ω (from `dyn_torque` or static) → actuator + reduction options (direct, belt, planetary, cycloid) with mass/size |
| `actuator_size` | READ | `project_id`, `joint_ids`, `margin` → static worst-case τ per joint + pass/fail vs the actuator |
| `electronics_estimate` | READ | spec → component list with envelopes/mass/power + reasons; optional `reserve=True` calls `internals_pack` |
| `power_budget` | READ | actuators duty + electronics + runtime → Wh, I_peak, battery pick, wire gauges |
| `dyn_build` / `dyn_torque` / `dyn_stability` / `dyn_reactions` / `dyn_rom_sweep` | READ (writes results files only) | §4.3 |
| `dyn_to_fem` | LOCAL_MUTATE (creates sim records) | `project_id`, `trajectory`, `links` → load cases + `sim_run` per link, SF into spec |
| `thermal_estimate` | READ | enclosure part + internal components → T_in, flags; offer `sim_run(thermal)` |
| `cable_route` | LOCAL_MUTATE | nets → paths (sweep parts), lengths into BOM, bend/clash checks |
| `clash_report` | READ | `project_id` → overlaps, strays, unsupported + annotated renders; gated in the lead's "done" check |
| `material_pick` | READ | part + requirements → ranked materials (from §5) with E/ρ, σ/ρ, temp, process |

Lead-agent prompt changes [J]: at stage 3 (hardpoints/skeleton, from the 2026-10-30 design research) the engineer must run `actuator_size` + `electronics_estimate` + `power_budget` *before* skinning. Before "done", it must run `dyn_torque` on a representative motion, `dyn_stability`, `rom_sweep`, `dyn_to_fem` for the most loaded links, and `clash_report`, and it must state the margins in its final message.

### 6.4 Tests to write (TDD targets with known answers)

- Spur/planetary/bevel/worm interference = 0 at 3 phases. Planetary without the ring phase gives > 0 (guards the fix).
- GT2 20/60, 122T: center 80.997 ± 0.01 mm.
- Four-bar loop solve vs the Freudenstein analytic, < 0.01°. Grashof classification.
- 2-link arm `mj_inverse` vs hand formula (exact). Reactions 9.81 N / 1.9865 N·m.
- B-rep inertia of a box vs analytic (m(a²+b²)/12).
- Stability margin of a rectangle-foot box vs geometry.
- Power budget and battery pick on a fixture spec.
- Clash: two overlapping boxes, plus one box 10 m away → 1 overlap, 1 stray.

---

## 7. Sources

- bd_warehouse: https://github.com/gumyr/bd_warehouse (Apache-2.0; module list verified in the installed 0.4.0 wheel [T])
- py_gearworks: https://pypi.org/project/py-gearworks/ , https://gggears.readthedocs.io/en/latest/build123d_examples.html (Apache-2.0, from the wheel METADATA [T])
- cq_gears: https://github.com/meadiode/cq_gears , licence per https://awesome.ecosyste.ms/projects/github.com%2Fmeadiode%2Fcq_gears
- MuJoCo: https://github.com/google-deepmind/mujoco , https://mujoco.readthedocs.io/en/stable/programming/modeledit.html
- Bambu PA6-CF TDS: https://polyalkemi.no/wp-content/uploads/2024/01/Bambu_PA6-CF_Technical_Data_Sheet-v2.pdf
- Ultimaker TPU 95A: https://www.utwente.nl/en/rpl/Data%20sheets/Ultimaker%203/TPU%2095A.pdf , https://www.wevolver.com/specs/ultimaker-tpu-95a
- Prusament PC Blend (secondary): https://filament2print.com/en/pc-polycarbonate/3457-prusament-pc-blend.html
- Metals: ASM/MatWeb and AZoM pages linked in §5.3; Copper Development Association C36000.
- Actuators and electronics: vendor pages linked inline in §1.4 and §3.6. Rows tagged [2] are retailer typicals and should be verified per SKU when a catalog row is added.
- Spike code and results: `/private/tmp/claude-501/-Users-hartigan-Documents-Claude-Projects/8b6e121f-d432-4043-912f-dff97e8094a0/scratchpad/p10-mech/` (`spike_mech.py`, `spike_mech2.py`, `ring_phase.py`, `spike_mujoco.py`, `spike_mujoco_fast.py`, `fourbar.py`, `react.py`, `mjspec_t.py`, `coacd_t.py`, `*_results.json`).
