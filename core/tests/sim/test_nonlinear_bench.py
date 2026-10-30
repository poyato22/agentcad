"""Nonlinear static benchmarks against analytic solutions, on fine meshes with the real
CalculiX: the validated model error bands in mises.sim.nonlinear come from these. Minutes long,
so they are excluded from the default run: `pytest -m fem_slow` or MISES_FEM_SLOW=1. The fast,
coarse versions run by default in test_nonlinear.py."""
import math

import numpy as np
import pytest
from build123d import Align, Box, export_step

from mises.sim import nonlinear as nlm
from mises.sim.ccx import find_ccx, run_ccx
from mises.sim.deck import Setup, resolve_loads, write_deck
from mises.sim.materials import custom_material
from mises.sim.mesh import mesh_step
from mises.sim.settings import ForceLoad, NonlinearSettings

from .test_nonlinear import (BEAM, CASE, _ctx, _fields, _part, _run, elastica, hertz_study,
                             tip_load)

pytestmark = [pytest.mark.fem_slow,
              pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")]


@pytest.mark.timeout(900)
async def test_bench_large_deflection_cantilever_vs_elastica(tmp_path):
    """PL^2/EI = 3 (tip rotation ~ 60 degrees): tip deflection within 5% of the elastica."""
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "beam", {
        "load_cases": [{**CASE, "loads": [{**CASE["loads"][0], "newtons": tip_load(3.0)}]}],
        "material": {"name": "PLA"}, "mesh": {"size_mm": 2.5},
        "nonlinear": {"large_deflection": True, "increments": 12}})
    assert out.ok, out.summary
    u = _fields(tmp_path, "beam", out).probe([[100.0, 5.0, 5.0]])[0]["values"]["displacement"]
    dv, dh = elastica(3.0)
    err = (-u[2] / 100 - dv) / dv
    print(f"\nelastica PL^2/EI=3: FE {-u[2] / 100:.4f} L, elastica {dv:.4f} L, error {err:+.2%}; "
          f"shortening FE {-u[0] / 100:.4f} vs {dh:.4f}")
    assert abs(err) < nlm.MODEL_ERROR_BAND["large_deflection"]
    assert -u[0] / 100 == pytest.approx(dh, rel=0.05)


@pytest.mark.timeout(900)
async def test_bench_plastic_hinge_load(tmp_path):
    """A cantilever of an elastic-perfectly-plastic material collapses at the plastic hinge
    load P = sy b h^2 / (4 L). The root holds only u_x (plus the two lines that stop rigid
    motion) so the clamp does not add the plane-strain constraint beam theory leaves out."""
    sy, L, b = 40.0, 100.0, 10.0
    hinge = sy * b * b ** 2 / 4 / L
    d = tmp_path / "job"
    d.mkdir()
    export_step(Box(L, b, b, align=(Align.MIN,) * 3), str(d / "model.step"))
    metrics = {"bbox": {"min": [0, 0, 0], "max": [L, b, b], "size": [L, b, b]}}
    mesh = await mesh_step(d / "model.step", d, 2.0, timeout_s=300)
    m = custom_material({"name": "Flow", "e_mpa": 2300, "nu": 0.35, "yield_mpa": sy / 0.7,
                         "density_g_cm3": 1.24, "tangent_modulus_mpa": 0.5})
    s = Setup()
    root = [n for n, p in mesh.nodes.items() if abs(p[0]) < 1e-9]
    mid_z = [n for n in root if abs(mesh.nodes[n][2] - b / 2) < 1e-6]
    mid_y = [n for n in root if abs(mesh.nodes[n][1] - b / 2) < 1e-6]
    assert mid_z and mid_y
    s.cards += ["*NSET, NSET=ROOT", *map(str, root), "*NSET, NSET=RZ", *map(str, mid_z),
                "*NSET, NSET=RY", *map(str, mid_y)]
    s.boundary += ["ROOT,1,1", "RZ,3,3", "RY,2,2"]
    s.held |= set(root)
    resolve_loads(mesh, metrics, [ForceLoad(face="x-max", newtons=1.5 * hinge,
                                            direction="-z")], s)
    nl = NonlinearSettings(plasticity=True, increments=10, min_increment=1e-4)
    model = nlm.NlModel()
    write_deck(d / "job.inp", mesh, m, s.cards, nlm.nonlinear_step(s, nl, model), "hinge",
               nlm.plastic_cards(m))
    await run_ccx(find_ccx(), d, timeout_s=800, allow_partial=True)
    reached = nlm.load_reached(nlm.parse_sta(d / "job.sta"))
    collapse = reached * 1.5 * hinge
    err = (collapse - hinge) / hinge
    print(f"\nplastic hinge: FE collapse {collapse:.1f} N, analytic {hinge:.1f} N, "
          f"error {err:+.2%} ({len(mesh.nodes)} nodes)")
    assert 0 < err < nlm.MODEL_ERROR_BAND["plasticity"]


@pytest.mark.timeout(3600)
async def test_bench_hertz_peak_pressure(tmp_path):
    """Hertz: an elastic sphere on a rigid flat, contact radius a = R / 10: the peak contact
    pressure within 15% of p0 = 3 F / (2 pi a^2)."""
    out, F, a, p0 = await hertz_study(tmp_path, size=2.5, fine=0.2, timeout_s=1700)
    assert out.ok, out.summary
    r = out.data["results"]
    err = (r["max_contact_pressure_mpa"] - p0) / p0
    f = _fields(tmp_path, "ball", out)
    # the contact patch radius from the pressure along the x axis
    xs = np.linspace(0, 2 * a, 41)
    pr = [f.probe([[x, 0.0, -math.sqrt(100.0 - x * x) + 0.01]])[0]["values"].get(
        "contact_pressure") or 0.0
          for x in xs]
    edge = max((x for x, p in zip(xs, pr) if p > 0.1 * p0), default=0.0)
    print(f"\nHertz: FE peak {r['max_contact_pressure_mpa']:.1f} MPa, p0 {p0:.1f} MPa, error "
          f"{err:+.2%}; contact radius ~{edge:.2f} mm vs a {a:.2f} mm")
    assert abs(err) < nlm.MODEL_ERROR_BAND["contact"]
    # p falls to 10% of p0 at r = 0.995 a; nodal interpolation smears it by about an element
    assert edge == pytest.approx(a, abs=0.25)
    assert math.isfinite(F)
