"""Gear strength study (GEARS) against Shigley's worked examples and hand calculations.

Shigley's Mechanical Engineering Design (9th/10th ed.), ch. 14:
- Ex. 14-1: 16T, P = 8 /in, F = 1.5 in, 1200 rpm, Y = 0.296, cut profile: K_v = 1.524 and
  W_t = 364 lbf gives sigma = 10 kpsi (Lewis).
- Ex. 14-4: 17T pinion / 52T gear, P = 10 /in, F = 1.5 in, 1800 rpm, 4 hp, Qv 6, commercial
  enclosed, steel 240 HB / 200 HB, R = 0.90, 1e8 pinion cycles: W_t = 164.8 lbf, K_v = 1.377,
  K_s = 1.043 (pinion), K_m = 1.22, sigma_P = 6417 psi, sigma_G = 4855 psi (J_P 0.30, J_G 0.40),
  sigma_c = 70 360 psi (C_p = 2300), S_F(pinion) = 5.62.
"""
import asyncio
import math

import pytest

from mises.sim import gears as G
from mises.sim.gear_tools import GearStrengthArgs, gear_strength, run_gear_strength

PSI = 0.00689476          # MPa per psi
LBF = 4.44822             # N per lbf
IN = 25.4


def test_lewis_shigley_14_1():
    v = math.pi * 2.0 * 1200 / 12 / G.FT_MIN_PER_M_S        # 628.3 ft/min in m/s
    kv = G.kv_barth(v, "cut")
    assert kv == pytest.approx(1.524, abs=0.002)
    s = G.lewis_bending_mpa(364 * LBF, 1.5 * IN, IN / 8, 0.296, kv)
    assert s / PSI == pytest.approx(10_000, rel=0.005)


def _ex14_4(**kw):
    steel240 = G.gear_material("Steel 4140", hardness_hb=240)
    steel200 = G.gear_material("Steel 4140", hardness_hb=200)
    # Shigley's E = 30 Mpsi, nu = 0.30 (C_p = 2290 sqrt(psi), rounded to 2300 in the book)
    steel240 = G.replace(steel240, e_mpa=30e6 * PSI, nu=0.30)
    steel200 = G.replace(steel200, e_mpa=30e6 * PSI, nu=0.30)
    opt = G.MeshOptions(qv=6, mounting="commercial", reliability=0.90)
    t = 4 * 745.7 / (1800 * 2 * math.pi / 60)
    p = G.Member("pinion", 17, steel240, 1800.0)
    g = G.Member("gear", 52, steel200, 1800.0 * 17 / 52)
    return G.check_mesh(p, g, t, IN / 10, 1.5 * IN, opt,
                        j_override={"pinion": 0.30, "gear": 0.40}, **kw)


def test_agma_shigley_14_4_factors_and_stresses():
    r = _ex14_4()
    f = r["factors"]
    assert r["tangential_load_n"] / LBF == pytest.approx(164.8, rel=0.003)
    assert r["pitch_line_velocity_m_s"] * G.FT_MIN_PER_M_S == pytest.approx(801.1, rel=0.002)
    assert f["dynamic_kv"] == pytest.approx(1.377, abs=0.002)
    assert f["size_ks_pinion"] == pytest.approx(1.043, abs=0.002)
    assert f["load_distribution_km"] == pytest.approx(1.22, abs=0.003)
    assert f["reliability_kr"] == pytest.approx(0.85)
    pin, gear = r["gears"]
    assert pin["bending_stress_mpa"] / PSI == pytest.approx(6417, rel=0.005)
    assert gear["bending_stress_mpa"] / PSI == pytest.approx(4855, rel=0.006)
    # the book rounds C_p to 2300; with the exact 2290.6 the stress is 0.4% lower
    assert pin["contact_stress_mpa"] / PSI * 2300 / f["elastic_ze_sqrt_mpa"] * PSI ** 0.5 \
        == pytest.approx(70_360, rel=0.005)
    assert f["elastic_ze_sqrt_mpa"] / PSI ** 0.5 == pytest.approx(2290.6, rel=0.002)


def test_agma_shigley_14_4_safety_factors():
    opt_life = {"life_hours": 1e8 / (1800 * 60)}      # 1e8 pinion cycles
    r = _ex14_4()
    r_life = G.check_mesh(
        G.Member("pinion", 17, G.replace(G.gear_material("Steel", hardness_hb=240),
                                         e_mpa=30e6 * PSI, nu=0.3), 1800.0),
        G.Member("gear", 52, G.replace(G.gear_material("Steel", hardness_hb=200),
                                       e_mpa=30e6 * PSI, nu=0.3), 1800.0 * 17 / 52),
        r["driver_torque_nm"], IN / 10, 1.5 * IN,
        G.MeshOptions(qv=6, mounting="commercial", reliability=0.90, **opt_life),
        j_override={"pinion": 0.30, "gear": 0.40})
    pin = r_life["gears"][0]
    assert pin["life_factors"]["y_n"] == pytest.approx(0.977, abs=0.001)
    assert pin["life_factors"]["z_n"] == pytest.approx(0.948, abs=0.001)
    # S_t(240 HB) = 77.3 HB + 12 800 = 31 350 psi: S_F = 31 350 x 0.977 / (0.85 x 6417) = 5.62
    assert pin["bending_allowable_mpa"] / PSI == pytest.approx(31_350, rel=0.003)
    assert pin["bending_sf"] == pytest.approx(5.62, abs=0.04)
    # S_c(240 HB) = 322 HB + 29 100 = 106 380 psi; hand calc with this sigma_c
    sc = pin["contact_allowable_mpa"]
    assert sc / PSI == pytest.approx(106_380, rel=0.003)
    assert pin["contact_sf"] == pytest.approx(
        sc * 0.9485 / (0.85 * pin["contact_stress_mpa"]), rel=0.002)
    assert r["gears"][0]["governing"] in ("bending", "contact")


def test_hertz_forms_agree():
    """The pitch-point radius form (eq. 14-14) equals the AGMA I-factor form (eq. 14-16)."""
    st = G.gear_material("Steel 4140")
    p, g = G.Member("p", 20, st), G.Member("g", 50, st)
    r = G.check_mesh(p, g, 10.0, 2.0, 20.0, G.MeshOptions())
    k = r["factors"]["load_distribution_km"] * r["factors"]["size_ks_pinion"]
    direct = G.hertz_contact_mpa(r["tangential_load_n"], 20.0, 20.0, 40.0, 100.0,
                                 r["factors"]["elastic_ze_sqrt_mpa"], k)
    assert r["gears"][0]["contact_stress_mpa"] == pytest.approx(direct, rel=1e-4)
    ring = G.Member("r", 80, st, internal=True)
    ri = G.check_mesh(p, ring, 10.0, 2.0, 20.0, G.MeshOptions())
    k = ri["factors"]["load_distribution_km"] * ri["factors"]["size_ks_pinion"]
    direct = G.hertz_contact_mpa(ri["tangential_load_n"], 20.0, 20.0, 40.0, 160.0,
                                 ri["factors"]["elastic_ze_sqrt_mpa"], k, internal=True)
    assert ri["gears"][0]["contact_stress_mpa"] == pytest.approx(direct, rel=1e-4)
    assert ri["gears"][0]["contact_stress_mpa"] < r["gears"][0]["contact_stress_mpa"] * 1.0


def test_contact_ratio_hand_calc():
    # 20/40, m 1, 20 deg: L = 5.718 + 9.370 - 10.261 = 4.827; p_b = pi cos 20 = 2.952
    c = G.contact_ratio(20, 40, 1.0)
    assert c["profile"] == pytest.approx(1.635, abs=0.003)
    from mises.mech.sizing import contact_ratio as old
    assert c["profile"] == pytest.approx(old(20, 40, 1.0), rel=1e-9)
    h = G.contact_ratio(20, 40, 1.0, helix_deg=20, b_mm=10)
    assert h["face"] == pytest.approx(10 * math.tan(math.radians(20)) / math.pi, rel=1e-9)
    # internal 20 / 80: hand calc of the same line-of-action form
    r1, r2, a = 10.0, 40.0, math.radians(20)
    la = (math.sqrt(11 ** 2 - (r1 * math.cos(a)) ** 2) - math.sqrt(39 ** 2 - (r2 * math.cos(a))
          ** 2) + 30 * math.sin(a))
    assert G.contact_ratio(20, 80, 1.0, internal=True)["profile"] == pytest.approx(
        la / (math.pi * math.cos(a)), rel=1e-9)


def test_geometry_factor_table_matches_shigley_example():
    assert G.geometry_factor_j(17, 52) == pytest.approx(0.30, abs=0.01)
    assert G.geometry_factor_j(52, 17) == pytest.approx(0.40, abs=0.01)
    assert G.geometry_factor_j(12, 40) < G.geometry_factor_j(17, 40)     # undercut weaker
    assert G.geometry_factor_j(300, 300) == pytest.approx(0.49)          # clamped, conservative


def test_kv_agma_and_kr():
    assert G.kv_agma(0.0, 6) == pytest.approx(1.0)
    assert G.reliability_kr(0.99) == pytest.approx(1.0)
    assert G.reliability_kr(0.999) == pytest.approx(1.25)


def test_undercut_and_backlash():
    assert G.undercut_min_teeth(20) == pytest.approx(17.1, abs=0.05)
    b = G.backlash_mm(60.0, 2.0, printed=False)
    assert b["min_normal_backlash_mm"] == pytest.approx(2 / 3 * (0.06 + 0.03 + 0.06), abs=1e-3)
    assert G.backlash_mm(20.0, 1.0, printed=True)["recommended_mm"] == pytest.approx(0.15)


def test_tool_pair_printed_pla_flags_undercut_and_reports():
    a = GearStrengthArgs(module_mm=1.0, z1=12, z2=36, face_width_mm=6.0, torque_nm=0.3,
                         rpm=300, material="PLA")
    r = run_gear_strength(a)
    assert r["estimate"] is True and "analytic estimate" in r["fidelity_label"]
    assert r["weakest"]["gear"] == "gear 1"                 # the 12T pinion
    assert any("undercut" in w for w in r["warnings"])
    assert r["meshes"][0]["factors"]["dynamic_basis"].startswith("Barth")
    assert 0.85 < r["efficiency_estimate"] < 0.97            # dry PLA
    assert r["meshes"][0]["backlash"]["recommended_mm"] >= 0.15
    assert r["ratio"] == pytest.approx(3.0)
    assert "PLA" in r["materials"] and "tensile" in r["materials"]["PLA"]["source"]
    # PLA S_t = 0.25 x 51 = 12.75 -> 12.8; sigma = 600 N ... hand calc of the bending SF
    m = r["meshes"][0]
    g1 = m["gears"][0]
    f = m["factors"]
    sigma = (m["tangential_load_n"] * f["overload_ko"] * f["dynamic_kv"]
             * f["load_distribution_km"] * g1["size_factor_ks"] / (6.0 * 1.0
                                                                    * g1["geometry_factor_j"]))
    assert g1["bending_stress_mpa"] == pytest.approx(sigma, rel=5e-3)
    assert g1["bending_sf"] == pytest.approx(12.8 / sigma, rel=5e-3)
    assert g1["static_bending_sf"] == pytest.approx(51.0 / sigma, rel=5e-3)
    assert r["weakest"]["static_bending_sf"] == g1["static_bending_sf"]


def test_tool_power_rpm_equals_torque():
    a = GearStrengthArgs(module_mm=1.5, z1=20, z2=40, face_width_mm=8, power_w=50, rpm=600,
                         material="POM")
    b = GearStrengthArgs(module_mm=1.5, z1=20, z2=40, face_width_mm=8,
                         torque_nm=50 / (600 * 2 * math.pi / 60), rpm=600, material="POM")
    assert run_gear_strength(a)["weakest"] == run_gear_strength(b)["weakest"]


def test_stronger_material_and_wider_face_raise_sf():
    base = dict(module_mm=1.0, z1=20, z2=40, face_width_mm=6.0, torque_nm=0.5, rpm=200)
    pla = run_gear_strength(GearStrengthArgs(**base, material="PLA"))["weakest"]["min_sf"]
    pacf = run_gear_strength(GearStrengthArgs(**base, material="PA-CF"))["weakest"]["min_sf"]
    steel = run_gear_strength(GearStrengthArgs(**base, material="Steel 4140"))["weakest"][
        "min_sf"]
    wide = run_gear_strength(GearStrengthArgs(**{**base, "face_width_mm": 12.0},
                                              material="PLA"))["weakest"]["min_sf"]
    assert pla < pacf < steel and wide > pla


def test_helical_pair():
    a = GearStrengthArgs(module_mm=1.0, z1=20, z2=40, face_width_mm=8, torque_nm=0.5, rpm=500,
                         helix_angle_deg=20, material="PETG")
    m = run_gear_strength(a)["meshes"][0]
    assert m["axial_load_n"] == pytest.approx(m["tangential_load_n"] * math.tan(
        math.radians(20)), rel=1e-3)
    assert m["contact_ratio"]["face"] > 0
    assert m["center_distance_mm"] == pytest.approx(30 / math.cos(math.radians(20)), rel=1e-3)


def test_train_passes_torque_down():
    a = GearStrengthArgs(kind="train", module_mm=1.0, face_width_mm=6, torque_nm=0.2, rpm=1000,
                         material="PA12",
                         stages=[{"z1": 12, "z2": 36}, {"z1": 15, "z2": 45, "module_mm": 1.5,
                                                         "face_width_mm": 10}])
    r = run_gear_strength(a)
    m1, m2 = r["meshes"]
    assert m2["driver_torque_nm"] == pytest.approx(0.2 * 3 * m1["efficiency"], rel=1e-3)
    assert r["ratio"] == pytest.approx(9.0) and r["output_rpm"] == pytest.approx(1000 / 9)
    assert r["efficiency_estimate"] == pytest.approx(m1["efficiency"] * m2["efficiency"],
                                                     rel=1e-3)


def test_planetary_each_mesh():
    a = GearStrengthArgs(kind="planetary", module_mm=1.0, face_width_mm=8, z_sun=12,
                         z_planet=18, z_ring=48, n_planets=3, torque_nm=0.5, rpm=600,
                         material="PETG")
    r = run_gear_strength(a)
    sp, pr = r["meshes"]
    # equal tangential load on both meshes: 2 T / (d_s n) x K_gamma(3) = 1.16
    assert sp["tangential_load_n"] == pytest.approx(2 * 500 / 12 / 3 * 1.16, rel=1e-3)
    assert pr["tangential_load_n"] == pytest.approx(sp["tangential_load_n"], rel=1e-6)
    assert pr["internal"] is True
    assert r["planetary"]["ratio"] == pytest.approx(5.0)
    assert r["planetary"]["carrier_rpm"] == pytest.approx(120.0)
    planet = next(g for g in sp["gears"] if g["gear"] == "planet")
    assert planet["reversed_bending"] and planet["bending_allowable_mpa"] == pytest.approx(
        0.7 * G.gear_material("PETG").bend_allow_mpa, rel=1e-3)
    assert r["weakest"]["gear"] in ("sun", "planet", "ring")
    assert r["planetary"]["assembly_problems"] == []
    assert any("sun" in w and "undercut" in w for w in r["warnings"])


def test_metal_life_class_and_unknown_material():
    a = GearStrengthArgs(module_mm=2.0, z1=20, z2=60, face_width_mm=20, torque_nm=20, rpm=1500,
                         material="Steel 4140 QT", life_hours=5000)
    r = run_gear_strength(a)
    life = r["weakest"]["life"]
    assert life["cycles_to_failure"] and "cycles" in life["class"]
    with pytest.raises(Exception, match="unknown gear material"):
        run_gear_strength(GearStrengthArgs(module_mm=1, z1=20, z2=40, face_width_mm=5,
                                           torque_nm=1, material="cheese"))


def test_overloaded_fails():
    r = run_gear_strength(GearStrengthArgs(module_mm=0.5, z1=12, z2=24, face_width_mm=3,
                                           torque_nm=2.0, rpm=100, material="PLA"))
    assert r["verdict"] == "fails" and r["weakest"]["min_sf"] < 1


def test_tool_registered_and_listed(tmp_path):
    from mises.runtime import Runtime
    from mises.sim.tools import sim_types_list
    rt = Runtime(tmp_path / "home")
    assert rt.registry.list(["gear_strength"])
    out = asyncio.run(sim_types_list.handler(None, sim_types_list.input_model()))
    ids = [t["id"] for t in out.data["analytic"]]
    assert "gear_strength" in ids and "gear_strength" in out.summary
    res = asyncio.run(gear_strength.handler(None, GearStrengthArgs(
        module_mm=1, z1=20, z2=40, face_width_mm=6, torque_nm=0.3, material="POM")))
    assert "analytic estimate" in res.summary


def test_agents_are_told_to_run_it():
    from mises.agent.definitions import load_definitions
    from mises.orchestrator.lead import LEAD_ENGINEERING, LEAD_TOOLS
    assert "gear_strength" in LEAD_TOOLS and "gear_strength" in LEAD_ENGINEERING
    defs = load_definitions([])
    for name in ("sim", "simulation"):
        assert "gear_strength" in defs[name].tools and "gear_strength" in defs[name].body
    assert "gear_strength" in defs["engineer"].body
