"""Impact / drop dynamics (sim/impact.py, impact_fem.py, impact_tools.py): known answers."""
from __future__ import annotations

import asyncio
import math

import pytest

from mises.sim.ccx import find_ccx
from mises.sim.impact import (CUSHIONS, G, Cushion, Layer, descent, egg_g_limit,
                              free_fall_velocity, lumped_impact, parachute_cd_area,
                              spring_mass_impact, terminal_velocity)
from mises.sim.impact_fem import crush_hardening, parse_dat, write_impact_deck
from mises.sim.impact_tools import ImpactArgs, build_layers, register_impact_tools, run_impact
from mises.tools.registry import ToolRegistry


# ---- descent: free fall, terminal velocity, drag

@pytest.mark.parametrize("h", [0.5, 2.0, 10.0])
def test_free_fall_impact_velocity_is_sqrt_2gh(h):
    assert free_fall_velocity(h) == pytest.approx(math.sqrt(2 * 9.81 * h), rel=1e-12)
    d = descent(0.1, h)                       # no drag area: free fall
    assert d["impact_velocity_m_s"] == pytest.approx(math.sqrt(2 * 9.81 * h), rel=1e-12)
    assert d["fall_time_s"] == pytest.approx(math.sqrt(2 * h / 9.81), rel=1e-12)
    assert d["terminal_velocity_m_s"] is None


def test_terminal_velocity_formula():
    # v_t = sqrt(2 m g / (rho Cd A)): 0.1 kg, CdA 0.1 m2 -> 4.0018 m/s
    assert terminal_velocity(0.1, 0.1) == pytest.approx(
        math.sqrt(2 * 0.1 * 9.81 / (1.225 * 0.1)), rel=1e-12)
    assert terminal_velocity(0.1, 0.1) == pytest.approx(4.0018, abs=1e-3)
    assert parachute_cd_area(0.5, 1.5) == pytest.approx(1.5 * math.pi * 0.0625)


def test_descent_matches_a_numerical_integration_and_tends_to_terminal():
    m, cda, h = 0.12, 0.05, 8.0
    rho, vt = 1.225, terminal_velocity(0.12, 0.05)
    # integrate m dv/dt = m g - 1/2 rho CdA v^2 to the ground
    v = y = t = 0.0
    dt = 1e-5
    while y < h:
        a = 9.81 - 0.5 * rho * cda * v * v / m
        v += a * dt
        y += v * dt
        t += dt
    d = descent(m, h, cd_area_m2=cda)
    assert d["impact_velocity_m_s"] == pytest.approx(v, rel=2e-3)
    assert d["fall_time_s"] == pytest.approx(t, rel=2e-3)
    assert descent(m, 500.0, cd_area_m2=cda)["impact_velocity_m_s"] == pytest.approx(vt, rel=1e-6)
    # a parachute slows it
    assert descent(m, h, cd_area_m2=cda, parachute_diameter_m=0.4)["impact_velocity_m_s"] < v


# ---- the lumped transient against the analytic spring-mass impact

def _spring(m_kg, k_n_m, damping=0.0):
    """A one-part stack: mass m on a linear spring k (e A / t = k)."""
    area, t = 1e-4, 0.01
    return Layer("mass", m_kg, area, t, e_mpa=k_n_m * t / area / 1e6, damping=damping)


@pytest.mark.parametrize("m,k,v", [(0.06, 2e4, 3.0), (1.0, 5e5, 5.0), (0.2, 1e3, 1.0)])
def test_rigid_mass_on_linear_spring_peak_deceleration(m, k, v):
    r = lumped_impact([_spring(m, k)], v, g=0.0)
    a = spring_mass_impact(m, k, v)
    assert a["peak_accel_m_s2"] == pytest.approx(v * math.sqrt(k / m))   # the closed form
    assert r.peak_g == pytest.approx(a["peak_g"], rel=0.01)
    assert r.max_compression_m["mass"] == pytest.approx(v * math.sqrt(m / k), rel=0.01)
    assert r.contact_time_s == pytest.approx(math.pi * math.sqrt(m / k), rel=0.02)
    assert r.restitution == pytest.approx(1.0, abs=0.02)                 # elastic: bounces


def test_spring_with_gravity_matches_the_energy_balance():
    m, k, v = 0.5, 4e4, 4.0
    r = lumped_impact([_spring(m, k)], v)
    a = spring_mass_impact(m, k, v, g=9.81)
    assert r.peak_g == pytest.approx(a["peak_g"], rel=0.01)
    assert r.max_compression_m["mass"] == pytest.approx(a["max_compression_m"], rel=0.01)


def test_crushable_foam_plateau_force_absorbs_the_energy():
    foam = Cushion("test_foam", 5.0, 0.1, 0.7, 20.0, damping_ratio=0.0)
    m, area, t, v = 0.5, 0.0025, 0.05, 3.0
    r = lumped_impact([Layer("foam_and_mass", m, area, t, cushion=foam)], v)
    # a flat plateau of 0.1 MPa on 25 cm2 = 250 N: felt g = F / (m g)
    assert r.peak_g == pytest.approx(250.0 / (m * G), rel=0.03)
    stroke = r.max_compression_m["foam_and_mass"]
    # energy: 1/2 m v^2 + m g x is crushed out under the curve (elastic ramp to 2 % strain,
    # then the plateau)
    work = 250.0 * (stroke - 0.5 * 0.02 * t)
    assert work == pytest.approx(0.5 * m * v * v + m * 9.81 * stroke, rel=0.02)
    assert r.energy_absorbed_j["foam_and_mass"] == pytest.approx(0.5 * m * v * v, rel=0.08)
    # a crushed foam only gives back its elastic unloading energy F^2 / 2k (k = E A / t)
    k_un = 5e6 * area / t
    e_back = 250.0 ** 2 / (2 * k_un)
    assert r.restitution == pytest.approx(math.sqrt(e_back / (0.5 * m * v * v)), rel=0.1)
    assert r.restitution < 0.3
    assert not r.bottomed_out


def test_too_thin_a_cushion_bottoms_out_and_the_g_climbs():
    thin = lumped_impact([Layer("pad", 0.06, 0.0016, 0.004, cushion=CUSHIONS["eps_20"])], 6.0)
    thick = lumped_impact([Layer("pad", 0.06, 0.0016, 0.05, cushion=CUSHIONS["eps_20"])], 6.0)
    assert thin.bottomed_out == ["pad"] and not thick.bottomed_out
    assert thin.peak_g > 1.5 * thick.peak_g


def test_egg_limit():
    assert egg_g_limit() == pytest.approx(35.0 / (0.06 * 9.81))


# ---- the study, the deck and the tool

def _egg_args(**kw):
    return ImpactArgs.model_validate({
        "layers": [{"name": "foam", "mass_g": 5, "footprint_mm": [40], "thickness_mm": 30,
                    "cushion": "eps_20"},
                   {"name": "egg", "mass_g": 60, "footprint_mm": [40], "thickness_mm": 10,
                    "e_mpa": 1000}],
        "payload": "egg", **kw})


def test_impact_study_lumped_reports_g_check_energy_and_label():
    args = _egg_args(drop_height_m=2.0)
    r = asyncio.run(run_impact(args, build_layers(None, args.layers)))
    assert r["impact_velocity_m_s"] == pytest.approx(math.sqrt(2 * 9.81 * 2.0))
    assert r["fidelity_class"] == "impact_lumped" and "ESTIMATE" in r["fidelity"]
    assert r["g_check"]["verdict"] == "fail"          # 30 mm of EPS from 2 m: ~300 g
    assert r["g_check"]["limit_g"] == pytest.approx(egg_g_limit())
    ke = 0.5 * 0.065 * r["impact_velocity_m_s"] ** 2
    assert r["energy_absorbed_j"]["foam"] == pytest.approx(ke, rel=0.1)
    # a parachute and a soft, thick cushion bring it under the limit
    soft = ImpactArgs.model_validate({
        "layers": [{"name": "foam", "mass_g": 5, "footprint_mm": [60], "thickness_mm": 60,
                    "cushion": "pu_soft_foam"},
                   {"name": "egg", "mass_g": 60, "footprint_mm": [40], "thickness_mm": 10,
                    "e_mpa": 1000}],
        "payload": "egg", "drop_height_m": 2.0, "parachute_diameter_m": 0.6})
    r2 = asyncio.run(run_impact(soft, build_layers(None, soft.layers)))
    assert r2["descent"]["impact_velocity_m_s"] < 3.0
    assert r2["g_check"]["verdict"] == "pass"


def test_args_need_one_of_height_or_velocity():
    with pytest.raises(ValueError):
        _egg_args()
    with pytest.raises(ValueError):
        _egg_args(drop_height_m=1.0, impact_velocity_m_s=3.0)


def test_crush_hardening_reproduces_the_nominal_curve():
    c = CUSHIONS["eps_20"]
    for sig, ep in crush_hardening(c)[1:]:
        eps = 1 - math.exp(-(ep + sig / c.e_mpa))      # total true strain -> nominal
        assert sig / (1 - eps) == pytest.approx(c.stress(eps), rel=0.03)


def test_deck_has_dynamics_contact_and_foam():
    args = _egg_args(drop_height_m=1.0)
    deck, blocks = write_impact_deck(build_layers(None, args.layers), 4.4, payload="egg",
                                     duration_s=0.004)
    for card in ("*DYNAMIC", "*CONTACT PAIR", "*RIGID BODY", "*PLASTIC",
                 "*INITIAL CONDITIONS,TYPE=VELOCITY", "*DLOAD", "ELSE"):
        assert card in deck
    assert "BODYN,3,-4400" in deck
    assert len(blocks) == 2 and blocks[1].z0 == pytest.approx(30.0)


def test_parse_dat():
    text = """
 velocities (vx,vy,vz) for set PAYN and time  0.1000000E-03

        65 0.0 0.0 -2.000000E+03
        66 0.0 0.0 -4.000000E+03

 total force (fx,fy,fz) for set FREF and time  0.1000000E-03

        0.0 0.0 1.500000E+02

 total internal energy for set B0 and time  0.1000000E-03

        2.500000E+02

 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set B0 and time  0.1000000E-03

         1   1  0.0 0.0 -1.0 0.0 0.0 0.0
         2   1  0.0 0.0 -3.0 0.0 0.0 0.0
"""
    d = parse_dat(text)
    assert d["v"][1e-4] == [-2000.0, -4000.0]
    assert d["rf"][1e-4] == 150.0
    assert d["else"]["B0"][1e-4] == 250.0
    assert d["smax"]["B0"][1e-4] == (pytest.approx(3.0), 2)


def test_tools_are_registered():
    reg = ToolRegistry()
    register_impact_tools(reg)
    assert reg.get("impact_study").safety.value == "local_mutate"
    assert reg.get("descent_estimate").safety.value == "read"
    assert "egg" in reg.get("impact_study").description
    from mises.sim.tools import register_sim_tools
    reg2 = ToolRegistry()
    register_sim_tools(reg2)                 # agents find it with the other sim tools
    assert reg2.get("impact_study") and reg2.get("descent_estimate")


@pytest.mark.slow
@pytest.mark.timeout(300)
@pytest.mark.skipif(find_ccx() is None, reason="CalculiX not installed")
def test_calculix_impact_agrees_with_the_lumped_model(tmp_path):
    args = _egg_args(drop_height_m=2.0, fidelity="fem")
    r = asyncio.run(run_impact(args, build_layers(None, args.layers), workdir=tmp_path))
    fem = r["fem"]
    assert fem is not None, r["notes"]
    assert r["fidelity_class"] == "impact_fem"
    assert fem["completed"] and fem["peak_floor_force_n"] > 50
    lum = r["lumped"]
    assert fem["peak_g"] == pytest.approx(lum["peak_g"], rel=0.3)
    ke = 0.5 * 0.065 * r["impact_velocity_m_s"] ** 2
    # the crushed foam keeps the energy (plastic dissipation), within 15 %
    assert fem["energy_j"]["foam"]["end_j"] == pytest.approx(ke, rel=0.15)
    assert fem["peak_stress"]["foam"]["von_mises_mpa"] > 0.05
