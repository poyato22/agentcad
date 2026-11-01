"""Plan 10 C2 (research 2026-11-01 §2.3, §6.4): sizing formulas with known answers, and the
mechanism library: >= 40 mechanisms whose cards build, whose loops close and whose mobility
matches the intended DOF."""
import math

import pytest

from mises.assembly.solve import solve
from mises.mech import library, sizing
from mises.mech.assemble import plan_assembly
from mises.mech.shapes import belt_center, belt_length, mesh_phase, ring_phase
from mises.types import MisesError


# ---- sizing
def test_gt2_20_60_on_a_122_tooth_belt_is_80_997_mm():
    pd1, pd2 = 20 * 2 / math.pi, 60 * 2 / math.pi
    assert pd1 == pytest.approx(12.732, abs=1e-3) and pd2 == pytest.approx(38.197, abs=1e-3)
    # 242.03 mm at an 80 mm centre: 121 teeth is just short, so the next stock loop (122T)
    assert belt_length(pd1, pd2, 80.0) == pytest.approx(242.03, abs=0.01)
    assert belt_center(pd1, pd2, 244.0) == pytest.approx(80.997, abs=0.01)
    assert sizing.belt_teeth_in_mesh(20, pd1, pd2, 80.997) == pytest.approx(9.0, abs=0.05)


def test_belt_drive_card_snaps_to_the_stock_belt():
    m, p, plan = library.plan_for("belt_drive_gt2", {})
    s = plan.sizing
    assert s["belt_teeth"] == 122 and s["center_mm"] == pytest.approx(80.997, abs=0.01)
    assert s["wrap_small_deg"] == pytest.approx(161.9, abs=0.2)


def test_lewis_and_contact_ratio():
    assert sizing.lewis_y(20) == pytest.approx(0.322)
    # F_t = 2 T / d = 2 * 1000 / 20 = 100 N; sigma = 100 / (6 * 1 * 0.322)
    assert sizing.lewis_stress_mpa(1.0, 1.0, 20, 6.0) == pytest.approx(51.76, abs=0.01)
    assert sizing.contact_ratio(20, 40, 1.0) == pytest.approx(1.635, abs=0.01)   # spike: 1.635


def test_grashof_classes():
    assert sizing.grashof([100, 40, 120, 80])["kind"] == "crank-rocker"
    assert sizing.grashof([40, 100, 120, 80])["kind"] == "double crank (drag link)"
    assert not sizing.grashof([100, 70, 40, 50])["grashof"]
    assert "change point" in sizing.grashof([100, 60, 100, 60])["kind"]


def test_screw_and_slider_formulas():
    assert sizing.slider_crank_x(0, 20, 70) == pytest.approx(90.0)
    assert sizing.slider_crank_x(180, 20, 70) == pytest.approx(50.0)
    eta = sizing.screw_efficiency(2.0, 7.0, 0.15)
    assert 0.2 < eta < 0.4                       # Tr8x2 ~0.3 (research §2.2)
    assert sizing.screw_efficiency(8.0, 7.0, 0.15) > 0.5
    assert not sizing.screw_backdrivable(2.0, 7.0) and sizing.screw_backdrivable(8.0, 7.0)
    assert sizing.screw_force_n(0.3, 8.0, 0.65) == pytest.approx(153.2, abs=0.2)
    f = sizing.euler_buckling_n(205000, 8, 300, 1.0)
    assert f == pytest.approx(math.pi ** 2 * 205000 * math.pi * 8 ** 4 / 64 / 300 ** 2)


def test_planetary_rules():
    assert sizing.planetary_ok(12, 12, 36, 3) == []
    assert any("integer" in x for x in sizing.planetary_ok(12, 13, 38, 3))
    assert any("sun + 2" in x for x in sizing.planetary_ok(12, 12, 35, 3))


def test_mesh_phase_formulas():
    # gear 2 opposite a gear 1 tooth: a gap there (half a tooth for even z2, none for odd)
    assert mesh_phase(20, 0, 40, 0) % 9 == pytest.approx(4.5)
    assert mesh_phase(20, 0, 41, 0) % (360 / 41) == pytest.approx(0, abs=1e-9)
    # a planet's ring phase steps by (zs + zr)/n teeth: whole teeth for an assemblable set
    p0 = ring_phase(12, mesh_phase(12, 0, 12, 0), 36, 0)
    p1 = ring_phase(12, mesh_phase(12, 0, 12, 120), 36, 120)
    assert ((p1 - p0) / (360 / 36)) % 1 == pytest.approx(0, abs=1e-9)


def test_lewis_sf_uses_the_materials_table():
    m, p, plan = library.plan_for("spur_pair", {"material": "PLA", "input_torque_nm": 2.0})
    m2, p2, plan2 = library.plan_for("spur_pair", {"material": "Steel 4140", "input_torque_nm": 2.0})
    assert plan2.sizing["pinion_bending_sf"] > 5 * plan.sizing["pinion_bending_sf"]
    assert any("safety factor" in w for w in plan.warnings)


# ---- the library
def test_at_least_forty_mechanisms_with_cards():
    ms = library.all_mechanisms()
    assert len(ms) >= 40
    cats = {m.category for m in ms}
    assert {"gear", "reducer", "belt", "linear", "linkage", "leg", "gripper", "joint",
            "intermittent"} <= cats
    for m in ms:
        assert m.summary and m.functions and m.checks and m.params, m.id


@pytest.mark.parametrize("mech_id", [m.id for m in library.all_mechanisms()])
def test_every_card_builds_closes_and_has_the_intended_mobility(mech_id):
    m, p, plan = library.plan_for(mech_id, {})
    card = library.card_for(m, p, plan)
    assert card.dof == card.dof_intended, (card.mobility_formula, card.warnings)
    assert card.ports and card.joints and card.parts
    res = solve(plan_assembly(plan))
    assert not res.loop_errors
    assert plan.inputs, "a mechanism names its input joint(s)"
    keys = {j.key for j in plan.joints}
    assert set(plan.inputs) <= keys
    for j in plan.joints:
        if j.kind == "revolute":
            assert j.design and j.design.get("bearing"), (mech_id, j.key)


def test_four_bar_card_curve_matches_freudenstein_and_the_solve():
    m, p, plan = library.plan_for("four_bar", {"crank_deg": 60.0})
    card = library.card_for(m, p, plan)
    assert card.sizing["kind"] == "crank-rocker"
    asm = plan_assembly(plan)
    jin = next(j for j in asm.joints if j.id.endswith("crank_o2"))
    jr = next(j for j in asm.joints if j.id.endswith("rocker_o4"))
    curve = dict((round(t), v) for t, v in card.transmission["curve"])
    from mises.assembly.solve import solve_from
    prev = {j.id: j.value for j in asm.joints}
    for t in (60, 80, 100, 120):
        moved = asm.model_copy(update={"joints": [x.model_copy(update={"value": float(t)})
                                                  if x.id == jin.id else x for x in asm.joints]})
        res = solve_from(moved, prev)
        prev = {**{x.id: x.value for x in moved.joints}, **res.solved}
        want = sizing.freudenstein_rocker(t, 40, 120, 80, 100, card.sizing["branch"])
        assert abs(((res.applied[jr.id] - want) + 180) % 360 - 180) < 0.01
    assert 60 in curve


def test_jansen_foot_path_has_a_stride():
    m, p, plan = library.plan_for("jansen_leg", {})
    assert plan.sizing["stride_mm"] > 40 and plan.sizing["lift_mm"] > 10


def test_params_out_of_range_and_unknown_are_refused():
    with pytest.raises(MisesError, match="outside"):
        library.plan_for("spur_pair", {"z1": 3})
    with pytest.raises(MisesError, match="no parameter"):
        library.plan_for("spur_pair", {"teeth": 20})
    with pytest.raises(MisesError, match="integer"):
        library.plan_for("planetary", {"zp": 13})
    with pytest.raises(MisesError):
        library.get("warp_drive")


@pytest.mark.parametrize("words,expect", [
    ("convert rotation to linear", {"rack_pinion", "lead_screw_axis", "slider_crank",
                                    "belt_linear_axis"}),
    ("high reduction for a robot joint", {"planetary_compound", "cycloidal_reducer",
                                          "strain_wave"}),
    ("walking leg", {"jansen_leg", "quadruped_leg_belt", "quadruped_leg_linkage"}),
    ("gripper", {"parallel_gripper", "linkage_gripper"}),
])
def test_rank_by_function(words, expect):
    top = {r["id"] for r in library.rank(words, limit=8)}
    assert top & expect, top


def test_gear_couplings_follow_tooth_counts():
    m, p, plan = library.plan_for("spur_pair", {"z1": 15, "z2": 45})
    out = next(j for j in plan.joints if j.coupling)
    assert out.coupling["ratio"] == pytest.approx(-1 / 3)
    m, p, plan = library.plan_for("planetary", {})
    carrier = next(j for j in plan.joints if j.key == "carrier_rev")
    assert carrier.coupling["ratio"] == pytest.approx(12 / 48)      # 1 / (1 + 36/12)
    assert plan.sizing["ratio"] == 4
