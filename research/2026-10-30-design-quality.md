# Design quality for Mises: sleek, covered, honest builds (research for D15, D16, D25, D27, D28)

Researched 2026-10-30. Feeds Plan 9 (`docs/superpowers/plans/2026-10-30-design-first.md`) and builds on Plan 8 (`2026-10-30-geometry-quality.md`).
Tags: **[T]** = tested here on build123d 0.13.0 in `core/.venv`. **[V]** = vendor or press claim. **[2]** = secondary or aggregator source only. **[J]** = my judgment, not sourced.

---

## 0. Top recommendations (TL;DR)

1. **Make the agent design in stages, with a gate at each one.** The stages are brief → mood and pitch → massing sketch → hardpoints/skeleton → skin over mechanism → split lines/CMF → detail → engineering checks. Each stage produces a named artifact, and the agent may not touch detail geometry before massing is approved (§1.1).
2. **Default to "skin over skeleton".** Mechanism parts (actuators, PCBs, batteries) are placed at hardpoints on the skeleton. Visible parts are separate *cover* parts: lofted shells with an inset cavity, split into moldable halves or panels with real 0.6–1.0 mm gaps. Straight bars are banned as visible geometry. A link is a `tapered_capsule` or a `station_loft` shell (§2).
3. **Add 6 recipes to `mises_shapes`.** All were tested [T]: `squircle`, `station_loft`, `skin_shell` (inset-loft subtract, **not** `offset_3d`), `tapered_capsule`, `joint_cowl`, `panel_split(gap)`. Also add a `trust()` validator that cross-checks two volume methods (§2.3).
4. **Fix the renderer before trusting design critique** [T]. It flat-shades each triangle, so perfectly smooth lofts (even an exact ellipse loft) render as crumpled. The model will "see" dents that do not exist. Add smooth vertex normals, a zebra (reflection-stripe) mode for G1/G2 checks, and an NPR "sketch" mode (§4.3, §2.4).
5. **Concept sketch = NPR line render of a rough massing model**, not SVG drawn freehand. It is grounded in geometry, it becomes the skeleton, and the same renderer produces it. Use SVG only for annotation overlays and the 2D silhouette thumbnails in option pitches (§4.4).
6. **Critique loop: use measurements plus renders, with a separate judge context.** Escalate strategy when an issue repeats, and give the judge an *aesthetic* rubric. Published work shows that renders mainly cut catastrophic tail failures (CADSmith: mean Chamfer 18.19 → 0.74). No paper measures aesthetics, so Mises needs its own rubric (§3).
7. **Image budget**: every current Claude model is in the high-resolution tier (2576 px long edge, 4784 visual tokens). Design critique should send 2–4 views at about 1024 px, or one 2×2 contact sheet at about 1536 px, instead of 4×512 px. Raise `sketch.MAX_IMAGE_EDGE` from 1568 (§4.1).
8. **Models**: Opus 5.5 at high effort for design and build. The judge runs in a fresh context: Fable 5.1 for the final design review on design-heavy builds, otherwise Opus 5.5. Sonnet 5.5 handles cheap per-round checks. Haiku 5.5 handles classification and leak checks. Anthropic publishes no design-taste benchmark, so validate with a Mises eval (§5).
9. **Classify design intensity** with a 0–1 score from a Haiku call plus the existing `shape_complexity` keywords. The class sets sketch depth, number of options pitched, surfacing rules, critique weights and DFM strictness (§6).
10. **Pitch 2–3 directions** as cards. Each card has a name, 3 mood words, a silhouette (front and side), a proportion note, a CMF swatch and explicit trade-offs (mass, cost, manufacturability, risk). One option must be the "honest engineering" baseline (§7).

---

## 1. The industrial-design process an agent can follow

### 1.1 Stage-gated pipeline (what each stage outputs)

| # | Stage | Artifact the agent must produce | Gate / check |
|---|---|---|---|
| 0 | **Brief** | Use, user, environment, scale, hard constraints (DoF, payload, actuator models, battery), *design intent sentence* ("a calm, appliance-like home humanoid") | User or lead confirms |
| 1 | **Mood and pitch** (D25) | 2–3 directions (§7): mood words, reference archetypes, silhouette thumbnails | User picks one or mixes them |
| 2 | **Massing** (D15) | Rough solids only: torso, pelvis, head, limb volumes, and wheels for a car. Proportions in "units" (head heights, wheel diameters). Rendered as an NPR sketch (§4.4) | User circles, pins and comments; the agent revises; build starts only on approval |
| 3 | **Hardpoints / skeleton** (G3) | Skeleton part: joint axes, actuator envelopes, battery and PCB volumes, ground/eye line, wheel centres, H-point. In car design this is the "package". The clay modeller "takes that sketch and puts it over the package" ([Core77](https://www.core77.com/posts/86265/A-Look-at-Six-Car-Design-Specialties-Part-2-The-Clay-Modeler); [Audi: package = "the design's technical architecture"](https://www.audi-mediacenter.com/en/press-releases/the-abcs-of-audi-design-14079)) | `section_check`: every envelope sits inside the massing, with clearance |
| 4 | **Primary surfaces** | Covers as lofts and Gordon surfaces through *sections taken from the massing* and *offset from the skeleton envelopes*. Few, large surfaces | Zebra render: no stripe breaks on primary surfaces; silhouette still matches the approved sketch (IoU) |
| 5 | **Split lines and CMF** | Parting lines and panel gaps chosen *as design lines* (they are the feature lines). Materials and finishes assigned (colour blocking: e.g. light shell / dark joints) | Every panel is moldable (draft direction exists), and gaps are consistent |
| 6 | **Detail** | Fillets, vents, fasteners hidden under covers, lighting/sensor windows, logos | Fillets last; the fillet ladder never falls below a set minimum |
| 7 | **Engineering pass** | Walls, ribs, bosses, draft, mass, interference, range-of-motion sweep | DFM numbers (§1.4); ROM sweep shows no cover collision |

Professional practice evaluates **proportion and stance first, then surfacing, then graphics** ([vizcom sports car design, 2](https://vizcom.com/blog/sports-car-design)). Clay or physical models remain the final check of proportion ([Autocar on clay](https://www.autocar.co.uk/node/164543); [Automotive World / Cupra](https://www.automotiveworld.com/news/cupra-terramar-sculpting-the-new-hero/)). Mises' analogue is the rendered massing plus a turntable view card.

### 1.2 Design-language checklist (goes into the designer prompt and the judge rubric)
- **Silhouette first.** The object should be recognisable as a black fill from front and side. There should be one dominant gesture line per view (e.g. spine-to-heel on a humanoid, beltline on a car). [J]
- **Proportion in units.** Humanoid: about 7.5–8 head heights. Car: wheelbase about 3 wheel diameters ([glossary, 2](https://nielsvanroij.com/automotive-glossery/)), greenhouse:body about 1:3–2:3 (Audi via [TUM paper](https://mediatum.ub.tum.de/doc/1226669/682881.pdf)), and dash-to-axle sets the premium/sporty read ([Hagerty](https://www.hagerty.com/media/?p=181035); [J.D. Power](https://www.jdpower.com/cars/shopping-guides/what-is-a-dash-to-axle-ratio-in-car-design)). Quadruped: body length about 2× leg length, low and wide. [J]
- **Few, large, continuous surfaces.** Class-A means character lines plus G2 (or G3) continuity, inspected by reflections ([Wikipedia: Class A surface](https://en.wikipedia.org/wiki/Class_A_surface)). In zebra stripes, G0 shows a jump, G1 a kink, and G2 flows smoothly through the seam ([Rhino Zebra](https://docs.mcneel.com/rhino/7/help/en-us/commands/zebra.htm); [Onshape surfacing](https://cad.onshape.com/help/Content/surfacing.htm); [Autodesk](https://help.autodesk.com/cloudhelp/2023/ENU/Inventor-Help/files/GUID-C67F8283-DFDF-498F-89BE-91E131CE6171.htm)).
- **Feature lines are real.** A crisp line comes from a panel gap, a parting line or a deliberate crease. It never comes from leftover boolean edges.
- **Hierarchy.** Big primary volumes, then secondary covers, then tertiary details (vents, fasteners), each about 1/3 the scale of the level above. [J]
- **Hide or celebrate the mechanism, never half-expose it.** Joints become either clean cowls or deliberate "jewel" elements (machined rings).
- **CMF**: at most 2–3 materials and colours. Colour blocking follows the hierarchy (e.g. light glossy shells, matte dark joints/soft goods, one accent).

### 1.3 How the reference robots get "sleek" (what to copy)
| Robot | Technique | Source |
|---|---|---|
| **Tesla Optimus Gen 2** | Tesla-designed actuators with *integrated electronics and harnessing*, so no loose cables. "Body panel upgrades" | [Not a Tesla App](https://notateslaapp.com/news/1821/tesla-unveils-optimus-robot-gen-2-tesla-designed-fingertip-sensors-actuators-and-ten-other-improvements-video) [V/2] |
| **Optimus Gen 3 (leaked app renders)** | Enclosed joints in place of exposed actuators, a more unified torso shell, flexible covers at hip and pelvis for pinch points. Hand actuators moved into the forearm (tendon-driven) | [Not a Tesla App](https://www.notateslaapp.com/news/4725/tesla-app-leaks-new-optimus-gen-3-robot-design), [Drive Tesla](https://driveteslacanada.ca/news/tesla-optimus-gen-3-design-android-app/), [eletric-vehicles patents](https://eletric-vehicles.com/tesla/tesla-files-patents-revealing-optimus-gen-3-mechanical-blueprint/) [2, leak-based] |
| **1X NEO / NEO Gamma** | Tendon (cable) drives keep motors central. A soft 3D-lattice polymer body is covered by a whole-garment knit nylon suit (Shima Seiki), with about 22 dB operation | [Yanko Design](https://www.yankodesign.com/2025/03/25/neo-gamma-humanoid-robot-dons-a-3d-printed-fabric-skin-to-better-blend-with-your-home/amp/), [humanoid.guide](https://humanoid.guide/product/neo-gamma) [2] |
| **Figure 03** | Washable, tool-free removable textile over multi-density foam (pinch-point guard). About 9% lighter than Figure 02. Designed for volume manufacturing | [Robotics & Automation News](https://roboticsandautomationnews.com/2025/10/09/figure-unveils-third-generation-humanoid-robot/95351), [Gigazine](https://gigazine.net/gsc_news/en/20251010-figure-03) [V/2] |
| **Boston Dynamics Spot** | The 2017 SpotMini redesign replaced the exposed prototype with a smooth yellow shell and curved leg covers that hide wiring | [ECN 2017](https://www.ecnmag.com/news/2017/11/video-day-sleek-new-quadruped-robot), [New Atlas](https://newatlas.com/boston-dynamics-spotmini-robot-streamlined/52169) [2] |
| **Unitree Go2** | An apparently single-shot injection-molded housing *textured to read as two pieces* (fewer parts, same design read). "Meticulous cable management" | [Simplexity teardown](https://www.simplexitypd.com/blog/unitree-go2-quadruped-robot-dog-teardown/), [iFixit](https://www.ifixit.com/News/109048/unitree-go2-is-an-impressively-affordable-robot-dog-but-is-it-repairable) [2] |

**Patterns to encode** [J, synthesised from the above]:
1. **Actuator = joint.** The actuator sits *on* the joint axis, so the link between joints is a slim hollow shell carrying only a harness. In Mises, the skeleton puts actuator envelopes on joint axes, and links are shells.
2. **Proximal mass.** Motors go toward the torso/hip with tendons or linkages to distal joints (NEO, Optimus hand). Limbs then taper distally, which is what reads as "sleek".
3. **Shell-and-insert.** A structural inner frame (machined or cast) carries loads. Thin cosmetic shells (molded, 2–3 mm) or soft goods clip over it. Cables run *inside* links through pass-throughs in bulkheads.
4. **Joint covers with ROM slots**, plus flexible or soft covers where shells would pinch.
5. **Fewer, bigger parts** (Go2 single-shot shell) with fake split lines for visual rhythm.

### 1.4 Engineering honesty (molds, DFM numbers for the engineering pass)
- Use a uniform nominal wall. Ribs are 50–60% of the wall and at most 2.5–3× wall high. Boss walls are ≤ 60% of the wall. Draft is 1–2° (≥ 0.5° minimum, 3–5° for textured surfaces). Keep parting lines simple. ([Stratasys Direct guide](https://info.stratasysdirect.com/rs/626-SBR-192/images/Stratasys_Direct_Design_Guidelines_Injection_Molding_201907.pdf), [RapidDirect](https://www.rapiddirect.com/blog/injection-molding-design-guide/), [Fathom PDF](https://fathommfg.com/app/uploads/2025/12/Design-Guidelines-for-Injection-Molding.pdf))
- Each cover panel needs a pull direction. Check that every face normal has `dot(n, pull) >= sin(draft)` or lies on the parting line. build123d `Solid.draft(faces, neutral_plane, angle)` exists and works on planar faces [T]. For freeform skins, *build draft into the sections* (sections taper along the pull) rather than drafting afterwards.

---

## 2. Doing it in build123d / OCCT

### 2.1 API names verified on build123d 0.13.0 [T]
- Lofts: `bd.Solid.make_loft(wires_or_vertices, ruled=False)`, builder-free `bd.loft(sections, ruled=False)`.
- Sweeps: `bd.sweep(sections, path, multisection=False, is_frenet=False, transition=..., binormal=...)`, `bd.Solid.sweep(...)`, `bd.Face.sweep(profile, path)`.
- Surfaces: `bd.Face.make_gordon_surface(profiles, guides, tolerance=3e-4)` (a curve network, the closest thing to "loft with guide curves"; works best when every profile crosses every guide exactly once, reusing the same Edge objects; [Spitfire Gordon tutorial](https://build123d.readthedocs.io/en/latest/tutorial_spitfire_wing_gordon.html), [Surface modeling](https://build123d.readthedocs.io/en/latest/tutorial_surface_modeling.html)). Also `Face.make_surface(exterior, surface_points, interior_wires)`, `Face.make_surface_from_array_of_points(grid, tol, smoothing, min_deg, max_deg)`, `Face.make_bezier_surface(points, weights)`, `Face.make_surface_from_curves`, and `Face.make_surface_patch(edge_face_constraints=[(edge, face, ContinuityLevel.C1)], ...)` (a G1/C2 fill patch; `ContinuityLevel` is C0/C1/C2; not exercised here).
- Solids from surfaces: `bd.Solid.thicken(face_or_shell, depth, normal_override=None)`, builder `bd.thicken(..., both=True)`.
- Offsets and shells: `Solid.offset_3d(openings, thickness, tolerance, kind)`, `bd.offset(obj, amount, openings, kind)`, `Solid.hollow`, `Solid.shell`.
- Blends: `Solid.fillet(radius, edge_list)`, `Solid.max_fillet(edge_list, tolerance, max_iterations)`, `Solid.chamfer`, `Solid.draft(faces, neutral_plane, angle)`; `mises_shapes.blend_edges` / `variable_fillet` already wrap these.
- Splits: `bd.split(obj, bisect_by=Plane|Face|Shell, keep=Keep.TOP|BOTTOM|BOTH)`.
- Splines: `bd.Spline(*pts, tangents=...)`, `bd.Edge.make_spline(pts, periodic=True)`, `bd.Edge.make_spline_approx(pts, tol, max_deg)`, `bd.Edge.make_ellipse(a, b, plane)`.

### 2.2 Findings from testing (these change how recipes must be written) [T]
1. **`offset_3d` on lofted freeform solids is untrustworthy.** It returns `is_valid=True`, but the volume is inconsistent.
   - Thigh loft hollowed with `offset_3d(openings, -2.5)`: OCCT volume 95,589 vs mesh volume 112,837 (an 18% disagreement).
   - Torso: `offset_3d` gave a **negative** precise OCCT volume (−679,684).
   - **Fix:** build the cavity as a second loft through *inset sections* and subtract it. The volumes then agree within about 1% (thigh 115,681 vs 115,206; torso 881,389 vs 888,176).
   - Implication for D8 (mass on every part): `Shape.volume` on these lofts was off by about 2% against a 0.05 mm mesh. Compute mass with `BRepGProp.VolumeProperties_s(shape, props, 1e-6)` and cross-check.
2. **`is_valid` is not enough.** Subtracting a 0.9 mm groove tube from an `offset_3d` shell *increased* its volume. Use `trust()` (§2.3): valid, positive precise volume, and agreement with the mesh volume.
3. **Panel lines as engraved grooves are fragile; panel *gaps* are robust.** Splitting the shell twice at ±gap/2 gives two valid parts and a true feature line. It is also how real products are built.
4. **Spline seam on a split plane breaks meshing.** A periodic section whose seam sits at local +X, split by `Plane.XZ`, produced a face that `tessellate()` could not mesh (`NoneType.NbNodes`). Put the seam off any parting plane (`seam_deg=225`).
5. **Interpolating dense superellipse samples is fine geometrically.** The lumpy look in renders was the renderer (point 6). Even so, prefer `make_spline_approx` (fitted) or exact conics (`make_ellipse`) for sections. Caveat: a closed approximated spline is not periodic, so its seam is not guaranteed G2. Keep the seam on a hidden side, or use a periodic `make_spline` with about 16–24 evenly spaced points.
6. **The Mises rasterizer flat-shades per triangle** (`render.py` `_project`: `shade` is per triangle). A loft of *exact ellipses* renders as crumpled foil at 0.05 mm tessellation. A vision judge will report false "dents/lumps" and the agent will chase them. See §4.3 for the fix.
7. Speed: lofts take about 0.03 s. Shell, rib and split for a limb take about 2 s. A torso shell with sockets takes about 0.5 s. All are fine inside the worker.

### 2.3 Tested code patterns (run as one script, all pass) [T]

```python
import math
import numpy as np
import build123d as bd
from build123d import Plane, Axis, Pos, Rot, Keep

# --- helpers ---------------------------------------------------------------
def squircle(a, b, n=2.6, plane=Plane.XY, k=96, tol=0.05, seam_deg=225.0):
    """Closed smooth section: superellipse |x/a|^n + |y/b|^n = 1 (n=2 ellipse, n~3-4 'soft box').
    Fitted (approximated) B-spline, not interpolated. Seam at seam_deg: keep it off split planes."""
    pts = []
    for i in range(k):
        t = math.radians(seam_deg) + 2 * math.pi * i / k
        c, s = math.cos(t), math.sin(t)
        pts.append(plane.from_local_coords((a * math.copysign(abs(c) ** (2 / n), c),
                                            b * math.copysign(abs(s) ** (2 / n), s))))
    return bd.Wire([bd.Edge.make_spline_approx(pts + [pts[0]], tol=tol, max_deg=5)])

def station_loft(stations, inset=0.0):
    """stations: [(z, half_w, half_d, n, dx, dy)] along +Z. inset>0 shrinks every section."""
    return bd.Solid.make_loft([
        squircle(a - inset, b - inset, n, Plane.XY.offset(z).shift_origin((dx, dy, z)))
        for z, a, b, n, dx, dy in stations])

def occ_volume(s, eps=1e-6):
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(s.wrapped, p, eps); return p.Mass()

def mesh_volume(s, tol=0.05):
    v, f = s.tessellate(tol, 0.1)
    V = np.array([[q.X, q.Y, q.Z] for q in v]); F = np.array(f)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)

def trust(s, what, rel=0.03):
    """Valid AND two independent volumes agree (catches bad offsets that is_valid misses)."""
    vo, vm = occ_volume(s), mesh_volume(s)
    if not (s.is_valid and vo > 0 and abs(vo - vm) <= rel * max(vo, vm)):
        raise ValueError(f"{what}: untrustworthy solid (occ={vo:.0f}, mesh={vm:.0f})")
    return s

# --- 1. tapered limb shell enclosing a servo (thigh: hip axis z=0, knee z=-220)
THIGH = [(-10, 38, 34, 2.2, 0, 0), (-60, 42, 40, 2.6, 0, 4), (-120, 36, 34, 2.6, 0, 3),
         (-180, 30, 28, 2.4, 0, 0), (-212, 28, 26, 2.2, 0, 0)]   # belly forward (dy) = muscle read
WALL = 2.5
outer = station_loft(THIGH)
cavity = station_loft(THIGH, inset=WALL)                          # NOT offset_3d (see 2.2)
cavity = cavity + Pos(0, 0, -5) * bd.Box(60, 60, 12) + Pos(0, 0, -217) * bd.Box(40, 40, 12)
shell = trust(outer - cavity, "thigh shell")
servo_env = Pos(0, 2, -45) * bd.Box(43, 23, 43)                   # 40x20x40 servo + 1.5 mm clearance
clash = servo_env - cavity
assert not clash.solids() or clash.volume < 1.0, "servo does not fit inside the skin"
rib = (Pos(0, 0, -68) * bd.Box(200, 200, 2.0)) & outer          # bulkhead the servo bolts to
rib = rib - Pos(0, 0, -68) * bd.Box(16, 10, 4)                    # harness pass-through
thigh = trust(shell + rib, "thigh+rib")
front = bd.split(thigh, bisect_by=Plane.XZ, keep=Keep.TOP)       # clamshell halves; the parting
back = bd.split(thigh, bisect_by=Plane.XZ, keep=Keep.BOTTOM)     # line runs on the side silhouette

# --- 2. torso shell lofted through 6 sections + a real panel gap as the feature line
TORSO = [(0, 120, 70, 3.0, 0, 0), (90, 105, 68, 2.6, 0, 0), (200, 135, 80, 2.8, 0, 6),
         (300, 165, 85, 3.2, 0, 10), (370, 150, 72, 3.4, 0, 4), (410, 70, 55, 2.4, 0, 0)]
t_out = station_loft(TORSO)                                       # pelvis, waist, chest x2, shoulder, neck
t_in = station_loft(TORSO, inset=3.0) + Pos(0, 0, -5) * bd.Cylinder(100, 12) \
       + Pos(0, 0, 410) * bd.Cylinder(60, 12)                     # open bottom + neck
torso = trust(t_out - t_in, "torso shell")
for sx in (-1, 1):                                                # shoulder sockets for joint covers
    torso = torso - Pos(sx * 165, 5, 330) * Rot(0, 90, 0) * bd.Cylinder(48, 60)
GAP = 0.8
chest = bd.split(torso, bisect_by=Plane.XY.offset(250 + GAP / 2), keep=Keep.TOP)
abdomen = bd.split(torso, bisect_by=Plane.XY.offset(250 - GAP / 2), keep=Keep.BOTTOM)

# --- 3. joint cover: a revolved cowl around a knee actuator (joint axis = X)
R_ACT, W_ACT, CLR, T = 34, 50, 2.0, 2.5            # actuator radius/width, clearance, wall
r_in = R_ACT + CLR
prof = bd.Spline((-(W_ACT/2 + CLR + T), 0, r_in*0.55), (-W_ACT/2, 0, r_in + T + 3),
                 (W_ACT/2, 0, r_in + T + 3), (W_ACT/2 + CLR + T, 0, r_in*0.55))
x0, x1 = (prof @ 0).X, (prof @ 1).X
lid = bd.Wire([prof, bd.Line(prof @ 1, (x1, 0, 0)), bd.Line((x1, 0, 0), (x0, 0, 0)),
               bd.Line((x0, 0, 0), prof @ 0)])
cowl = bd.revolve(bd.Face(lid), axis=Axis.X)                     # barrel lozenge around the joint
cowl = cowl - Rot(0, 90, 0) * bd.Cylinder(r_in, W_ACT + 2 * CLR)  # actuator + clearance
cowl = cowl - Pos(0, 0, -100) * bd.Box(200, 200, 200)             # keep the visible upper cap
cowl = cowl - bd.Box(W_ACT + 2 * CLR, 30, 200)                    # ROM slot the link swings through

# --- 4. tapered capsule link = convex hull of two spheres (replaces every visible "bar")
def tapered_capsule(r1, r2, L):
    a = math.asin(max(-0.99, min(0.99, (r1 - r2) / L)))
    p1 = (r1*math.cos(a), 0, r1*math.sin(a)); p2 = (r2*math.cos(a), 0, L + r2*math.sin(a))
    m1, m2 = (a - math.pi/2) / 2, (a + math.pi/2) / 2
    w = bd.Wire([bd.ThreePointArc((0, 0, -r1), (r1*math.cos(m1), 0, r1*math.sin(m1)), p1),
                 bd.Line(p1, p2),
                 bd.ThreePointArc(p2, (r2*math.cos(m2), 0, L + r2*math.sin(m2)), (0, 0, L + r2)),
                 bd.Line((0, 0, L + r2), (0, 0, -r1))])
    return bd.revolve(bd.Face(w), axis=Axis.Z)                  # G1 everywhere, 3 faces

# --- 5. Gordon surface (profiles x guides) thickened into a fairing panel
profs = [bd.Spline((-40, 0, z), (0, 25 + 5*math.sin(z/40), z), (40, 0, z)) for z in (0, 60, 120)]
guides = [bd.Spline(*[p @ u for p in profs]) for u in (0, 0.5, 1)]
panel = bd.Solid.thicken(bd.Face.make_gordon_surface(profs, guides), 2.0)
```
Measured: thigh 125,085 mm³ (halves 59,716 + 65,369). Torso shell 838,684 (chest 345,732, abdomen 490,936). Cowl valid. Capsule valid. Gordon panel valid. The whole script runs in about 5 s. Rendered (two-tone chest/abdomen, clamshell thigh, orange knee cowl), it already reads as a product rather than a kit.

### 2.4 Rules for the prompt and recipe docs (geometry that doesn't fail) [T + J]
- **Smoothness comes from construction, not fillets.** Lofts through matched sections, revolves of splines, capsules and Gordon surfaces have no sharp edges to fillet. Fillet only the *boolean seams* (e.g. arm stub into torso) with the existing `blend_edges` ladder, last, with a minimum radius. If the ladder hits the floor, change topology (loft a transition instead of fusing).
- **Same section topology.** Every loft section has the same edge count and seam angle. Use a single closed spline per section. Avoid `RectangleRounded`, which gives 8 edges, G1 only, and loft twist.
- **Shells = outer loft − inset loft** (or `thicken` of a single face). Never use `offset_3d` on freeform unless `trust()` passes.
- **"Cover around servo" pattern:** envelope = component bbox + clearance. Assert `envelope - cavity` is empty. Add a bulkhead = plane slab ∩ outer. Add harness holes. Split along the silhouette parting plane. Add bosses and ribs from the DFM ratios (§1.4).
- **Feature lines = panel gaps or splits**, placed on planes or design curves chosen in stage 5, with seams kept off them.
- **Skin from skeleton:** sections are placed at skeleton stations, sized as `max(envelope half-width + clearance + wall, design curve)`. The skin then stays honest when the skeleton changes (G3 regeneration).
- **G2 checks:** loft sections with consistent parameterisation, then inspect with the zebra render (§4.3). For G1/G2 fills between panels, try `Face.make_surface_patch` with `ContinuityLevel.C1/C2` constraints (untested here).

### 2.5 Proposed `mises_shapes` additions (one-line index entries)
- `squircle(a, b, n, plane, seam_deg)`: a smooth closed section; n=2 ellipse, 3–4 soft box.
- `station_loft(stations, inset=0)`: a body or limb through (z, half_w, half_d, n, dx, dy) stations.
- `skin_shell(stations, wall, open_ends=(True, True))`: outer − inset loft, `trust()`-checked.
- `tapered_capsule(r1, r2, L)`: a G1 link between two joint spheres.
- `joint_cowl(r_act, w_act, clearance, wall, rom_deg)`: a revolved cap with a range-of-motion slot.
- `panel_split(shape, plane_or_face, gap=0.8)`: two parts with a real gap.
- `clamshell(shape, plane)`: molded halves (+ optional lip/groove).
- `cover_for(component_envelope, skin, clearance)`: asserts the fit and adds a bulkhead plus harness hole.
- `trust(shape)`: validity plus volume cross-check (and use the same precise volume for D8 mass).
- `humanoid_massing(height, proportions)`, `quadruped_massing(...)`, `car_massing(wheelbase, wheel_d, ...)`: stage-2 massing archetypes driven by proportion units.

---

## 3. LLM text-to-CAD research: what measurably helped

| Work | What they did | Measured effect | Lesson for Mises |
|---|---|---|---|
| **CADSmith** (arXiv 2603.26512) | Sonnet generator, **separate Opus judge**, kernel metrics plus a 3-view render (iso 35°/45°, high rear 65°/220°, front 10°), 2400×800 composite | Zero-shot → no-vision → full: exec 95/99/100%, **mean** Chamfer 28.37 / 18.19 / **0.74**, median IoU 0.81/0.96/0.96. On the hardest tier, removing the render raised mean CD from 1.42 to 49.68. 88/100 converged in round 1 | Renders mainly **remove catastrophic failures**, and matter most on complex parts. Keep the judge separate. Tell it not to repeat a failed suggestion, and switch strategy from iteration 3. Small defects at joints were missed at fixed view scale, so allow **judge-requested views and zoom crops** ([HTML](https://arxiv.org/html/2603.26512)) |
| **CADCodeVerify** (ICLR 2025, arXiv 2410.05340) | VLM writes verification questions about the render, answers them, refines | GPT-4: −7.30% point-cloud distance, +5.0–5.5% success | "Ask yourself checkable questions about the image" beats a generic "critique this" ([HF](https://huggingface.co/papers/2410.05340)) |
| **LLMForge / Foundation Models for Automatic CAD Generation** (arXiv 2607.05573) | Scalar render metrics vs VLM natural-language critique | Scalar feedback peaks at round 1. NL critique keeps improving in rounds 2–3. Visual score r=0.959 with overall, VLM semantic r=0.776 (a distinct signal). Freeform and multi-body are *out of scope* | Use **both** numbers and words. Freeform/aesthetic evaluation is an open problem, so build a rubric ([alphaxiv](https://www.alphaxiv.org/overview/2607.05573)) |
| **3DCodeBench** (arXiv 2606.01057) | 12 VLMs, Blender procedural code, multi-view VLM critic | Multi-turn feedback raised executability 0.692 to 0.972. Full agent harnesses raise executability **but not per-instance shape quality**. Extra views add little. Floating or disconnected parts are common | The bottleneck is geometric reasoning. Supply **structure** (skeleton, recipes) rather than more loops. Add a "connectedness" check ([emergentmind](https://www.emergentmind.com/papers/2606.01057)) |
| **CAD-Assistant** (arXiv 2412.13810) | Zero-shot VLLM planner plus FreeCAD tools: sketch-image parameteriser, renderer, **2D cross-section generator** | Beats VLLM baselines and supervised methods on several benchmarks | Sections as a tool (Mises already has `section_check`). Feed sections to the judge for covers ([arXiv](https://arxiv.org/abs/2412.13810)) |
| **CADDesigner** (arXiv 2508.01031) | Text plus *sketch* input, requirement-clarifying dialogue, iterative visual feedback, a **knowledge base of finished cases** | "Outperforms baselines" (no numbers on the abstract page) | Store approved builds as reusable design cases or recipes ([arXiv](https://arxiv.org/abs/2508.01031)) |
| **LL3M** (arXiv 2508.08228) | Multi-agent Blender coder with planner, retrieval (BlenderRAG over API docs), critic, verifier on renders | Qualitative only | RAG over API docs plus a verifier that re-renders after fixes ([arXiv](https://arxiv.org/abs/2508.08228)) |
| **CAD-Coder** (arXiv 2505.14646) | Fine-tuned VLM, image → CadQuery | Chamfer 6.54 vs Text2CAD 29.29 (×10⁻³), invalid 1.45% vs 3.75% | Fine-tuning helps prismatic parts, but not relevant to aesthetics ([arXiv](https://arxiv.org/abs/2505.14646)) |
| **SketchAgent** (CVPR 2025, arXiv 2411.17673) | An off-the-shelf multimodal LLM draws stroke-by-stroke (cubic Béziers on a grid) via in-context examples, with chat edits | Editing requests executed in about 92% of cases [2]. Fails on complex concepts | Model-drawn vector sketches suit icons and annotations, not complex 3D product perspective ([CVF](https://openaccess.thecvf.com/content/CVPR2025/html/Vinker_SketchAgent_Language-Driven_Sequential_Sketch_Generation_CVPR_2025_paper.html)) |
| CADGenBench (in `research/models/model-choice-2026-10.md`) | Drawing → part | build123d-mcp + Opus 5.5 max: 86.9% vs plain Fable 5: 37.3% | The tool loop matters more than the model |

**Bottom line:** no published work measures *aesthetic* quality of LLM CAD. What reliably helps:
- a measurement plus render loop with a separate judge
- checkable questions instead of open critique
- adaptive or zoomed views
- strategy escalation
- structure supplied up front (skeleton, recipes, past cases)

Mises should add its own aesthetic rubric and eval set (§6, §8).

---

## 4. Multimodal into Claude

### 4.1 What Claude accepts (Claude API docs, [Vision](https://platform.claude.com/docs/en/build-with-claude/vision))
- Formats: JPEG, PNG, GIF, WebP, as base64, URL or Files API `file_id`.
- Limits: up to **600 images per request** (100 for 200k-context models; 20 per turn on claude.ai). Max **8000×8000 px**. **10 MB per image** (5 MB on Bedrock). 128 MB request.
  - With **more than 20 images in a request**, including images resent from earlier turns and images inside `tool_result`, a stricter per-image dimension limit applies. Keep each image ≤ 3000 px, or keep to ≤ 20 image blocks.
- Cost: `ceil(w/28) × ceil(h/28)` visual tokens.
  - **High-resolution tier (Claude 4.7 and later, i.e. every current model): max long edge 2,576 px, max 4,784 visual tokens.** The standard tier is 1,568 px / 1,568 tokens.
  - Examples: 512² costs 361 tokens, 1000² costs 1,296, 1024×1024 costs about 1,369.
- Put images **before** the text that asks about them. Images under 200 px, rotated images and low-quality images degrade accuracy. Coordinates and localisation are approximate.
- **No image generation:** "Claude is an image understanding model only… it cannot generate, produce, edit, manipulate, or create images" (Vision FAQ). The current models table also lists input as "Text and images → text" for Opus 5.5 and Fable 5.1 ([Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/overview), [Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview)).
- **PDF**: up to 600 pages per request (100 below 1M context). Each page goes in as **extracted text plus a page image**, costing 1.5–3k text tokens per page plus image tokens ([PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)). This suits reference drawings, spec sheets and actuator datasheets.

### 4.2 In Claude Code (the Mises runtime)
- MCP tool results can carry `image` content blocks. Mises already returns `ToolImage`s from `render_views`/`critique_model` (`views.py:_tool_images`).
- Claude Code warns at 10k tokens of MCP output and **caps it at 25k tokens by default** (`MAX_MCP_OUTPUT_TOKENS` raises the cap) ([Claude Code MCP docs](https://code.claude.com/docs/en/mcp)). Four 1024 px images (about 5.5k tokens) fit comfortably. Eight do not fit with much text.
- The Read tool on a PNG returns the image to the model. Claude Code downsizes large images. Community reports say oversized (>5 MB) MCP screenshots caused "image too large" loops ([issue mirror, 2](https://claudeissues.com/issue/27611-read-tool-loops-indefinitely-on-large-playwright-screenshots-with-image-was-too)). Keep Mises PNGs < 1 MB and ≤ 2048 px.

### 4.3 Render fixes before trusting design critique (render.py) [T + J]
1. **Smooth shading**: weld vertices (round to 1e-4, `np.unique` on rows), compute area-weighted vertex normals, interpolate normals per pixel (Phong) or per vertex (Gouraud). Split normals where the dihedral angle exceeds about 30° so real creases stay crisp. Without this, smooth B-reps look dented (see §2.2 #6).
2. **Zebra mode**: shade = `0.5 + 0.5*sign(sin(k * dot(n_view, stripe_dir)))` using interpolated normals. A stripe kink means G1, a jump means G0. This gives the judge (and the user) a real surface-quality signal, the same one Rhino, Onshape and Inventor use.
3. **Sketch (NPR) mode**: §4.4.
4. **Material/CMF preview**: per-part colour and gloss (a Blinn specular term) so colour blocking reads. This also covers D24 cover images: three-point light, soft ground shadow, 3/4 view at 35 mm-equivalent perspective (the renderer is orthographic now; add perspective).
5. **Defaults for design critique**: 3/4 front, side and front at 1024 px, plus judge-requested zoom crops (CADSmith lesson). Raise `views._SIZE_MAX` above 1024 for crops, and raise `sketch.MAX_IMAGE_EDGE` from 1568 to 2048. Sending 4×512 px to a high-res-tier model wastes its resolution on surface judgement.

### 4.4 Concept sketch without image generation: recommendation
Options considered:
- **(a) Model-drawn SVG.** It is cheap and editable. But LLMs "degrade with increasing complexity" ([SVGenius](https://arxiv.org/abs/2506.03139)) and SketchAgent fails on complex concepts. A freehand 3D perspective product sketch from SVG will be off-model and won't become geometry.
- **(b) NPR line render of a rough massing model.** It is geometrically true, cheap (lofts plus capsules build in under 1 s), and the approved massing *becomes* the stage-3/4 input.
- **(c) External image-gen API.** It adds a dependency, gives no geometric grounding, and the user wants honest CAD.

**Recommend (b), with (a) as an overlay layer:**
- Build `massing` (one simplified solid per major volume) using archetype recipes and proportion units.
- Render a "sketch" style:
  - paper-white background
  - **silhouette** lines (the existing `_silhouette_edges`) at 2.5 px
  - **crease and occluding contour** lines (the existing `_depth_edges`, plus normal-discontinuity edges) at 1.5 px
  - optional suggestive contours ([DeCarlo et al.](https://history.siggraph.org/?p=115938); [Princeton line drawing course notes](https://gfx.cs.princeton.edu/proj/sg08lines/lines-1-overview.pdf))
  - a 2–3-band toon tone fill in light grey
  - a **hand-drawn wobble**: displace line pixels along their normal by low-frequency noise (amplitude about 1 px, wavelength about 40 px), and draw each line twice with slight offset ("over-sketch")
- Views: 3/4 front perspective plus side elevation, with a ground line and a human-scale figure for scale.
- The agent adds **SVG annotation overlays**: callouts, arrows, proportion grid, dimension marks, labels. These are simple shapes the model draws reliably. The user's circles and pins (D15) are stored in the same overlay coordinate frame, which maps back to 3D through the known camera.
- For pitch thumbnails (§7), a filled **black silhouette** from `render_mask` is the strongest, cheapest proportion signal.

---

## 5. Which Claude model for which stage
What Anthropic publishes ([models overview](https://platform.claude.com/docs/en/models/overview)):
- All current models (Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5) take text and image input and have 1M context, 128K output and adaptive thinking.
- Fable 5.1 is "for demanding reasoning and long-horizon agentic work, or when your evals on Opus 5.5 at higher effort still fall short", at $10/$50.
- Opus 5.5 is the recommended default, at $4/$20 with default effort `medium`.
- Sonnet 5.5 costs $2/$10 and Haiku 5.5 from $0.10/$0.50.
- The **only published vision numbers** are from the Opus 5.5 announcement: Chartography 89.0% (Opus 5.5) vs 88.4% (Fable 5.1), and OSWorld 2.1 81.8% vs 80.7%. One customer anecdote credits Opus 5.5 with the best "graphics and polish" ([announcement](https://www.anthropic.com/claude-opus-5-5)).
- There are **no published design-taste, 3D or spatial-reasoning benchmarks.** CADGenBench (Opus 5.5 + build123d-mcp, 86.9%) is the strongest CAD evidence.

| Stage | Model | Why |
|---|---|---|
| Classify design intensity, leak/irrelevance check (D26), route | **Haiku 5.5** | Classification and routing is its stated use; cheap |
| Brief, pitch options, massing, skeleton, surfacing code (lead/designer/engineer) | **Opus 5.5, effort `high`** (`xhigh`/`max` for design-heavy hero parts) | Best measured CAD-code harness result, and Anthropic's default recommendation |
| Per-round render check (cheap, frequent) | **Sonnet 5.5** in a separate context | CADSmith pattern: a separate judge; cheap enough for every round |
| Final design review on design-heavy builds; tie-breaks when Opus loops | **Fable 5.1, effort `high`** | "When evals on Opus 5.5 at higher effort fall short"; stronger long-horizon reasoning. **Unverified for visual taste**, so A/B it |
| Engineering-heavy checks (DFM, sims) | Opus 5.5 | Numbers-driven; vision is secondary |

Honest unknowns: whether Fable 5.1 judges aesthetics better than Opus 5.5, and whether Sonnet 5.5's judgments correlate with user preference. **Action:** build a 20-prompt design eval (humanoid, dog, car, kettle, drone, mold, jet-engine nacelle). Score it by user pairwise preference plus rubric agreement. Pick the judge per class from the data.

---

## 6. Design-intensity classification (D27)
**Score** `d ∈ [0,1]` = max(keyword prior, Haiku rubric score). Store it on the run beside `shape_complexity`.

| Signal | Design-heavy ↑ | Engineering-heavy ↓ |
|---|---|---|
| Who judges success | A person looking at or touching it (consumer, robot, car, furniture, wearable) | A spec, fit or test (bracket, mold, gearbox, manifold, fixture) |
| Words | sleek, beautiful, premium, iconic, friendly, like Optimus/NEO/Tesla/Apple, concept, styling | tolerance, load, torque, ISO, DFM, cooling channel, ejector, flow, stress |
| Visible surfaces | Most of the surface is customer-facing | Hidden or functional |
| Freeform need | Lofts, blends, covers | Prismatic, revolved |

**Classes and process knobs:**

| Knob | Engineering (d<0.3) | Mixed (0.3–0.65) | Design (d>0.65) |
|---|---|---|---|
| Concept stage | Skip, or a 1-view schematic | 1 sketch, 1 revision | Pitch 2–3 directions, then a massing sketch with ≥ 1 revision; approval required |
| Skeleton | Datums and dims | Hardpoints + envelopes | Hardpoints + envelopes + **design curves** (silhouette, beltline/spine) |
| Surfacing rules | Prismatic, fillets for stress | Covers over functional core | Skin-over-skeleton mandatory; no visible bars; G2 on primary surfaces; zebra check |
| Critique weights (silhouette / proportion / surface / details / function / DFM) | 0.1/0.05/0.05/0.1/0.4/0.3 | 0.2/0.15/0.15/0.1/0.2/0.2 | 0.25/0.25/0.2/0.1/0.1/0.1 |
| Views sent to judge | front/top/side + sections | iso + 2 + 1 section | 3/4 hero (perspective), side, front, zebra + zoom crops |
| Critique rounds | ≤ 2 | ≤ 3 | ≤ 4, design review at the end by the stronger judge |
| DFM strictness | Hard gate | Hard gate on structural parts | Warn during design, hard gate before "release" |
| Model | Opus 5.5 medium | Opus 5.5 high | Opus 5.5 high/xhigh + Fable 5.1 final review |

Jet engines and molds are *engineering-heavy with a design shell* (the nacelle). The classifier should allow **per-part** classes: e.g. nacelle d=0.6, fan disk d=0.1.

**Aesthetic judge rubric** (each item scored 0–3, using checkable questions in CADCodeVerify style):
1. Does the front/side silhouette match the approved sketch (IoU ≥ 0.85)?
2. Are proportions within the target units?
3. Are any straight bars, raw servos or loose cables visible?
4. Do zebra stripes break on primary surfaces?
5. Are feature lines continuous across parts?
6. Are panel gaps consistent?
7. Are there ≤ 3 CMF materials, applied to the hierarchy?
8. Does any joint cover collide over its range of motion?
9. Are fasteners and vents placed deliberately?

---

## 7. Pitching aesthetic options (D25)
**Format: one card per direction, 2–3 cards, always including a baseline:**
- **Name + 3 mood words**, e.g. "Monolith: calm, appliance, soft-white" / "Athlete: taut, muscular, exposed-jewel joints" / "Workwear: rugged, modular, high-vis panels".
- **Silhouette pair**: black-fill front and side from a 30-second massing variant. The same skeleton keeps them comparable.
- **NPR 3/4 sketch** with 2–4 SVG callouts of the defining features.
- **Proportion note**: e.g. "8 heads, narrow waist, 1.4:1 shoulder:hip".
- **CMF swatch**: 2–3 colours and finishes.
- **Trade-offs table**: mass Δ, part count, molding complexity (undercuts, split count), cost tier, risk (fit, ROM, thermal), and what the user gives up.
- **Recommendation with one reason.** Ask the user to pick, mix ("A's torso + B's legs") or comment on the sketch.

**Rules** [J]:
- Directions must differ in **silhouette and proportion**, not just colour.
- One option is the "honest engineering" baseline (covers only where needed) so the user sees the cost of styling.
- Don't pitch on engineering-heavy tasks unless asked.
- Re-pitch later only for genuinely open decisions (e.g. head design), max 2 options.

---

## 8. Concrete harness changes (mapped to Plan 9)
| Req | Change |
|---|---|
| D15 | `concept_sketch(build_id, massing_script)` tool: build massing in the worker, render NPR sketch (3/4 + side) plus SVG overlay layer, emit a card. User annotations are stored as overlay JSON (normalised image coordinates + camera) and fed back as data. Approval flag gates `part_create` for design-class runs |
| D16 | New recipes (§2.5). Prompt rule: "visible geometry = covers; links = capsule/station_loft shells; never a straight bar between actuators". `trust()` on all shells. Judge rubric item 3 auto-fails bars (heuristic: long prismatic parts with ≤ 6 faces visible in the hero view) |
| D24 | Smooth shading + perspective + CMF + ground shadow in `render.py`; cover = 3/4 hero at 1024 px |
| D25 | `pitch_options` tool: 2–3 massing variants → silhouettes + sketches + trade-off table → option cards |
| D27 | `design_intensity.py` (Haiku + keyword prior, per part) → knobs table §6 in the orchestrator and prompts |
| D28 | This doc. Next: the 20-prompt design eval with pairwise preference to settle the judge model and image sizes |
| G1 | Separate judge context. Checkable-question rubric. No repeated failed suggestions. Strategy switch at round 3. Judge-requested views and crops. Zebra view in the design class |
| D8 | Mass from `BRepGProp` with eps 1e-6 (and `trust()`), not the default `Shape.volume`, for lofted parts |

---

## Sources (beyond those inline)
- Claude docs: [Vision](https://platform.claude.com/docs/en/build-with-claude/vision), [PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support), [Models overview](https://platform.claude.com/docs/en/models/overview), [Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/overview), [Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview), [Opus 5.5 announcement](https://www.anthropic.com/claude-opus-5-5), [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- build123d: [Surface modeling](https://build123d.readthedocs.io/en/latest/tutorial_surface_modeling.html), [Spitfire Gordon tutorial](https://build123d.readthedocs.io/en/latest/tutorial_spitfire_wing_gordon.html)
- Class-A / zebra: [Wikipedia Class A](https://en.wikipedia.org/wiki/Class_A_surface), [Rhino Zebra](https://docs.mcneel.com/rhino/7/help/en-us/commands/zebra.htm), [Onshape surfacing](https://cad.onshape.com/help/Content/surfacing.htm)
- Car proportions: [Hagerty volumes & proportions](https://www.hagerty.com/media/?p=191742), [Jalopnik dash-to-axle](https://jalopnik.com/2114994/car-design-dash-axle-proportions/), [H-Point book](https://lic.haui.edu.vn/vn/gioi-thieu-sach-moi/h-point-the-fundamentals-of-car-design-packaging/78255)
- DFM: [Stratasys Direct 2017 guide](https://stratasys.com/siteassets/sdm/resources/design-guidelines/injection-molding/injection_molding_design_guidelines_2017.pdf), [Xcentric](https://xcentricmold.com/plastic-injection-molding-best-practices/)
- Test scripts used for the [T] claims: session scratchpad (`snip.py`, `t1.py`–`t6.py`); they are not in the repo. Re-run the §2.3 block in `core/.venv` to reproduce.
