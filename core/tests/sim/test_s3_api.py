"""Plan 6 S3: calibration routes and calibration_* tools, the calibrated material reaching a
study's preflight, fatigue / anisotropy in the settings schema, and the prompts."""
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.agent.definitions import load_definitions
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.sim import calibration_tools as ct
from mises.sim import studies
from mises.sim.fields import save_fem
from mises.store import AgentPolicy
from mises.types import Caps, ErrorCode, MisesError, SafetyClass

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SID = "sim_00000000abcd"
CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}


def _bend(E, P=20.0, L=64.0, b=10.0, h=4.0):
    return {"span_mm": L, "width_mm": b, "thickness_mm": h, "load_n": P,
            "deflection_mm": P * L ** 3 / (48 * E * b * h ** 3 / 12)}


TESTS = {"material": "PLA", "printer": "MK4",
         "tensile": [{"force_n": 1800, "width_mm": 10, "thickness_mm": 4},
                     {"force_n": 1760, "area_mm2": 40}, {"force_n": 1840, "area_mm2": 40}],
         "bend": [_bend(2100.0), _bend(2100.0, P=30)]}


@pytest.fixture
def rt(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(h))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    return Runtime(h, provider_resolver=lambda ref: (fake, "fake-model"))


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _study(home, part="beam", e=2300.0):
    """A finished static study whose fields put 1 mm of -z deflection at (100, 0, 0)."""
    d = home / "parts" / part / "sims" / SID
    d.mkdir(parents=True)
    rec = {"sim_id": SID, "part_id": part, "type": "static", "status": "done",
           "created_at": time.time(), "geometry_hash": "x",
           "material": {"name": "PLA", "e_mpa": e, "e_eff_mpa": e, "stiffness_factor": 1.0},
           "settings": {"load_cases": [CASE]}, "results": {"governing_case": "tip"}}
    (d / "result.json").write_text(json.dumps(rec))
    coords = np.array([[0, 0, 0], [100, 0, 0], [0, 10, 0], [0, 0, 10]], dtype=float)
    disp = np.array([[0, 0, 0], [0, 0, -1.0], [0, 0, 0], [0, 0, 0]])
    save_fem(d / "fields.npz", coords, np.array([[0, 1, 2, 3]]), {"displacement": disp},
             {"displacement": "mm"})
    return d


def test_calibration_routes_fit_save_list_get_delete(client, rt):
    assert client.get("/sims/calibrations").status_code == 401
    assert client.get("/sims/calibrations", headers=AUTH).json() == []
    r = client.post("/sims/calibrations/fit", headers=AUTH, json=TESTS)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["saved"] is False and p["basis"] == "calibrated E (2), strength (3), low confidence"
    assert p["fit"]["e"]["value_mpa"] == pytest.approx(2100.0)
    assert p["fit"]["strength"]["value_mpa"] == pytest.approx(45.0)
    assert p["material"]["basis"] == p["basis"]
    assert p["datasheet"]["basis"] == "datasheet / estimate"
    assert client.get("/sims/calibrations", headers=AUTH).json() == []  # not saved

    r = client.post("/sims/calibrations", headers=AUTH, json=TESTS)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["fit"]["n_tests"] == 5 and rec["tests"]["tensile"][1]["area_mm2"] == 40
    assert rec["fit"]["strength"]["sd"] > 0 and rec["fit"]["confidence"] in ("low", "medium")
    r = client.post("/sims/calibrations", headers=AUTH,
                    json={"material": "PLA", "printer": "MK4", "bend": [_bend(2200.0)],
                          "append": True})
    assert r.status_code == 201 and r.json()["fit"]["n_tests"] == 6
    assert r.json()["id"] == rec["id"]
    listed = client.get("/sims/calibrations?material=pla", headers=AUTH).json()
    assert [x["id"] for x in listed] == [rec["id"]]
    assert client.get(f"/sims/calibrations/{rec['id']}", headers=AUTH).json()["printer"] == "MK4"
    assert client.get("/sims/calibrations/cal_0000000000", headers=AUTH).status_code == 404
    assert client.get("/sims/calibrations/nope", headers=AUTH).status_code == 400

    # the next study of PLA on the MK4 profile uses it
    s = studies.validate_settings("static", {"load_cases": [CASE],
                                             "material": {"name": "PLA", "printer": "MK4"}})
    m, _ = studies.preflight("static", s, rt.home / "parts" / "x", rt.home, "PLA")
    assert m.basis == "calibrated E (3), strength (3)" and m.calibration["id"] == rec["id"]
    assert client.delete(f"/sims/calibrations/{rec['id']}", headers=AUTH).status_code == 200
    assert client.delete(f"/sims/calibrations/{rec['id']}", headers=AUTH).status_code == 404


def test_calibration_routes_reject_bad_input(client, rt):
    for body in ({"material": "PLA"},
                 {"material": "Nope", "tensile": [{"force_n": 1, "area_mm2": 1}]},
                 {"material": "PLA", "tensile": [{"force_n": -1, "area_mm2": 1}]},
                 {"material": "PLA", "part": [{"sim_id": SID, "point": [0, 0, 0],
                                               "load_n": 1, "deflection_mm": 1}]}):
        r = client.post("/sims/calibrations/fit", headers=AUTH, json=body)
        assert r.status_code in (400, 422), (body, r.text)


def test_part_load_test_through_the_route(client, rt):
    _study(rt.home)
    body = {"material": "PLA", "part": [{"sim_id": SID, "point": [100, 0, 0], "load_n": 20,
                                         "deflection_mm": 2.5, "direction": [0, 0, -1]}]}
    r = client.post("/sims/calibrations/fit", headers=AUTH, json=body)
    assert r.status_code == 200, r.text
    f = r.json()["fit"]
    assert f["e"]["value_mpa"] == pytest.approx(2300 * 2.0 / 2.5, rel=1e-4)
    assert f["part_tests"][0]["predicted_deflection_mm"] == pytest.approx(2.0, rel=1e-4)


def _ctx(home, scope=("*",)):
    pol = AgentPolicy(run_id="r", version=1, edit_scope=list(scope), read_scope=list(scope),
                      caps=Caps(), allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=home, run_id="r", policy=pol, call_id="c",
                           services={"runtime": SimpleNamespace(home=home)})


async def test_calibration_tools(tmp_path):
    ctx = _ctx(tmp_path)
    out = await ct.calibration_fit.handler(ctx, ct.cal.CalibrationInput(**TESTS))
    assert out.ok and out.summary.startswith("preview (not saved)")
    assert "calibrated E (2), strength (3)" in out.summary and out.data["saved"] is False
    saved = await ct.calibration_save.handler(ctx, ct.SaveArgs(**TESTS))
    cid = saved.data["id"]
    assert saved.summary.startswith("saved PLA on MK4")
    lst = await ct.calibration_list.handler(ctx, ct.ListArgs())
    assert [r["id"] for r in lst.data["calibrations"]] == [cid]
    got = await ct.calibration_get.handler(ctx, ct.IdArgs(id=cid))
    assert got.data["fit"]["n_tests"] == 5
    assert (await ct.calibration_delete.handler(ctx, ct.IdArgs(id=cid))).ok
    with pytest.raises(MisesError):
        await ct.calibration_get.handler(ctx, ct.IdArgs(id=cid))


async def test_agent_save_and_delete_need_approval_with_before_after(tmp_path):
    """Final review I1: an agent's calibration_save / _delete is the user's click on a card
    showing what changes (before -> after); the app's own routes keep their confirm."""
    ctx = _ctx(tmp_path)
    save, delete = ct.calibration_save, ct.calibration_delete
    args = ct.SaveArgs(**TESTS)
    assert save.needs_approval(ctx, args) and delete.needs_approval(ctx, ct.IdArgs(id="cal_0a1b2c3d4e"))
    pv = await save.preview(ctx, args)
    assert pv["before"]["n_tests"] is None and pv["after"]["n_tests"] == 5
    assert any(c.startswith("E: ") and "→" in c for c in pv["changes"])
    assert any(c.startswith("tests: — → 5") for c in pv["changes"])
    cid = (await save.handler(ctx, args)).data["id"]
    again = await save.preview(ctx, args)
    assert again["before"]["n_tests"] == 5 and not any(c.startswith("tests") for c in again["changes"])
    gone = await delete.preview(ctx, ct.IdArgs(id=cid))
    assert gone["after"]["n_tests"] is None and "data sheet" in gone["note"]
    # the app's own UI call (Runtime.ui_tool_call) asks no card
    ui = SimpleNamespace(**{**vars(ctx), "services": {"runtime": SimpleNamespace(
        home=tmp_path, ui_calls={"c": SimpleNamespace(run_id="r", meta={})})}})
    assert save.needs_approval(ui, args) is None


async def test_part_test_outside_the_run_scope_is_refused(tmp_path):
    _study(tmp_path, part="secret")
    ctx = _ctx(tmp_path, scope=("beam",))
    body = ct.cal.CalibrationInput(material="PLA", part=[{
        "sim_id": SID, "point": [100, 0, 0], "load_n": 20, "deflection_mm": 2.5}])
    with pytest.raises(MisesError) as e:
        await ct.calibration_fit.handler(ctx, body)
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


def test_tools_are_registered_with_their_safety_classes(rt):
    names = {t.name: t for t in rt.registry.list()}
    assert names["calibration_list"].safety == SafetyClass.READ
    assert names["calibration_fit"].safety == SafetyClass.READ
    assert names["calibration_save"].safety == SafetyClass.LOCAL_MUTATE
    assert names["calibration_delete"].safety == SafetyClass.LOCAL_MUTATE


def test_static_schema_has_fatigue_and_anisotropy_options():
    ts = {t["id"]: t for t in studies.types_list()}
    schema = json.dumps(ts["static"]["schema"])
    for key in ("fatigue", "cycles", "fully_reversed", "anisotropy", "print_direction",
                "printer", "calibrated", "sn_a_mpa"):
        assert key in schema, key


def test_prompts_use_calibrations_and_ask_for_fatigue_cycles():
    defs = load_definitions()
    eng = defs["engineer"].body
    assert "calibration_list" in eng and "calibrated" in eng
    assert "fatigue" in eng and "cycles" in eng and "drone" in eng.lower()
    sim = defs["sim"]
    assert "calibration_list" in sim.tools and "fatigue" in sim.body
