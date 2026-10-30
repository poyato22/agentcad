"""S1 review fix round 1: partial solves never pass, the worst case governs, honest
validation labels, estimated tangents, deck details (sigma_inf, ties, .sta columns) and the
assembly target's scope."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from mises.sim import nonlinear as nlm
from mises.sim.deck import Setup
from mises.sim.materials import MATERIALS
from mises.sim.mesh import Mesh, Surface
from mises.sim.settings import NonlinearSettings
from mises.sim.studies import check_requirements, fidelity_note, material_caveats
from mises.sim.tools import SimRunArgs, sim_run
from mises.sim.types import SimError
from mises.types import ErrorCode, MisesError

from .test_nonlinear import BEAM, CASE, _ctx, _part, _run, _tet_mesh, needs_ccx

METRICS = {"bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]}}


def _rec(cases, **r):
    return {"sim_id": "sim_000000000001", "type": "static", "status": "done",
            "fidelity_class": "fem_nonlinear",
            "results": {"error_band": 0.01, "model_error_band": 0.05, "cases": cases,
                        "safety_factor": cases[0]["safety_factor"],
                        "max_displacement_mm": cases[0]["max_displacement_mm"], **r}}


# ---- C1 / C2: requirement checks

def test_partial_solve_never_passes_a_requirement():
    stopped = {"name": "buckle", "safety_factor": 3.0, "max_displacement_mm": 1.0,
               "converged": False, "load_fraction": 0.55}
    rec = _rec([stopped])
    for kind, target in (("max_deflection_mm", 5.0), ("min_safety_factor", 1.2)):
        ch = check_requirements(rec, [{"id": "r", "kind": kind, "target": target}])[0]
        assert ch["status"] == "fail" and ch["note"] == "did not reach full load (55%)", ch


def test_requirement_without_a_case_is_judged_on_every_case():
    ok = {"name": "A", "safety_factor": 1.4, "max_displacement_mm": 0.5, "converged": True,
          "load_fraction": 1.0}
    collapsed = {"name": "B", "safety_factor": 0.6, "max_displacement_mm": 9.0,
                 "converged": False, "load_fraction": 0.6}
    rec = _rec([ok, collapsed])
    sf = check_requirements(rec, [{"id": "r", "kind": "min_safety_factor", "target": 1.2}])[0]
    assert sf["measured"] == 0.6 and sf["status"] == "fail"
    only_a = check_requirements(rec, [{"id": "r", "kind": "min_safety_factor", "target": 1.2,
                                       "load_case_id": "A"}])[0]
    assert only_a["measured"] == 1.4 and only_a["status"] == "pass"
    defl = check_requirements(rec, [{"id": "r", "kind": "max_deflection_mm", "target": 20}])[0]
    assert defl["measured"] == 9.0 and defl["status"] == "fail"


# ---- I1: labels

def test_unvalidated_configurations_are_labelled_not_validated():
    rec = {"fidelity_class": "fem_nonlinear", "results": {
        "model_error_band": 0.15, "nonlinear": {"description": "contact",
                                                "unvalidated": ["curved contact faces"]}}}
    note = fidelity_note(rec)
    assert "UNVALIDATED configuration: curved contact faces" in note
    assert not note.endswith("(validated)")


def _cylinder_patch() -> Mesh:
    """A quarter of a cylinder's surface as a strip of triangles (normals spread 90 deg)."""
    nodes, tris, k = {}, [], 1
    for i in range(7):
        a = math.radians(15 * i)
        for z in (0.0, 5.0):
            nodes[k] = (5 * math.cos(a), 5 * math.sin(a), z)
            k += 1
    for i in range(6):
        p, q = 1 + 2 * i, 3 + 2 * i
        tris += [[p, q, q + 1], [p, q + 1, p + 1]]
    return Mesh(nodes=nodes, tets=[], surfaces=[Surface(tag=1, center=[3, 3, 2.5],
                                                        bbox=[0, 0, 0, 5, 5, 5], area=40,
                                                        tris=tris)], size_mm=1.0, order=1)


def test_curved_faces_are_detected_and_flat_ones_are_not():
    assert nlm.curved(_cylinder_patch(), [1])
    assert not nlm.curved(_tet_mesh(), [7])


def test_rigid_support_mixed_with_a_pair_and_orthotropic_plastic_are_flagged():
    mesh = _tet_mesh()
    mesh.surfaces.append(Surface(tag=8, center=[0, 3.3, 3.3], bbox=[0, 0, 0, 0, 10, 10],
                                 area=50, tris=[[1, 3, 4]]))
    nl = NonlinearSettings.model_validate({
        "plasticity": True, "rigid_supports": [{"face": "bottom"}],
        "contacts": [{"a": {"face": "x-min"}, "b": {"face": "bottom"}}]})
    ortho = SimpleNamespace(**{k: getattr(MATERIALS["PLA"], k) for k in
                               ("e_eff_mpa", "allowable_mpa")}, orthotropic=True)
    model = nlm.resolve_nonlinear(mesh, METRICS, nl, ortho, Setup())
    text = " | ".join(model.unvalidated)
    assert "rigid support in a study that also has a contact pair" in text
    assert "orthotropic" in text


def test_isotropic_caveat_follows_the_material():
    from mises.sim.studies import CAVEAT_ORTHOTROPIC, CAVEATS_NONLINEAR
    assert material_caveats(CAVEATS_NONLINEAR, MATERIALS["PLA"]) == CAVEATS_NONLINEAR
    swapped = material_caveats(CAVEATS_NONLINEAR, SimpleNamespace(orthotropic=True))
    assert CAVEAT_ORTHOTROPIC in swapped
    assert not any(c.startswith("Isotropic") for c in swapped)


# ---- I5 / M1 / M4: deck details

def test_sigma_inf_is_e_over_70000_whatever_the_penalty():
    mesh = _tet_mesh()
    mesh.surfaces.append(Surface(tag=8, center=[0, 3.3, 3.3], bbox=[0, 0, 0, 0, 10, 10],
                                 area=50, tris=[[1, 3, 4]]))
    E = MATERIALS["PLA"].e_eff_mpa
    for penalty in (5.0, 1000.0):
        nl = NonlinearSettings.model_validate({"penalty": penalty, "contacts": [
            {"a": {"face": "x-min"}, "b": {"face": "bottom"}}]})
        cards = nlm.resolve_nonlinear(mesh, METRICS, nl, MATERIALS["PLA"], Setup()).cards
        i = cards.index("*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR")
        k, s_inf = map(float, cards[i + 1].split(","))
        assert k == pytest.approx(penalty * E) and s_inf == pytest.approx(E / 70000)


def test_sta_columns_total_then_step_time(tmp_path):
    p = tmp_path / "job.sta"
    p.write_text("  STEP      INC     ATT  ITRS     TOT TIME     STEP TIME      INC TIME\n"
                 "     2         3     1     4  0.180000E+01  0.800000E+00  0.100000E+00\n")
    row = nlm.parse_sta(p)[0]
    assert row["total_time"] == pytest.approx(1.8) and row["step_time"] == pytest.approx(0.8)
    assert nlm.load_reached([row]) == pytest.approx(0.8)    # the step's own load fraction


def test_tie_slave_avoids_supported_nodes():
    mesh = _tet_mesh()
    mesh.surfaces.append(Surface(tag=8, center=[0, 3.3, 3.3], bbox=[0, 0, 0, 0, 10, 10],
                                 area=50, tris=[[1, 3, 4]]))
    nl = NonlinearSettings.model_validate({"contacts": [{"kind": "bonded",
                                                         "a": {"face": "bottom"},
                                                         "b": {"face": "x-min"}}]})
    setup = Setup(supported={2})                  # node 2 is on the base (tag 7) only
    cards = nlm.resolve_nonlinear(mesh, METRICS, nl, MATERIALS["PLA"], setup).cards
    sa = cards[cards.index("*SURFACE, NAME=SA1, TYPE=ELEMENT") + 1]
    assert sa == "1,S4"   # x-min (element face 4) became the slave
    with pytest.raises(SimError, match="both faces share nodes with a support"):
        nlm.resolve_nonlinear(mesh, METRICS, nl, MATERIALS["PLA"], Setup(supported={1}))


def test_deformed_normals_turn_with_the_face():
    mesh = _tet_mesh()          # base z = 0, outward normal -z
    rot = {1: (0, 0, 0), 2: (0, 0, 10.0), 3: (0, 0, 0)}   # lift corner 2: tilt the face
    n = nlm.deformed_normals(mesh, [7], rot)[1]
    assert n[2] < -0.5 and abs(n[0]) > 0.3


# ---- M5 / M6: the assembly target

def test_project_id_pattern_and_an_agent_cannot_study_another_build(monkeypatch):
    with pytest.raises(Exception):
        SimRunArgs(type="static", project_id="../etc")
    import mises.catalog.tools as cat
    monkeypatch.setattr(cat, "ui_call_meta", lambda ctx: None)
    monkeypatch.setattr(cat, "run_project", lambda ctx: "proj_mine")
    from mises.assembly.model import AssemblyStore  # noqa: F401  (store never reached)
    ctx = SimpleNamespace(workdir="/nonexistent", run_id="r1", store=SimpleNamespace(),
                          services={}, policy=None, call_id="c1", events=None)
    import asyncio
    with pytest.raises(MisesError) as e:
        asyncio.run(sim_run.handler(ctx, SimRunArgs(type="static", project_id="proj_other",
                                                    settings={"load_cases": [CASE]})))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


def test_gate_scope_refuses_another_build():
    from mises.gate import Gate
    from mises.sim.tools import sim_run as tool
    pol = SimpleNamespace(edit_scope=["proj_mine", "project:proj_mine"], read_scope=[])
    g = Gate.__new__(Gate)
    g._check_scope(tool, SimRunArgs(type="static", project_id="proj_mine"), pol)
    with pytest.raises(MisesError) as e:
        g._check_scope(tool, SimRunArgs(type="static", project_id="proj_other"), pol)
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


# ---- C2 / I2 / I4 end to end

@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(400)
async def test_fast_collapsing_case_governs_a_two_case_plastic_study(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    load = CASE["loads"][0]
    out = await _run(ctx, "beam", {
        "load_cases": [{**CASE, "name": "light", "loads": [{**load, "newtons": 5}]},
                       {**CASE, "name": "heavy", "loads": [{**load, "newtons": 150}]}],
        "material": {"name": "PLA"}, "mesh": {"size_mm": 5},
        "nonlinear": {"plasticity": True, "increments": 10, "min_increment": 1e-4,
                      "tangent_modulus_mpa": 0.5}},
        requirements=[{"id": "sf", "kind": "min_safety_factor", "target": 1.2}])
    assert out.ok, out.summary
    r = out.data["results"]
    assert r["governing_case"] == "heavy"
    assert r["converged"] is False and r["load_fraction"] < 1
    assert r["nonlinear"]["converged"] is False and r["nonlinear"]["load_reached_pct"] < 100
    assert r["safety_factor"] <= r["load_fraction"]
    # fields come from the last converged increment
    assert r["fields_at_fraction"] == pytest.approx(r["load_fraction"], abs=1e-4)
    assert r["nonlinear"]["fields_at_fraction"] == r["fields_at_fraction"]
    ch = out.data["checks"][0]
    assert ch["status"] == "fail" and "did not reach full load" in ch["note"]
    assert r["nonlinear"]["tangent_estimated"] is False
    assert "did NOT reach full load" in out.summary
    assert np.isfinite(r["max_plastic_strain"])


@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(400)
async def test_fast_estimated_tangent_is_said_out_loud(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "beam", {"load_cases": [CASE], "material": {"name": "PLA"},
                                   "mesh": {"size_mm": 5},
                                   "nonlinear": {"plasticity": True, "increments": 2}})
    assert out.ok, out.summary
    r = out.data["results"]
    assert r["nonlinear"]["tangent_estimated"] is True
    assert any("post-yield slope estimated" in w for w in out.data["warnings"])
    assert "(tangent estimated)" in out.summary
