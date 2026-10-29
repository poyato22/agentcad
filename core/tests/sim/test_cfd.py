"""Built-in wind solver: voxelizer, budgets and fallbacks, a coarse sphere in the default suite,
the worker process and the wind study through sim_run; the sphere and flat-plate validations
are cfd_slow (minutes each; run with -m cfd_slow or MISES_CFD_SLOW=1)."""
import json
import math
import struct

import numpy as np
import pytest

from mises.sim.cfd import cfd_solver_status, run_wind_job
from mises.sim.cfd.results import CD_TABLE, classify, friction_cd, mesh_volume_mm3
from mises.sim.cfd.setup import (fit_budget, frontal_area_mm2, plan_grid, read_stl, voxelize,
                                 wind_frame, yawed)
from mises.sim.cfd.wind import run_study
from mises.sim.fields import Fields
from mises.sim.results import read_glb
from mises.sim.settings import WindSettings
from mises.sim.studies import wind_payload


def sphere(r=50.0, n=32):
    th = np.linspace(0, np.pi, n + 1)
    ph = np.linspace(0, 2 * np.pi, 2 * n + 1)

    def P(t, p):
        return [r * np.sin(t) * np.cos(p), r * np.sin(t) * np.sin(p), r * np.cos(t)]
    tris = []
    for i in range(n):
        for j in range(2 * n):
            a, b = P(th[i], ph[j]), P(th[i + 1], ph[j])
            c, d = P(th[i + 1], ph[j + 1]), P(th[i], ph[j + 1])
            if i > 0:
                tris.append([a, b, d])
            if i < n - 1:
                tris.append([b, c, d])
    return np.array(tris)


def box(sx, sy, sz):
    v = np.array([[x, y, z] for x in (-sx / 2, sx / 2) for y in (-sy / 2, sy / 2)
                  for z in (-sz / 2, sz / 2)])
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, c, d in quads:
        tris += [[v[a], v[b], v[c]], [v[a], v[c], v[d]]]
    return np.array(tris)


def settings(**kw):
    return wind_payload(WindSettings(**kw))


def test_voxelized_sphere_volume_and_frontal_area():
    tris = sphere()
    g = plan_grid(tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0), 16, 3)
    solid = voxelize(tris, g)
    vol = solid.sum() * g.h_mm ** 3
    assert vol == pytest.approx(4 / 3 * math.pi * 50 ** 3, rel=0.1)
    assert frontal_area_mm2(tris) == pytest.approx(math.pi * 50 ** 2, rel=0.03)
    assert mesh_volume_mm3(tris) == pytest.approx(4 / 3 * math.pi * 50 ** 3, rel=0.02)


def test_a_plate_thinner_than_a_cell_does_not_vanish():
    tris = box(2, 100, 100)  # 2 mm thick, cells ~8 mm
    g = plan_grid(tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0), 12, 3)
    solid = voxelize(tris, g)
    assert solid.any(0).sum() == pytest.approx(144, abs=30)  # ~12 x 12 cells face-on
    assert solid.sum(0).max() == 1                           # one cell thick


def test_read_stl_binary_and_ascii(tmp_path):
    tris = box(10, 20, 30)
    b = bytearray(80) + struct.pack("<I", len(tris))
    for t in tris:
        b += struct.pack("<3f", 0, 0, 0) + struct.pack("<9f", *t.ravel()) + b"\0\0"
    (tmp_path / "b.stl").write_bytes(bytes(b))
    assert np.allclose(read_stl(tmp_path / "b.stl"), tris)
    lines = ["solid x"] + [ln for t in tris for ln in
                           ["facet normal 0 0 0", "outer loop",
                            *[f"vertex {p[0]} {p[1]} {p[2]}" for p in t], "endloop",
                            "endfacet"]] + ["endsolid x"]
    (tmp_path / "a.stl").write_text("\n".join(lines))
    assert np.allclose(read_stl(tmp_path / "a.stl"), tris)


def test_wind_frame_and_yaw():
    R = wind_frame([0, 1, 0], [0, 0, 1])
    assert np.allclose(R @ [0, 1, 0], [1, 0, 0]) and np.allclose(R @ [0, 0, 1], [0, 0, 1])
    assert np.allclose(yawed([1, 0, 0], [0, 0, 1], 90), [0, 1, 0])


def test_classify_and_friction():
    assert classify(np.array([100, 100, 100]), 4 / 3 * math.pi * 50 ** 3) == "sphere"
    assert classify(np.array([100, 100, 100]), 1e6) == "cube"
    assert classify(np.array([2, 100, 100]), 2e4) == "flat_plate"
    assert friction_cd(1e4, 4.0, 1.0) == pytest.approx(1.328 / 100 * 0.5 * 4)
    assert friction_cd(1e6, 1.0, 1.0) == pytest.approx(0.5 * 0.074 * 1e6 ** -0.2)


def test_solver_status_rows_for_the_system_check(monkeypatch):
    monkeypatch.setattr("mises.sim.cfd.find_su2", lambda: None)
    monkeypatch.setattr("mises.sim.cfd.find_container_runtime", lambda: None)
    rows = {r["id"]: r for r in cfd_solver_status()}
    assert rows["cfd_builtin"]["status"] == "ok"
    assert rows["su2"]["status"] == "optional_missing" and rows["su2"]["fix"]["kind"] == "open_url"
    assert rows["openfoam_container"]["status"] == "optional_missing"
    for r in rows.values():
        assert {"id", "name", "status", "version", "path", "detail", "fix"} <= set(r)


def test_budget_fallbacks_to_the_empirical_estimate(tmp_path):
    tris = sphere()
    lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    assert fit_budget(lo, hi, 4, 10, 64) is None
    r = run_study(tris, settings(refinement=4, domain_multiplier=10, memory_mb=64), tmp_path)
    assert r["method"] == "empirical" and r["fidelity"] == "estimate"
    assert r["shape_class"] == "sphere" and r["cd"] == CD_TABLE["sphere"]
    q = 0.5 * 1.225 * 10 ** 2
    assert r["drag_n"] == pytest.approx(0.47 * q * r["ref_area_m2"])
    r2 = run_study(tris, settings(refinement=4, timeout_s=5), tmp_path)
    assert r2["method"] == "empirical" and "budget" in r2["reason"]
    with pytest.raises(ValueError):
        run_study(tris, settings(refinement=4, timeout_s=5, solver="lbm"), tmp_path)


@pytest.mark.timeout(120)
def test_coarse_sphere_drag_and_outputs(tmp_path):
    r = run_study(sphere(), settings(speed_m_s=3, refinement=1, domain_multiplier=3,
                                     iterations=1200, yaw_deg=[90]), tmp_path)
    assert r["method"] == "lbm" and r["fidelity"] == "low fidelity"
    assert 0.3 < r["cd"] < 1.3, r["cd"]  # coarse (8 cells): a sanity band only
    assert abs(r["cl"]) < 0.2 and r["history"] and r["lattice"]["cells_across"] == 8
    assert r["drag_n"] == pytest.approx(r["cd"] * r["dynamic_pressure_pa"] * r["ref_area_m2"])
    assert [s["yaw_deg"] for s in r["yaw_sweep"]] == [0.0, 90]
    assert r["yaw_sweep"][1]["cd"] == pytest.approx(r["cd"], rel=0.25)  # a sphere is symmetric
    gltf, colors = read_glb(tmp_path / "result.glb")
    assert gltf["asset"]["extras"]["mises_sim"]["field"] == "cp"
    assert "_CP" in gltf["meshes"][0]["primitives"][0]["attributes"]
    f = Fields(tmp_path / "fields.npz")
    assert set(f.channels()) == {"velocity", "speed", "cp"}
    far = f.probe([[-150.0, 0.0, 0.0]])[0]  # upstream: free stream
    assert far["inside"] and far["values"]["speed"] == pytest.approx(3.0, rel=0.15)
    assert f.probe([[0.0, 0.0, 0.0]])[0]["inside"] is False  # inside the body
    sec = f.section(2, 0.0, "speed")
    assert sec["axis"] == "z" and sec["u"] == "x"
    for name in ("slice_xz.png", "slice_xy.png"):
        assert (tmp_path / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.timeout(120)
async def test_worker_process_runs_sandboxed(tmp_path):
    seen = []
    r = await run_wind_job(box(40, 40, 40), settings(refinement=1, domain_multiplier=3,
                                                     iterations=400, timeout_s=60),
                           tmp_path / "job", progress=seen.append)
    assert r["method"] == "lbm" and r["cd"] > 0.3
    assert (tmp_path / "job" / "result.glb").is_file()
    assert json.loads((tmp_path / "job" / "result.json").read_text())["cd"] == r["cd"]


@pytest.mark.cfd_slow
@pytest.mark.timeout(1500)
def test_validation_sphere_cd_within_25_percent(tmp_path):
    # D = 0.1 m at 3 m/s: Re 2e4, measured Cd ~0.47 (subcritical plateau)
    r = run_study(sphere(n=48), settings(speed_m_s=3, refinement=3, domain_multiplier=5,
                                         iterations=6000, timeout_s=1400), tmp_path)
    print("sphere", {k: r[k] for k in ("cd", "cd_pressure", "cd_lattice_total", "steps")})
    assert abs(r["cd"] - 0.47) / 0.47 < 0.25, r["cd"]


@pytest.mark.cfd_slow
@pytest.mark.timeout(1500)
def test_validation_flat_plate_cd(tmp_path):
    # a square plate normal to the flow: Cd ~1.17 (Hoerner); t/D = 0.04
    r = run_study(box(4, 100, 100), settings(speed_m_s=1.5, refinement=3, domain_multiplier=5,
                                             iterations=6000, timeout_s=1400), tmp_path)
    print("plate", {k: r[k] for k in ("cd", "cd_pressure", "cd_lattice_total", "steps")})
    assert abs(r["cd"] - 1.17) / 1.17 < 0.25, r["cd"]
