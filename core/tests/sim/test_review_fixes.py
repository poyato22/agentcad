"""C7 review fixes: compact tool results under the Gate's spill limit, the wind model error
band, the enforced wind memory budget, and input checks before a run exists."""
import functools
import json
import math
import os
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.gate import MAX_OUTPUT_BYTES
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.sim import studies
from mises.sim.cfd import memory_cap_mb
from mises.sim.cfd.validation import MODEL_ERROR_BAND, VALIDATION, model_error_band
from mises.sim.proc import run_sandboxed
from mises.sim.settings import WindSettings
from mises.sim.tools import ReviewArgs, design_review
from mises.sim.types import SimError
from mises.store import AgentPolicy
from mises.types import Caps, ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CUBE = ('from build123d import *\nVARS={"s":40.0}\ndef build(v):\n'
        '    return Box(v["s"],v["s"],v["s"])\n')


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


def _build(client, rt, pid, script):
    rid = rt.create_external_run("tester", provider="x", model="y")
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "mirror_part_create", {"part_id": pid, "script": script},
        call_id=f"b-{pid}", workdir=rt.workdir(rid)))
    assert out.ok, out.summary


def _default_wind_result(drag_n: float) -> dict:
    """What the worker returns for a default study (iterations 4000: 400 history samples,
    a 13-angle yaw sweep): far over the Gate's 16 KB output cap if sent whole."""
    hist = [{"step": 10 * (i + 1), "cd": 0.5 + 0.01 * math.sin(i), "cd_lattice_total": 0.6,
             "cl": 0.001 * i} for i in range(400)]
    sweep = [{"yaw_deg": a, "cd": 0.5, "cl": 0.0, "cs": 0.0, "drag_n": drag_n, "lift_n": 0.0,
              "side_n": 0.0, "method": "lbm"} for a in range(-90, 91, 15)]
    return {"method": "lbm", "fidelity": "low fidelity", "cd": 0.5, "cl": 0.0, "cs": 0.0,
            "drag_n": drag_n, "lift_n": 0.0, "side_n": 0.0, "moment_nm": [0.0, 0.0, 0.0],
            "cd_pressure": 0.48, "cd_friction_estimate": 0.02, "cd_lattice_total": 0.6,
            "reynolds": 1.3e4, "ref_area_m2": 0.0016, "frontal_area_m2": 0.0016,
            "dynamic_pressure_pa": 15.3, "convergence_band": 0.02, "model_error_band": 0.3,
            "refinement_level": 2, "validation": [], "converged": True, "stopped": "converged",
            "steps": 4000, "history": hist, "yaw_sweep": sweep, "cp_range": [-1.0, 1.0],
            "frame": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "lattice": {"shape": [100, 60, 60], "cells": 360000, "cell_mm": 3.3,
                        "cells_across": 12, "reynolds_effective": 1.2e4, "blockage_pct": 4.0,
                        "memory_mb_estimate": 546, "memory_mb_budget": 1024}}


@pytest.mark.slow
@pytest.mark.timeout(240)
def test_default_wind_result_keeps_its_card_and_checks_through_the_gate(client, rt,
                                                                       monkeypatch):
    seen = {}

    async def fake_job(tris, settings, job_dir, *, progress=None):
        seen.setdefault("settings", settings)
        return _default_wind_result(seen.get("drag", 1.0))
    monkeypatch.setattr("mises.sim.cfd.run_wind_job", fake_job)
    _build(client, rt, "cube", CUBE)
    rid = rt.create_external_run("engineer", provider="x", model="y")
    assert WindSettings().iterations == 4000
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "sim_run", {"type": "wind", "part_id": "cube", "reason": "drag",
                                       "requirements": [{"id": "d", "kind": "max_drag_n",
                                                         "target": 0.5}]},
        call_id="w1", workdir=rt.workdir(rid)))
    assert out.ok, out.summary
    step = rt.store.steps(rid, kinds=[StepKind.SIM_RUN])[-1]
    data = step.payload["output"]["data"]
    assert len(json.dumps(step.payload["output"])) < MAX_OUTPUT_BYTES
    assert data["card"]["sim_id"] == data["sim_id"] and data["charts_url"].endswith("/charts")
    assert data["checks"][0]["status"] == "fail"           # 1.0 N is clear over 0.5 N +- 30%
    assert data["card"]["fidelity_note"] == "low fidelity · ±30% (validated)"
    assert "skin-friction" in data["card"]["notes"][0]
    assert "±30% (validated)" in out.summary and "skin-friction" in out.summary
    rec = client.get(f"/sims/{data['sim_id']}", headers=AUTH).json()
    assert len(rec["results"]["convergence"]["history"]) == 400   # the bulk stays on disk
    assert rec["results"]["model_error_band"] == 0.3
    assert rec["results"]["convergence_band"] == 0.02
    charts = client.get(data["charts_url"], headers=AUTH).json()["charts"]
    assert {c["id"] for c in charts} >= {"cd_history", "cd_vs_yaw"}
    # design_review keeps the study id and a fail stays a fail at this size
    spec = {"requirements": [{"id": "drag", "kind": "custom", "target": {"max_drag_n": 0.5}}]}
    rev = client.portal.call(functools.partial(
        rt.gate.call, rid, "design_review", {"part_id": "cube", "spec": spec},
        call_id="rv", workdir=rt.workdir(rid)))
    row = next(r for r in rev.data["table"] if r["requirement_id"] == "drag")
    assert row["status"] == "fail" and row["sim_id"].startswith("sim_")
    assert rev.data["verdict"] == "fail"


class _FakeGate:
    """Returns a spilled output (data {}) but leaves the record on disk, like a real spill."""

    def __init__(self, parts):
        self.parts, self.payloads = parts, []

    async def call(self, run_id, name, payload, *, call_id, workdir):
        self.payloads.append(payload)
        sid = f"sim_{len(self.payloads):012x}"
        d = self.parts / payload["part_id"] / "sims" / sid
        d.mkdir(parents=True)
        rec = {"sim_id": sid, "part_id": payload["part_id"], "type": payload["type"],
               "call_id": call_id, "status": "done", "fidelity_class": "fem",
               "results": {}, "warnings": [],
               "checks": [{"requirement_id": r["id"], "kind": r["kind"], "target": r["target"],
                           "measured": 0.9, "unit": "", "status": "fail", "sim_id": sid}
                          for r in payload["requirements"]]}
        (d / "result.json").write_text(json.dumps(rec))
        return ToolOutput(summary="spilled", data={})


async def test_review_recovers_spilled_children_and_caps_every_child(tmp_path):
    parts = tmp_path / "parts"
    (parts / "pt").mkdir(parents=True)
    (parts / "pt" / "metrics.json").write_text(json.dumps(
        {"bbox": {"size": [10, 10, 10]}, "volume_mm3": 1000}))
    gate = _FakeGate(parts)
    pol = AgentPolicy(run_id="r", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    ctx = SimpleNamespace(workdir=tmp_path, run_id="r", call_id="c", services={"gate": gate},
                          policy=pol, events=None, store=None)
    spec = {"requirements": [{"id": "sf", "kind": "min_safety_factor", "target": 2,
                              "load_case_id": "a"},
                             {"id": "dr", "kind": "max_drag_n", "target": 1}],
            "load_cases": [{"id": "a", "fix": "bottom", "load": "top", "newtons": 5}]}
    out = await design_review.handler(ctx, ReviewArgs(part_id="pt", spec=spec,
                                                      budget_minutes=1))
    by = {r["requirement_id"]: r for r in out.data["table"]}
    assert by["sf"]["status"] == "fail" and by["sf"]["sim_id"] == "sim_000000000001"
    assert by["dr"]["status"] == "fail" and by["dr"]["sim_id"] == "sim_000000000002"
    static, wind = gate.payloads
    assert static["settings"]["solver"]["timeout_s"] <= 31   # half of the 1-minute budget
    assert wind["settings"]["timeout_s"] <= 61


class _DenyingGate:
    """A child sim_run the run's policy forbids, as a spawned verifier used to hit before the
    implied-classes fix. design_review must report the row as not checked, never fail."""

    async def call(self, run_id, name, payload, *, call_id, workdir):
        raise MisesError(ErrorCode.DENIED, "tool class local_mutate is not allowed for this run")


async def test_denied_child_study_is_not_checked_not_fail(tmp_path):
    parts = tmp_path / "parts"
    (parts / "pt").mkdir(parents=True)
    (parts / "pt" / "metrics.json").write_text(json.dumps(
        {"bbox": {"size": [10, 10, 10]}, "volume_mm3": 1000}))
    pol = AgentPolicy(run_id="r", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    ctx = SimpleNamespace(workdir=tmp_path, run_id="r", call_id="c",
                          services={"gate": _DenyingGate()}, policy=pol, events=None, store=None)
    spec = {"requirements": [{"id": "sf", "kind": "min_safety_factor", "target": 2,
                              "load_case_id": "a"}],
            "load_cases": [{"id": "a", "fix": "bottom", "load": "top", "newtons": 5}]}
    out = await design_review.handler(ctx, ReviewArgs(part_id="pt", spec=spec, budget_minutes=1))
    by = {r["requirement_id"]: r for r in out.data["table"]}
    assert by["sf"]["status"] == "warn" and by["sf"].get("checked") is False
    assert "denied" in (by["sf"].get("note") or "")
    assert out.data["verdict"] != "fail"       # no evidence is "not checked", never a fail


class _PassGate:
    """A child study that passes its checks; records the payload it was called with."""

    def __init__(self):
        self.payloads = []

    async def call(self, run_id, name, payload, *, call_id, workdir):
        self.payloads.append(payload)
        sid = "sim_pass0000001"
        checks = [{"requirement_id": r["id"], "kind": r["kind"], "target": r["target"],
                   "measured": 99.0, "unit": "", "status": "pass", "sim_id": sid}
                  for r in payload["requirements"]]
        return ToolOutput(summary="ok", data={"sim_id": sid, "checks": checks, "card": {}})


async def test_estimate_load_case_pass_becomes_warn(tmp_path):
    parts = tmp_path / "parts"
    (parts / "pt").mkdir(parents=True)
    (parts / "pt" / "metrics.json").write_text(json.dumps(
        {"bbox": {"size": [10, 10, 10]}, "volume_mm3": 1000}))
    gate = _PassGate()
    pol = AgentPolicy(run_id="r", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    ctx = SimpleNamespace(workdir=tmp_path, run_id="r", call_id="c",
                          services={"gate": gate}, policy=pol, events=None, store=None)
    spec = {"requirements": [{"id": "sf", "kind": "min_safety_factor", "target": 1.5,
                              "load_case_id": "impact"}],
            "load_cases": [{"id": "impact", "fix": "bottom", "load": "top", "newtons": 50,
                            "estimate": True}]}
    out = await design_review.handler(ctx, ReviewArgs(part_id="pt", spec=spec, budget_minutes=1))
    assert gate.payloads[0]["estimate"] is True            # estimate flag reaches sim_run
    row = next(r for r in out.data["table"] if r["requirement_id"] == "sf")
    assert row["status"] == "warn" and row.get("estimate") is True
    assert "estimate" in row["note"] and out.data["verdict"] != "pass"


def test_model_error_band_table_covers_every_level():
    assert set(MODEL_ERROR_BAND) == {1, 2, 3, 4}
    for level, rows in VALIDATION.items():  # the band is never tighter than what was measured
        assert all(abs(e) <= MODEL_ERROR_BAND[level] for *_, e in rows)
    assert model_error_band(12) == (0.30, 2) and model_error_band(16) == (0.15, 3)
    assert model_error_band(9) == (0.40, 1) and model_error_band(6) == (0.40, 1)
    assert "PREVIEW" in WindSettings.model_json_schema()["properties"]["refinement"][
        "description"]


def test_memory_cap():
    assert memory_cap_mb(24) == 8192
    assert memory_cap_mb(8) == 2048
    assert memory_cap_mb(24, local_models_gb=14) == 24 * 1024 - 6 * 1024 - 14 * 1024
    assert memory_cap_mb(4) == 64
    assert memory_cap_mb(0) == 8192  # unknown RAM: assume 16 GB


@pytest.mark.timeout(60)
async def test_memory_watchdog_kills_an_over_budget_process(tmp_path):
    script = tmp_path / "hog.py"
    script.write_text("import os, time\nopen('pid', 'w').write(str(os.getpid()))\n"
                      "b = bytearray(600 * 1024 * 1024)\n"
                      "for i in range(0, len(b), 4096):\n    b[i] = 1\ntime.sleep(30)\n")
    job = tmp_path / "job"
    job.mkdir()
    t0 = time.monotonic()
    with pytest.raises(SimError) as e:
        await run_sandboxed([sys.executable, "-I", str(script)], job, timeout_s=40,
                            what="the hog", memory_limit_mb=150)
    assert e.value.code == "memory" and "over its 150 MB budget" in e.value.message
    assert time.monotonic() - t0 < 25
    with pytest.raises(ProcessLookupError):
        os.kill(int((job / "pid").read_text()), 0)


def test_estimate_includes_the_measured_per_cell_peak():
    from mises.sim.cfd.setup import estimate_mb, fit_budget, plan_grid
    g = plan_grid(__import__("numpy").zeros(3), __import__("numpy").ones(3) * 100, 12, 5)
    assert estimate_mb(g) == pytest.approx(150 + g.cells * 1100 / 1e6)
    g2 = fit_budget(__import__("numpy").zeros(3), __import__("numpy").ones(3) * 100, 2, 5,
                    int(estimate_mb(g) / 0.8) + 1)
    assert g2 is not None and g2.cells_across == 12
    assert fit_budget(__import__("numpy").zeros(3), __import__("numpy").ones(3) * 100, 2, 5,
                      int(estimate_mb(g))).cells_across < 12   # needs the 20% margin


@pytest.mark.slow
@pytest.mark.timeout(240)
def test_bad_material_env_or_face_is_refused_before_a_run(client, rt):
    _build(client, rt, "cube", CUBE)
    before = len(client.get("/runs", headers=AUTH).json())
    case = {"name": "a", "supports": [{"kind": "fixed", "face": "bottom"}],
            "loads": [{"kind": "force", "face": "top", "newtons": 1, "direction": "-z"}]}
    bodies = [
        {"type": "static", "part_id": "cube", "settings": {"load_cases": [case],
                                                           "material": {"name": "Kryptonite"}}},
        {"type": "static", "part_id": "cube", "settings": {"load_cases": [case],
                                                           "environment": "Mars"}},
        {"type": "static", "part_id": "cube", "settings": {"load_cases": [
            {**case, "supports": [{"kind": "fixed", "face": "f99"}]}]}},
        {"type": "modal", "part_id": "cube", "settings": {"mesh": {"refine_faces": ["f77"]}}},
        {"part_id": "cube", "fix": "bottom", "load": {"face": "top", "newtons": 1,
                                                     "direction": "-z"}, "material": "nope"},
        {"type": "static", "part_id": "cube", "settings": {"load_cases": [
            {**case, "loads": [{"kind": "remote_force", "face": "top", "newtons": 1,
                                "direction": "-z", "point": ["nan", 0, 0]}]}]}},
    ]
    for b in bodies:
        r = client.post("/sims", headers=AUTH, json=b)
        assert r.status_code == 400, (b, r.text)
    assert len(client.get("/runs", headers=AUTH).json()) == before
    assert not list((rt.home / "parts" / "cube" / "sims").glob("*")) if (
        rt.home / "parts" / "cube" / "sims").exists() else True


def test_probe_and_section_reject_non_finite_values(client, rt):
    d = rt.home / "parts" / "p" / "sims" / "sim_00000000000f"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"sim_id": "sim_00000000000f", "part_id": "p",
                                               "status": "done", "type": "static"}))
    for params in ({"x": "nan", "y": 0, "z": 0}, {"x": 0, "y": "inf", "z": 0}):
        assert client.get("/sims/sim_00000000000f/probe", headers=AUTH,
                          params=params).status_code == 400
    r = client.get("/sims/sim_00000000000f/section", headers=AUTH,
                   params={"axis": "z", "offset": "nan", "channel": "von_mises"})
    assert r.status_code == 400
    r = client.get("/sims/sim_00000000000f/section", headers=AUTH,
                   params={"axis": "z", "offset": 0, "channel": "von_mises", "n": 500})
    assert r.status_code == 400


async def test_setup_failure_leaves_no_temp_dir(tmp_path):
    part = tmp_path / "parts" / "p"
    part.mkdir(parents=True)
    (part / "metrics.json").write_text("{}")  # no model.step: the snapshot copy fails
    ctx = SimpleNamespace(workdir=tmp_path, run_id="r", call_id="c", services={}, events=None)
    model = studies.validate_settings("wind", {})
    # the snapshot reads the part via safe_io (C6) and refuses a missing model.step
    with pytest.raises(MisesError):
        await studies.run_study(ctx, "wind", "p", part, model)
    assert not list((tmp_path / "sim_tmp").glob("*"))


def test_vec3_fields_must_be_finite():
    for bad in (float("nan"), float("inf"), 1e9):
        with pytest.raises(SimError):
            studies.validate_settings("static", {"load_cases": [{
                "name": "a", "supports": [{"kind": "pinned", "face": "f1",
                                           "axis": [bad, 0, 1]}],
                "loads": [{"kind": "gravity"}]}]})


def test_compact_output_always_fits():
    big = {"sim_id": "sim_000000000abc", "part_id": "p", "type": "static", "status": "done",
           "fidelity_class": "fem", "warnings": ["w" * 5000] * 50,
           "results": {"cases": [{"name": f"c{i}", "max_von_mises_mpa": 1.0, "junk": "x" * 3000}
                                 for i in range(8)],
                       "convergence": {"history": [{"nodes": i, "metric": 1.0}
                                                   for i in range(5000)]},
                       "blob": ["y" * 100] * 500},
           "checks": [{"requirement_id": f"r{i}", "kind": "min_safety_factor", "target": 2,
                       "status": "fail", "sim_id": "sim_000000000abc", "extra": "z" * 500}
                      for i in range(40)],
           "material": {"name": "PLA", "source": "s" * 5000}}
    out = studies.compact(big)
    assert len(json.dumps(out)) <= studies.COMPACT_MAX_BYTES
    assert out["sim_id"] == "sim_000000000abc" and out["card"]["sim_id"] == "sim_000000000abc"
    assert len(out["checks"]) == 40 and all(c["status"] == "fail" for c in out["checks"])
    assert "extra" not in out["checks"][0]
