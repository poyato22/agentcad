"""Plan 6 S3 benchmarks with the real CalculiX: orthotropic FDM cantilevers bent with the
layers along vs across the bending stress, and a static study with the Z-strength check and
the fatigue estimate end to end. Needs a real `ccx`."""
import math
from types import SimpleNamespace

import pytest

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.sim.ccx import find_ccx
from mises.sim.fields import Fields
from mises.sim.results import read_glb
from mises.sim.tools import SimRunArgs, sim_run
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")]

BEAM = ('from build123d import *\nVARS={"length":100.0}\ndef build(v):\n'
        '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
L, B, H, F = 100.0, 10.0, 10.0, 10.0
PLA_E, NU = 2300.0, 0.35
TIP = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
       "loads": [{"kind": "force", "face": "x-max", "newtons": F, "direction": "-z"}]}


class _Events:
    def publish(self, ev):
        pass


def _ctx(tmp_path):
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id="r1", events=_Events(), services={},
                           policy=pol, call_id="c1")


async def _static(ctx, material, **extra):
    return await sim_run.handler(ctx, SimRunArgs(type="static", part_id="beam", settings={
        "load_cases": [TIP], "material": material, **extra}))


def _timoshenko(e_bend: float, g: float) -> float:
    """Cantilever tip deflection, bending + shear (kappa 5/6)."""
    inertia = B * H ** 3 / 12
    return F * L ** 3 / (3 * e_bend * inertia) + F * L / (5 / 6 * g * B * H)


@pytest.mark.timeout(400)
async def test_orthotropic_cantilevers_match_the_analytic_layer_ratio(tmp_path):
    """E_z = E / 2. Printed flat (+z) the bending stress runs along the layers (E governs);
    printed standing on its end (+x) it runs across them (E_z governs). Both share the shear
    modulus of the x-z plane (G13 = G23 = E_z / (2 (1 + nu))), so the analytic ratio of the tip
    deflections is the Timoshenko ratio, close to E / E_z = 2."""
    ctx = _ctx(tmp_path)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="beam", script=BEAM))
    assert r.ok, r.summary
    flat = await _static(ctx, {"name": "PLA", "anisotropy": {"print_direction": "+z",
                                                             "e_z_ratio": 0.5}})
    upright = await _static(ctx, {"name": "PLA", "anisotropy": {"print_direction": "+x",
                                                                "e_z_ratio": 0.5}})
    assert flat.ok and upright.ok, (flat.summary, upright.summary)
    g_xz = 0.5 * PLA_E / (2 * (1 + NU))
    d_flat, d_up = _timoshenko(PLA_E, g_xz), _timoshenko(0.5 * PLA_E, g_xz)
    sim_flat = flat.data["results"]["max_displacement_mm"]
    sim_up = upright.data["results"]["max_displacement_mm"]
    assert sim_up / sim_flat == pytest.approx(d_up / d_flat, rel=0.03), (sim_up / sim_flat,
                                                                          d_up / d_flat)
    assert sim_flat == pytest.approx(d_flat, rel=0.08)
    assert sim_up == pytest.approx(d_up, rel=0.08)
    assert "orthotropic" in flat.data["material"]["model"]
    assert "orthotropic FDM" in upright.summary


@pytest.mark.timeout(400)
async def test_layer_strength_and_fatigue_estimate_end_to_end(tmp_path):
    """Printed upright (+x), the root bending stress (6 M / (b h^2) = 6 MPa) pulls the layers
    apart: the Z strength (51 x 0.5 x 0.7 = 17.9 MPa allowable) governs, not von Mises. The
    fatigue hotspot is the same node; its safety factor is the hand calculation on the Z curve
    (S_u,z = 25.5 MPa, 0 -> F: sigma_a = sigma_m = sigma / 2, Goodman)."""
    ctx = _ctx(tmp_path)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="beam", script=BEAM))
    assert r.ok, r.summary
    out = await _static(ctx, {"name": "PLA", "anisotropy": {"print_direction": "+x",
                                                            "strength_z_ratio": 0.5}},
                        fatigue={"cycles": 1e6, "cycle": "zero_to_max"})
    assert out.ok, out.summary
    res = out.data["results"]
    assert res["governing_failure"] == "across layers (Z tension)"
    sig = res["max_layer_tension_mpa"]
    assert sig == pytest.approx(6.0, rel=0.2)
    assert res["allowable_z_mpa"] == pytest.approx(51 * 0.5 * 0.7)
    assert res["safety_factor"] == pytest.approx(res["allowable_z_mpa"] / sig)
    assert res["safety_factor"] < res["safety_factor_von_mises"]
    f = res["fatigue"]
    su = 51 * 0.5
    sar = (sig / 2) / (1 - (sig / 2) / su)
    allow = 0.1 * su * (1e6 / 2e6) ** (-1 / 5.5)
    assert f["governing"] == "across layers (Z)"
    assert f["safety_factor"] == pytest.approx(allow / sar, rel=1e-6)
    assert f["cycles_to_failure"] == pytest.approx(2e6 * (sar / (0.1 * su)) ** -5.5, rel=1e-6)
    assert res["fatigue_safety_factor"] == f["safety_factor"] and f["label"] == "fatigue estimate"
    assert "FATIGUE ESTIMATE" in out.summary
    assert any("FATIGUE ESTIMATE" in w for w in out.data["warnings"]) == (f["safety_factor"] < 1)
    card = out.data["card"]
    assert {"layer_safety_factor", "fatigue_safety_factor"} <= {m["key"] for m in card["metrics"]}
    assert card["material_basis"] == "datasheet / estimate"
    sim_dir = tmp_path / "parts" / "beam" / "sims" / out.data["sim_id"]
    gltf, _ = read_glb(sim_dir / "result.glb")
    names = {c["name"] for c in gltf["asset"]["extras"]["mises_sim"]["channels"]}
    assert {"layer_tension", "fatigue_safety_factor", "safety_factor"} <= names
    assert {"layer_tension", "fatigue_safety_factor"} <= set(Fields(sim_dir / "fields.npz")
                                                            .channels())
    assert not math.isnan(f["hotspot_at"][0]) and f["hotspot_at"][0] < 50  # near the root
