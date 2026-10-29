"""Pure pieces of the study types: settings schemas and validation, materials, deck helpers,
.dat parsing, probe interpolation, the convergence loop, compare, templates, charts, cards,
requirement checks and the design-review plan. No solver needed."""
import math

import numpy as np
import pytest

from mises.sim import studies
from mises.sim.charts import charts
from mises.sim.deck import fit_axis, _moment_loads
from mises.sim.drop import impact_direction, impact_newtons
from mises.sim.fem import FemJob, level_sizes, mesh_options, run_levels
from mises.sim.fields import Fields, principal_stresses, save_fem, save_grid, von_mises_np
from mises.sim.materials import (FDM_KNOCKDOWN, custom_material, delete_custom, fdm_factors,
                                 get_material, load_custom, upsert_custom)
from mises.sim.mesh import Mesh, Surface
from mises.sim.results import parse_dat_table
from mises.sim.settings import ConvergenceSettings, MeshSettings, SolverSettings
from mises.sim.tools import plan_review
from mises.sim.types import SimError

CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}


# ---- settings

def test_every_type_has_a_schema_with_units_ranges_and_docs():
    ts = {t["id"]: t for t in studies.types_list()}
    assert set(ts) == {"static", "modal", "buckling", "thermal", "drop", "wind"}
    w = ts["wind"]["schema"]["properties"]
    assert w["speed_m_s"]["unit"] == "m/s" and w["speed_m_s"]["default"] == 10.0
    assert w["speed_m_s"]["exclusiveMinimum"] == 0 and w["speed_m_s"]["maximum"] == 150
    assert w["air_density"]["default"] == 1.225 and w["air_viscosity"]["default"] == 1.81e-5
    assert "description" in w["domain_multiplier"]
    assert ts["wind"]["available"] is True and ts["wind"]["fidelity_class"] == "cfd_lbm"
    assert ts["drop"]["fidelity_class"] == "fem_estimate"
    s = ts["static"]["schema"]
    assert "load_cases" in s["required"]
    assert {"mesh", "material", "convergence", "solver"} <= set(s["properties"])


@pytest.mark.parametrize("type_,settings,needle", [
    ("modal", {"n_modes": 0}, "n_modes"),
    ("modal", {"band_hz": [300, 100]}, "band_hz"),
    ("wind", {"speed_m_s": -1}, "speed_m_s"),
    ("wind", {"yaw_deg": [200]}, "yaw_deg"),
    ("wind", {"direction": [0, 0, 1], "up": [0, 0, 2]}, "parallel"),
    ("wind", {"refinement": 9}, "refinement"),
    ("static", {"load_cases": []}, "load_cases"),
    ("static", {"load_cases": [CASE, CASE]}, "unique"),
    ("static", {"load_cases": [{**CASE, "loads": [{"kind": "force", "face": "side",
                                                   "newtons": 1, "direction": "-z"}]}]}, "face"),
    ("static", {"load_cases": [CASE], "mesh": {"size_mm": 0}}, "size_mm"),
    ("static", {"load_cases": [CASE], "solver": {"timeout_s": 1}}, "timeout_s"),
    ("static", {"load_cases": [CASE], "material": {"custom": {"name": "X", "e_mpa": 1000,
                                                              "nu": 0.6, "yield_mpa": 10,
                                                              "density_g_cm3": 1}}}, "nu"),
    ("static", {"load_cases": [CASE], "bogus": 1}, "bogus"),
    ("thermal", {"heat": [{"face": "top", "watts": 1}]}, "steady state"),
    ("thermal", {"temperatures": [{"face": "top", "temp_c": 20}],
                 "heat": [{"face": "top", "watts": 1, "w_m2": 2}]}, "exactly one"),
    ("drop", {"impact_face": "bottom", "k": 0.5}, "k"),
    ("nonsense", {}, "unknown study type"),
])
def test_settings_schema_rejects_bad_ranges(type_, settings, needle):
    with pytest.raises(SimError) as e:
        studies.validate_settings(type_, settings)
    assert e.value.code == "bad_setup" and needle in e.value.message


def test_settings_accept_vectors_presets_and_defaults():
    s = studies.validate_settings("static", {"load_cases": [
        {**CASE, "loads": [{"kind": "remote_force", "face": "f2", "newtons": 5,
                            "direction": [1, 1, 0], "point": [0, 0, 50]},
                           {"kind": "bearing", "face": "f3", "newtons": 5, "direction": "+x"},
                           {"kind": "gravity"}]}]})
    assert s.load_cases[0].loads[2].g == 1.0 and s.convergence.enabled is False
    assert s.mesh.second_order is True and s.solver.timeout_s == 300
    w = studies.validate_settings("wind", {})
    assert w.solver == "auto" and w.turbulence == "smagorinsky"


# ---- materials

def test_fdm_factors_are_monotonic_and_documented():
    solid = fdm_factors(100, 3, "xy")
    assert solid == (pytest.approx(1.0), pytest.approx(FDM_KNOCKDOWN))
    e40, s40 = fdm_factors(40, 2, "xy")
    assert 0.4 < e40 < 1 and s40 < FDM_KNOCKDOWN
    assert fdm_factors(40, 2, "z")[1] == pytest.approx(0.6 * s40)
    assert fdm_factors(40, 4, "xy")[0] > e40  # more walls, stiffer


def test_custom_materials_crud(tmp_path):
    m = custom_material({"name": "My PETG", "e_mpa": 1800, "nu": 0.38, "yield_mpa": 45,
                         "density_g_cm3": 1.27, "k_w_mk": 0.2})
    upsert_custom(tmp_path, m)
    assert [c.name for c in load_custom(tmp_path)] == ["My PETG"]
    assert get_material("my petg", tmp_path).e_mpa == 1800
    with pytest.raises(SimError):
        get_material("my petg")  # not without the home
    with pytest.raises(SimError):
        upsert_custom(tmp_path, custom_material({**m.__dict__, "name": "PLA"}))
    with pytest.raises(SimError):
        custom_material({"name": "../x", "e_mpa": 1, "nu": 0.3, "yield_mpa": 1,
                         "density_g_cm3": 1})
    assert delete_custom(tmp_path, "My PETG") and not delete_custom(tmp_path, "My PETG")


# ---- deck helpers

def _cyl_mesh(r=5.0, n=24, h=10.0):
    nodes, tris = {}, []
    for k in range(n):
        a = 2 * math.pi * k / n
        nodes[2 * k + 1] = (r * math.cos(a) + 3, r * math.sin(a) - 2, 0.0)
        nodes[2 * k + 2] = (r * math.cos(a) + 3, r * math.sin(a) - 2, h)
    for k in range(n):
        a0, b0 = 2 * k + 1, 2 * k + 2
        a1, b1 = 2 * ((k + 1) % n) + 1, 2 * ((k + 1) % n) + 2
        tris += [[a0, a1, b1], [a0, b1, b0]]
    return Mesh(nodes=nodes, tets=[], surfaces=[Surface(1, [3, -2, 5], [0] * 6, 1, tris)],
                order=1)


def test_fit_axis_of_a_cylinder():
    pt, ax = fit_axis(_cyl_mesh(), [1])
    assert abs(abs(ax[2]) - 1) < 1e-6
    assert pt[0] == pytest.approx(3, abs=1e-6) and pt[1] == pytest.approx(-2, abs=1e-6)


def test_moment_loads_have_zero_resultant_and_the_asked_moment():
    nodes = {1: (0, 0, 0), 2: (0, 10, 0), 3: (0, 10, 10), 4: (0, 0, 10)}
    mesh = Mesh(nodes=nodes, tets=[], order=1,
                surfaces=[Surface(1, [0, 5, 5], [0, 0, 0, 0, 10, 10], 100,
                                  [[1, 2, 3], [1, 3, 4]])])
    M = np.array([200.0, 0.0, 50.0])
    f = _moment_loads(mesh, [1], M)
    F = np.array(list(f.values()))
    X = np.array([nodes[n] for n in f])
    c = X.mean(0)
    assert np.allclose(F.sum(0), 0, atol=1e-9)
    assert np.allclose(np.cross(X - c, F).sum(0), M, atol=1e-6)


def test_parse_dat_tables(tmp_path):
    p = tmp_path / "job.dat"
    p.write_text("""
     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.1930834E+07   0.1389545E+04   0.2211534E+03   0.0000000E+00
      2   0.1931000E+07   0.1389605E+04   0.2211630E+03   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S
""")
    rows = parse_dat_table(p, "E I G E N V A L U E   O U T P U T", 5)
    assert [r[0] for r in rows] == [1, 2] and rows[0][3] == pytest.approx(221.1534)
    q = tmp_path / "b.dat"
    q.write_text("     B U C K L I N G   F A C T O R   O U T P U T\n\n MODE NO       BUCKLING\n"
                 "                FACTOR\n\n      1   0.4731E+01\n      2   0.4790E+01\n")
    assert parse_dat_table(q, "B U C K L I N G   F A C T O R   O U T P U T", 2) == [
        [1, 4.731], [2, 4.79]]


def test_principal_and_von_mises_vectorized():
    S = np.array([[10, 0, 0, 0, 0, 0], [0, 0, 0, 1, 0, 0], [5, 5, 5, 0, 0, 0.0]])
    assert von_mises_np(S) == pytest.approx([10, math.sqrt(3), 0])
    p = principal_stresses(S)
    assert p[0] == pytest.approx([10, 0, 0]) and p[1] == pytest.approx([1, 0, -1])


# ---- probe and section

def test_probe_interpolates_linearly_inside_tets(tmp_path):
    coords = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10], [10, 10, 10.0]])
    tets = np.array([[0, 1, 2, 3], [1, 2, 3, 4]])
    f_lin = lambda p: 2 * p[0] + 3 * p[1] - p[2] + 1   # a linear field is exact
    path = tmp_path / "fields.npz"
    save_fem(path, coords, tets, {"t": np.array([f_lin(c) for c in coords]),
                                  "u": np.tile([1.0, 2.0, 3.0], (5, 1))}, {"t": "degC"})
    f = Fields(path)
    pts = [[1, 2, 3], [4, 4, 1], [6, 6, 6]]
    res = f.probe(pts)
    for p, r in zip(pts, res):
        assert r["inside"] and r["values"]["t"] == pytest.approx(f_lin(p), abs=1e-4)
    assert res[0]["values"]["u"] == pytest.approx([1, 2, 3])
    assert f.probe([[20, 20, 20]])[0]["inside"] is False
    sec = f.section(2, 1.0, "t", n=8)
    vals = np.array(sec["values"], dtype=float)
    assert np.isfinite(vals).any() and len(sec["u_values"]) == 8


def test_grid_probe_is_trilinear_and_rotated(tmp_path):
    shape = (6, 5, 4)
    I, J, K = np.meshgrid(*[np.arange(n, dtype=float) for n in shape], indexing="ij")
    cp = 0.5 * I - 0.25 * J + K
    R = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1.0]])  # wind along part +y
    path = tmp_path / "g.npz"
    save_grid(path, [0, 0, 0], 2.0, {"cp": cp}, {"cp": ""}, solid=np.zeros(shape, bool), rot=R)
    f = Fields(path)
    # part point p maps to the grid at R @ p; grid coords / spacing give the indices
    p = np.array([-3.0, 5.0, 2.0])
    g = R @ p / 2.0
    r = f.probe([p])[0]
    assert r["inside"] and r["values"]["cp"] == pytest.approx(0.5 * g[0] - 0.25 * g[1] + g[2])


# ---- convergence loop

def _job(tmp_path, conv=None, size=None):
    metrics = {"bbox": {"min": [0, 0, 0], "max": [100, 10, 10], "size": [100, 10, 10]},
               "volume_mm3": 10_000}
    return FemJob(root=tmp_path, step=tmp_path / "m.step", metrics=metrics,
                  material=get_material("PLA"), ccx="ccx",
                  mesh_settings=MeshSettings(size_mm=size), solver_settings=SolverSettings(),
                  progress=lambda *a, **k: None, convergence=conv)


async def test_convergence_loop_stops_when_the_change_is_small(tmp_path):
    seen = []

    async def solve_at(size, d, label, lo, hi):
        seen.append(size)
        metric = 10.0 + 0.2 * size ** 2  # converges as the mesh is refined
        return metric, {"info": {"nodes": int(1000 / size ** 3), "elements": 1}}
    job = _job(tmp_path, ConvergenceSettings(enabled=True, tol_pct=5, max_levels=6))
    lv = await run_levels(job, solve_at)
    assert lv["converged"] is True and len(seen) < 6
    assert lv["history"][-1]["change"] < 0.05
    assert all(b < a for a, b in zip(seen, seen[1:]))
    # never converging stops at max_levels and says so
    seen.clear()

    async def wild(size, d, label, lo, hi):
        seen.append(size)
        return float(len(seen) ** 3), {"info": {"nodes": len(seen), "elements": 1}}
    lv = await run_levels(_job(tmp_path, ConvergenceSettings(enabled=True, tol_pct=1,
                                                             max_levels=3)), wild)
    assert lv["converged"] is False and len(seen) == 3


def test_level_sizes_and_mesh_options(tmp_path):
    sizes, floor = level_sizes(_job(tmp_path))
    assert sizes[1] == pytest.approx(sizes[0] / 2)
    sizes, _ = level_sizes(_job(tmp_path, size=1.0))
    assert sizes == [pytest.approx(2.0), pytest.approx(1.0)]
    job = _job(tmp_path)
    job.mesh_settings = MeshSettings(refine_faces=["x-min"], refine_factor=0.5,
                                     second_order=False)
    opts = mesh_options(job, 4.0)
    assert opts == {"order": 1, "refine": [{"size": 2.0, "axis": 0, "value": 0}]}


# ---- drop

def test_impact_equivalent_fallback_and_direction():
    imp = impact_newtons(0.2, 1.0, 10)
    assert imp["newtons"] == pytest.approx(0.2 * 9.81 * 10) and imp["estimate"] is True
    assert "m g k" in imp["formula"]
    assert impact_direction("bottom", {}) == (0.0, 0.0, -1.0)
    assert impact_direction("x-max", {}) == (1.0, 0.0, 0.0)
    assert impact_direction("f1", {"face_list": [{"id": "f1", "normal": [0, 1, 0]}]}) == (
        0.0, 1.0, 0.0)


def test_impact_equivalent_uses_engineering_loads_when_present(monkeypatch):
    import sys
    import types
    mod = types.ModuleType("mises.engineering.loads")
    mod.impact_equivalent = lambda m, h, k: {"newtons": 42.0, "estimate": True,
                                             "formula": "F = m*g*k (C3)"}
    pkg = types.ModuleType("mises.engineering")
    pkg.loads = mod
    monkeypatch.setitem(sys.modules, "mises.engineering", pkg)
    monkeypatch.setitem(sys.modules, "mises.engineering.loads", mod)
    imp = impact_newtons(1, 1, 1)
    assert imp["newtons"] == 42.0 and imp["source"].startswith("engineering")


# ---- records: compare, templates, charts, cards, checks

def _rec(sim_id, type_="static", **results):
    return {"sim_id": sim_id, "type": type_, "part_id": "p", "status": "done",
            "fidelity_class": "fem", "results": results, "warnings": []}


def test_compare_table_marks_the_best():
    t = studies.compare([_rec("sim_000000000001", max_von_mises_mpa=6.0, safety_factor=5.0),
                         _rec("sim_000000000002", max_von_mises_mpa=4.0, safety_factor=7.5),
                         _rec("sim_000000000003", "wind", cd=0.5)])
    rows = {r["key"]: r for r in t["rows"]}
    assert rows["max_von_mises_mpa"]["values"] == [6.0, 4.0, None]
    assert rows["max_von_mises_mpa"]["best"] == 1 and rows["safety_factor"]["best"] == 1
    assert rows["cd"]["best"] is None  # one value: nothing to compare
    assert [c["sim_id"] for c in t["columns"]][2] == "sim_000000000003"
    assert "first_freq_hz" not in rows


def test_templates_crud(tmp_path):
    t = studies.add_template(tmp_path, "Desk load", "static", {"load_cases": [CASE]})
    assert studies.TEMPLATE_ID_RE.fullmatch(t["id"])
    assert t["settings"]["mesh"]["second_order"] is True  # stored normalized
    assert [x["name"] for x in studies.load_templates(tmp_path)] == ["Desk load"]
    with pytest.raises(SimError) as e:
        studies.add_template(tmp_path, "desk LOAD", "static", {"load_cases": [CASE]})
    assert e.value.code == "conflict"
    with pytest.raises(SimError):
        studies.add_template(tmp_path, "bad", "modal", {"n_modes": 99})
    assert studies.delete_template(tmp_path, t["id"]) is True
    assert studies.delete_template(tmp_path, t["id"]) is False
    assert studies.load_templates(tmp_path) == []


def test_charts_per_type(tmp_path):
    modal = _rec("sim_0000000000aa", "modal", band_hz=[100, 200],
                 modes=[{"mode": 1, "freq_hz": 150.0, "in_band": True},
                        {"mode": 2, "freq_hz": 400.0, "in_band": False}],
                 convergence={"history": [{"nodes": 100, "metric": 160.0},
                                          {"nodes": 800, "metric": 150.0}]})
    c = {x["id"]: x for x in charts(modal, tmp_path)["charts"]}
    assert c["modal_frequencies"]["bands"][0]["from"] == 100
    assert c["modal_frequencies"]["series"][0]["flags"] == [True, False]
    assert c["convergence"]["series"][0]["points"] == [[100, 160.0], [800, 150.0]]
    wind = _rec("sim_0000000000bb", "wind", yaw_sweep=[
        {"yaw_deg": 10, "cd": 0.6, "cl": 0.1}, {"yaw_deg": 0, "cd": 0.5, "cl": 0.0}],
        convergence={"history": [{"step": 10, "cd": 1.0, "cd_lattice_total": 1.2}]})
    c = {x["id"]: x for x in charts(wind, tmp_path)["charts"]}
    assert c["cd_vs_yaw"]["series"][0]["points"] == [[0, 0.5], [10, 0.6]]
    assert c["cd_history"]["series"][0]["points"] == [[10, 1.0]]
    save_fem(tmp_path / "fields.npz", np.zeros((3, 3)), np.zeros((0, 4), int),
             {"von_mises": np.array([1.0, 2.0, 3.0])}, {"von_mises": "MPa"})
    st = _rec("sim_0000000000cc", allowable_mpa=35.0)
    c = {x["id"]: x for x in charts(st, tmp_path)["charts"]}
    assert sum(c["von_mises_histogram"]["counts"]) == 3
    assert c["von_mises_histogram"]["markers"][0]["x"] == 35.0


def test_card_and_requirement_checks():
    rec = _rec("sim_0000000000dd", max_von_mises_mpa=6.0, safety_factor=2.5,
               max_displacement_mm=1.0, error_band=0.02, sf_by_case={"hover": 2.5})
    rec["checks"] = studies.check_requirements(rec, [
        {"id": "r1", "kind": "min_safety_factor", "target": 2.0, "load_case_id": "hover"},
        {"id": "r2", "kind": "max_deflection_mm", "target": 0.5},
        {"id": "r3", "kind": "min_safety_factor", "target": 2.4},
        {"id": "r4", "kind": "max_cd", "target": 1.0}])   # not a static question: skipped
    by = {c["requirement_id"]: c for c in rec["checks"]}
    assert by["r1"]["status"] == "pass" and by["r2"]["status"] == "fail"
    assert by["r3"]["status"] == "warn" and "r4" not in by  # within 10% of the limit
    card = studies.card(rec)
    assert card["verdict"] == "fail" and card["metrics"][0]["key"] == "max_von_mises_mpa"
    assert card["urls"]["glb"] == "/sims/sim_0000000000dd/result.glb"
    wind = {**_rec("sim_0000000000ee", "wind", cd=0.4, drag_n=1.0, error_band=0.01),
            "fidelity_class": "cfd_lbm"}
    ch = studies.check_requirements(wind, [{"id": "w", "kind": "max_drag_n", "target": 5}])
    assert ch[0]["status"] == "warn" and "low fidelity" in ch[0]["note"]


def test_plan_review_picks_the_studies_the_spec_needs():
    spec = {"requirements": [
        {"id": "r1", "kind": "min_safety_factor", "target": 2, "load_case_id": "crash"},
        {"id": "r2", "kind": "max_mass_g", "target": 50},
        {"id": "r3", "kind": "max_drag_n", "target": 2},
        {"id": "r4", "kind": "survive_drop_m", "target": 1.5}],
        "load_cases": [{"id": "hover", "fix": "bottom", "load": "top", "newtons": 10,
                        "direction": "-z"},
                       {"id": "crash", "fix": "bottom", "load": "x-max", "newtons": 80,
                        "direction": "-x", "estimate": True}],
        "excitation": {"rpm": 24000, "blades": 2}}
    plan = plan_review(spec)
    kinds = [(p["type"], p["case"]) for p in plan]
    assert kinds == [("static", "hover"), ("static", "crash"), ("modal", "modal"),
                     ("wind", "wind"), ("drop", "drop")]
    assert plan[1]["requirements"][0]["id"] == "r1" and plan[0]["requirements"] == []
    assert plan[2]["settings"]["excitation"]["rpm"] == 24000
    assert plan[4]["settings"]["drop_m"] == 1.5
    for p in plan:  # every planned study validates
        studies.validate_settings(p["type"], p["settings"])
    assert plan_review({"requirements": [{"id": "x", "kind": "max_mass_g", "target": 1}]}) == []


def test_custom_requirements_carry_study_kinds():
    # C3's spec model stops at kind "custom": its target names the study kind
    spec = {"requirements": [
        {"id": "c1", "kind": "custom", "target": {"max_drag_n": 2.5}},
        {"id": "c2", "kind": "custom", "target": {"excitation": {"rpm": 30000, "blades": 2}}},
        {"id": "c3", "kind": "custom", "target": "looks nice"}]}
    plan = plan_review(spec)
    assert [p["type"] for p in plan] == ["modal", "wind"]
    assert plan[1]["requirements"] == [{"id": "c1", "kind": "max_drag_n", "target": 2.5}]
    assert plan[0]["settings"]["excitation"]["rpm"] == 30000
