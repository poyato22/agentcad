"""Generative-design jobs and tools with a fake worker: background start, progress events on
the sim stream, wait / status / result, cancel, Pareto ranking, accept."""
import asyncio
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from mises.store import AgentPolicy
from mises.topopt import geometry as geo
from mises.topopt import jobs, tools, verify
from mises.topopt.spec import (AcceptArgs, IdArgs, TopoptGenerateArgs, TopoptRunArgs,
                               WaitArgs)
from mises.types import Caps, ErrorCode, MisesError, SafetyClass


class _Events:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


def _ctx(tmp_path, scope=("*",)):
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=list(scope), read_scope=list(scope),
                      caps=Caps(), allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id="r1", events=_Events(), services={},
                           policy=pol, call_id="c1", store=None)


def _part(tmp_path, pid="bracket"):
    d = tmp_path / "parts" / pid
    d.mkdir(parents=True)
    metrics = {"volume_mm3": 3072.0, "bbox": {"min": [0, 0, 0], "max": [24, 16, 8],
                                              "size": [24, 16, 8]},
               "face_list": [{"id": f"f{i}", "center": [0, 8, 4]} for i in range(6)]}
    (d / "metrics.json").write_text(json.dumps(metrics))
    (d / "model.step").write_text("ISO-10303-21; fake")
    return d


def _box_stl(path, s=10.0):
    V = np.array([[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0], [0, 0, s], [s, 0, s], [s, s, s],
                  [0, s, s]], float)
    T = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6],
                  [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    geo.write_stl(path, V, T)


class FakeWorker:
    """Stands in for the sandboxed optimizer: writes progress then the artifacts. The volume
    (and so the mass) follows the variant's volume fraction."""

    def __init__(self, hang=False, fail_for=()):
        self.hang = hang
        self.fail_for = set(fail_for)
        self.started = asyncio.Event()
        self.calls = []

    async def __call__(self, argv, job_dir, **kw):
        spec = json.loads((job_dir / "spec.json").read_text())
        self.calls.append(spec)
        self.started.set()
        for it in (5, 10):
            (job_dir / "progress.json").write_text(json.dumps(
                {"phase": "optimize", "it": it, "max_iter": 60, "c": 100.0 / it, "vf": 0.3,
                 "preview": it, "pct": it}))
            (job_dir / "preview.glb").write_bytes(b"glTF-fake")
            await asyncio.sleep(0.6)
        if self.hang:
            await asyncio.sleep(60)
        vf = spec["volume_fraction"]
        if vf in self.fail_for:
            return 1, "", "TOPOPT_ERROR: TopoptError: the design space has no elements"
        _box_stl(job_dir / "part.stl")
        (job_dir / "part.step").write_text("ISO-10303-21; fake result")
        (job_dir / "report.json").write_text(json.dumps({
            "volume_mm3": 3072.0 * vf, "faces": 900, "step_bytes": 123456, "converged": True,
            "compliance_bw": 50.0 / vf, "iters": 30, "thin_member_pct": 3.0,
            "printability": {"overhang_area_pct": 10.0}, "history": []}))
        return 0, "", ""


def _fake_verify(sf_by_vf=None):
    async def fake(job, material, ccx, *, size_mm, progress=None, threads=2):
        spec = json.loads((job / "spec.json").read_text())
        vf = spec["volume_fraction"]
        sf = (sf_by_vf or {}).get(vf, 3.0)
        return {"compliance": 50.0 / vf * 0.98, "max_deflection_mm": 0.2 / vf,
                "max_von_mises_mpa": 35.7 / sf, "safety_factor": sf, "allowable_mpa": 35.7,
                "nodes": 1000, "cases": []}
    return fake


@pytest.fixture
def fake(monkeypatch):
    w = FakeWorker()
    monkeypatch.setattr(jobs, "run_sandboxed", w)
    monkeypatch.setattr(verify, "verify", _fake_verify())
    monkeypatch.setattr(jobs, "POLL_S", 0.05)
    import mises.sim.ccx as ccx_mod
    monkeypatch.setattr(ccx_mod, "find_ccx", lambda: "/usr/bin/true")
    return w


BASE = dict(part_id="bracket", fixtures=[{"face": "x-min"}],
            load_cases=[{"name": "tip", "loads": [{"face": "x-max", "newtons": 100,
                                                   "direction": "-z"}]}])


async def _settle(rec_id, timeout=20):
    t = jobs._active.get(rec_id)
    if t is not None:
        await asyncio.wait_for(asyncio.shield(t), timeout)


async def test_run_returns_at_once_and_reports_on_the_sim_stream(tmp_path, fake):
    _part(tmp_path)
    ctx = _ctx(tmp_path)
    t0 = time.perf_counter()
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE, volume_fraction=0.3))
    assert time.perf_counter() - t0 < 1.0
    assert out.ok and out.data["status"] == "running" and out.data["stream"] == "sim"
    tid = out.data["topopt_id"]
    st = await tools.topopt_status.handler(ctx, IdArgs(topopt_id=tid))
    assert st.data["status"] in ("queued", "running")
    res = await tools.topopt_wait.handler(ctx, WaitArgs(topopt_id=tid, timeout_s=20))
    assert res.data["status"] == "done"
    v = res.data["variants"][0]
    assert v["verified"] and v["passes"] and v["rank"] == 1 and res.data["recommended"] == "v0"
    assert v["mass_g"] == pytest.approx(3072 * 0.3 / 1000 * 1.24)
    assert v["compliance_error_pct"] == pytest.approx(-2.0)
    kinds = [e.type for e in ctx.events.events]
    assert "topopt_progress" in kinds and kinds[-1] == "topopt_finished"
    assert all(e.data["stream"] == "sim" for e in ctx.events.events)
    prog = [e for e in ctx.events.events if e.type == "topopt_progress" and e.data.get("preview")]
    assert prog and prog[-1].data["preview_url"].endswith("preview.glb?v=10")
    fin = ctx.events.events[-1].data
    assert fin["status"] == "done" and "recommended v0" in fin["summary"]
    assert (tmp_path / "topopt" / tid / "v0" / "render.png").stat().st_size > 100
    # the worker got a resolved spec: ~50k voxels in 3072 mm^3, filter 2 voxels
    spec = fake.calls[0]
    assert spec["h"] == pytest.approx(0.39) and spec["filter_radius"] == pytest.approx(2.0)
    assert spec["load_cases"][0]["loads"][0]["direction"] == [0, 0, -1]


async def test_generate_sweeps_and_ranks_unverified_and_failing_last(tmp_path, fake,
                                                                     monkeypatch):
    _part(tmp_path)
    monkeypatch.setattr(verify, "verify", _fake_verify({0.12: 0.9}))
    ctx = _ctx(tmp_path)
    out = await tools.topopt_generate.handler(ctx, TopoptGenerateArgs(
        **BASE, sweep={"volume_fraction": [0.12, 0.25, 0.35]}))
    tid = out.data["topopt_id"]
    await _settle(tid)
    res = (await tools.topopt_result.handler(ctx, IdArgs(topopt_id=tid))).data
    by = {v["volume_fraction"]: v for v in res["variants"]}
    assert by[0.12]["passes"] is False and by[0.12]["rank"] == 3     # SF 0.9 < 1.5
    assert by[0.25]["pareto"] and by[0.35]["pareto"]
    assert by[0.25]["rank"] == 1 and by[0.35]["rank"] == 2
    assert res["recommended"] in ("v1", "v2") and res["pareto"] == ["v1", "v2"]
    # the low-vf variant was refined automatically
    assert by[0.12]["resolution_mm"] < by[0.35]["resolution_mm"]


def test_rank_never_puts_an_unverified_variant_first():
    vs = [{"id": "v0", "status": "done", "mass_g": 5.0, "verify": None},
          {"id": "v1", "status": "done", "mass_g": 9.0,
           "verify": {"safety_factor": 2.0, "max_deflection_mm": 1.0}},
          {"id": "v2", "status": "failed", "mass_g": 1.0},
          {"id": "v3", "status": "done", "mass_g": 12.0,
           "verify": {"safety_factor": 2.0, "max_deflection_mm": 1.5}}]
    pareto, best = jobs.rank(vs, min_safety_factor=1.5, max_deflection_mm=None)
    order = sorted(vs, key=lambda v: v["rank"])
    assert [v["id"] for v in order] == ["v1", "v3", "v0", "v2"]
    assert pareto == ["v1"] and best == "v1"
    assert vs[0]["passes"] is None and vs[0]["verified"] is False


def test_rank_with_a_deflection_target_picks_the_lightest_passing():
    vs = [{"id": f"v{i}", "status": "done", "mass_g": m,
           "verify": {"safety_factor": 3.0, "max_deflection_mm": d}}
          for i, (m, d) in enumerate([(6.5, 2.5), (11.8, 0.75), (18.1, 0.44)])]
    pareto, best = jobs.rank(vs, min_safety_factor=1.5, max_deflection_mm=1.0)
    assert best == "v1" and pareto == ["v1", "v2"] and vs[0]["passes"] is False
    # without a target: the stiffest per gram is recommended, and ranked first
    pareto, best = jobs.rank(vs, min_safety_factor=1.5, max_deflection_mm=None)
    assert best == "v2" and vs[2]["rank"] == 1 and vs[0]["rank"] == 2 and vs[1]["rank"] == 3


def test_expand_variants():
    a = TopoptGenerateArgs(**BASE, variants=[{"volume_fraction": 0.2}, {"volume_fraction": 0.2},
                                             {"filter_radius_mm": 3}])
    assert jobs.expand(a) == [{"volume_fraction": 0.2}, {"filter_radius_mm": 3}]
    b = TopoptGenerateArgs(**BASE, sweep={"volume_fraction": [0.1, 0.2],
                                          "filter_radius_mm": [1.5, 3]}, max_variants=3)
    assert len(jobs.expand(b)) == 3
    c = TopoptRunArgs(**BASE, objective="min_mass", max_deflection_mm=1.0)
    assert [d["volume_fraction"] for d in jobs.expand(c)] == list(jobs.DEFAULT_SWEEP)
    with pytest.raises(ValueError):
        TopoptRunArgs(**BASE, objective="min_mass")
    with pytest.raises(ValueError):
        TopoptGenerateArgs(**BASE, variants=[{"penal": 4}])


async def test_cancel_kills_the_run(tmp_path, monkeypatch):
    _part(tmp_path)
    w = FakeWorker(hang=True)
    monkeypatch.setattr(jobs, "run_sandboxed", w)
    ctx = _ctx(tmp_path)
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE, verify=False))
    tid = out.data["topopt_id"]
    await asyncio.wait_for(w.started.wait(), 5)
    c = await tools.topopt_cancel.handler(ctx, IdArgs(topopt_id=tid))
    assert c.ok
    with pytest.raises(asyncio.CancelledError):
        await _settle(tid)
    rec = jobs.read_record(tmp_path, tid)
    assert rec["status"] == "cancelled" and rec["variants"][0]["status"] == "cancelled"
    assert ctx.events.events[-1].type == "topopt_finished"
    again = await tools.topopt_cancel.handler(ctx, IdArgs(topopt_id=tid))
    assert not again.ok


async def test_stop_reaches_a_generative_design_run_like_any_background_job(tmp_path,
                                                                            monkeypatch):
    """B3 merge: the run is a background job — Stop (jobs.cancel_run, what Runtime.cancel and
    Stop all call) cancels it with no note; GET /jobs lists it; a one-job cancel defers."""
    from mises import jobs as bg
    bg.reset_for_tests()
    _part(tmp_path)
    w = FakeWorker(hang=True)
    monkeypatch.setattr(jobs, "run_sandboxed", w)
    ctx = _ctx(tmp_path)
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE, verify=False))
    tid = out.data["topopt_id"]
    await asyncio.wait_for(w.started.wait(), 5)
    job = bg.get(tid)
    assert job is not None and job.kind == "topopt" and bg.view(job)["cancel_url"] == \
        f"/jobs/{tid}/cancel"
    assert [j.id for j in bg.running()] == [tid]
    assert bg.cancel_run({"r1"}) == [tid]
    with pytest.raises(asyncio.CancelledError):
        await _settle(tid)
    assert jobs.read_record(tmp_path, tid)["status"] == "cancelled"
    assert job.stopped and job.status == "cancelled" and bg.running() == []
    # a second run, stopped on its own (POST /jobs/{id}/cancel): deferred
    w2 = FakeWorker(hang=True)
    monkeypatch.setattr(jobs, "run_sandboxed", w2)
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE, verify=False))
    tid2 = out.data["topopt_id"]
    await asyncio.wait_for(w2.started.wait(), 5)
    assert bg.user_cancel(tid2)
    with pytest.raises(asyncio.CancelledError):
        await _settle(tid2)
    assert bg.get(tid2).defer and bg.get(tid2).status == "cancelled"
    bg.reset_for_tests()


async def test_bad_input_is_refused_before_a_run_exists(tmp_path, fake):
    _part(tmp_path)
    ctx = _ctx(tmp_path)
    with pytest.raises(MisesError) as e:
        await tools.topopt_run.handler(ctx, TopoptRunArgs(**{**BASE, "fixtures": [
            {"face": "f9"}]}))
    assert e.value.code == ErrorCode.INVALID_INPUT and "f9" in e.value.message
    with pytest.raises(MisesError):
        await tools.topopt_run.handler(ctx, TopoptRunArgs(**{**BASE, "part_id": "nope"}))
    assert not (tmp_path / "topopt").exists() or not any((tmp_path / "topopt").iterdir())


async def test_out_of_scope_records_are_hidden(tmp_path, fake):
    _part(tmp_path)
    out = await tools.topopt_run.handler(_ctx(tmp_path), TopoptRunArgs(**BASE))
    tid = out.data["topopt_id"]
    await _settle(tid)
    with pytest.raises(MisesError) as e:
        await tools.topopt_result.handler(_ctx(tmp_path, scope=("other",)),
                                          IdArgs(topopt_id=tid))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


async def test_accept_makes_a_new_part_from_the_variant(tmp_path, fake, monkeypatch):
    from mises.cad.runner import BuildResult
    from mises.topopt import accept as accept_mod
    _part(tmp_path)
    built = {}

    async def fake_build(d, vars, **kw):
        built["script"] = (d / "part.py").read_text()
        built["body"] = (d / "topopt_body.step").read_text()
        m = {"volume_mm3": 900.0, "mass_g": 1.1, "bbox": {"size": [24, 16, 8]}, "valid": True,
             "holes": []}
        (d / "metrics.json").write_text(json.dumps(m))
        return BuildResult(True, metrics=m, vars={})
    monkeypatch.setattr(accept_mod, "run_build", fake_build)
    ctx = _ctx(tmp_path)
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE))
    tid = out.data["topopt_id"]
    await _settle(tid)
    acc = await tools.topopt_accept.handler(ctx, AcceptArgs(topopt_id=tid, part_id="bracket"))
    assert acc.ok and acc.data["part_id"] == "bracket_generative" and acc.data["variant"] == "v0"
    assert "import_step" in built["script"] and "fake result" in built["body"]
    assert jobs.read_record(tmp_path, tid)["accepted"][0]["part_id"] == "bracket_generative"
    assert any(e.type == "topopt_accepted" for e in ctx.events.events)
    with pytest.raises(MisesError):
        await tools.topopt_accept.handler(ctx, AcceptArgs(topopt_id=tid, part_id="other"))


def test_replace_by_an_agent_asks_first_when_the_user_edited_the_part(tmp_path):
    from mises import edits
    ctx = _ctx(tmp_path)
    a = AcceptArgs(topopt_id="to_" + "0" * 12, part_id="bracket", mode="replace",
                   base_version=3)
    assert tools._accept_needs_approval(ctx, a) is None
    edits.log_user_edit(tmp_path, "bracket", version=3, summary="moved a hole", run_id=None)
    assert tools._accept_needs_approval(ctx, a) == tools.APPROVAL_REASON
    assert tools._accept_needs_approval(ctx, a.model_copy(update={"mode": "new"})) is None



async def test_an_accepted_new_part_is_made_of_the_optimized_material(tmp_path, fake, monkeypatch):
    """Plan 9 B2: the new part carries the material the optimization used (PETG here), so its
    tree mass is the variant's: volume x that density; the build weighs it the same."""
    from mises import projects
    from mises.cad.runner import BuildResult
    from mises.store import Store
    from mises.topopt import accept as accept_mod
    _part(tmp_path)
    seen = {}

    async def fake_build(d, vars, **kw):
        seen["density"] = kw.get("density_g_cm3")
        m = {"volume_mm3": 900.0, "mass_g": 900.0 / 1000 * kw.get("density_g_cm3", 1.24),
             "bbox": {"size": [24, 16, 8]}, "valid": True, "holes": []}
        (d / "metrics.json").write_text(json.dumps(m))
        (d / "vars.json").write_text("{}")
        return BuildResult(True, metrics=m, vars={})
    monkeypatch.setattr(accept_mod, "run_build", fake_build)
    store = Store(":memory:")
    store.register_part("bracket", store.ensure_default_project().id)
    ctx = _ctx(tmp_path)
    ctx.store = store
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE, material="PETG"))
    tid = out.data["topopt_id"]
    await _settle(tid)
    acc = await tools.topopt_accept.handler(ctx, AcceptArgs(topopt_id=tid, part_id="bracket"))
    assert acc.ok, acc.summary
    pid = acc.data["part_id"]
    assert seen["density"] == 1.27
    assert store.get_part(pid).material == "PETG"
    view = projects.part_view(store, tmp_path, store.get_part(pid))
    assert view["mass_g"] == pytest.approx(900.0 / 1000 * 1.27) and view["mass_source"] == "built"


async def test_topopt_on_a_material_without_sim_data_is_refused(tmp_path, fake, monkeypatch):
    import dataclasses

    from mises.print import slicer
    from mises.store import Store
    # a filament the sim table lacks (FWC I4: TPU is now the table's TPU 95A)
    monkeypatch.setitem(slicer.MATERIALS, "PVA",
                        dataclasses.replace(slicer.MATERIALS["TPU"]))
    _part(tmp_path)
    store = Store(":memory:")
    store.register_part("bracket", store.ensure_default_project().id)
    store.update_part("bracket", material="PVA")
    ctx = _ctx(tmp_path)
    ctx.store = store
    with pytest.raises(MisesError) as e:
        await tools.topopt_run.handler(ctx, TopoptRunArgs(**BASE))
    assert "PVA has no simulation data" in e.value.message


def test_density_evolution_frames_are_kept_sparse_and_served(tmp_path):
    # DRONE demo: stills / replay of the density forming, after the run
    from mises.topopt import worker
    job = tmp_path / "v0"
    job.mkdir()
    state = {"preview": None, "evo": [], "next_evo": 1}
    for it in range(5, 201, 5):
        (job / "preview.glb").write_bytes(b"glTF" + bytes([it]))
        worker._keep_frame(job, it, state)
    assert state["evo"][:4] == [5, 10, 15, 20] and len(state["evo"]) <= worker.EVO_MAX
    assert all(b > a for a, b in zip(state["evo"], state["evo"][1:])) and state["evo"][-1] > 100
    assert (job / "evo_010.glb").read_bytes() == b"glTF" + bytes([10])
    tid = "to_" + "a" * 12
    d = jobs.job_dir(tmp_path, tid) / "v0"
    d.mkdir(parents=True)
    (d / "evo_005.glb").write_bytes(b"glTF-5")
    data, media = jobs.variant_file(tmp_path, tid, "v0", "evo_005.glb")
    assert data == b"glTF-5" and media == "model/gltf-binary"
    for bad in ("evo_5.glb", "evo_005.glb/../x", "../evo_005.glb", "evo_005.stl"):
        with pytest.raises(MisesError):
            jobs.variant_file(tmp_path, tid, "v0", bad)
    rec = {"topopt_id": tid, "variants": [{"id": "v0", "evolution": [5, 10]}]}
    assert jobs.public(rec)["variants"][0]["urls"]["evolution"] == [
        f"/topopt/{tid}/variants/v0/evo_005.glb", f"/topopt/{tid}/variants/v0/evo_010.glb"]


def test_a_split_result_says_how_to_join_it():
    # DRONE demo: 4 arms, each fixed at its own stack hole: 41.6% dropped, then "the load face
    # tag:pad_fr is not on the variant" with the hint "keep the load face inside the space"
    from mises.sim.types import SimError
    rep = {"islands_dropped_pct": 41.6, "bodies": 4}
    w = jobs.split_warning(rep)
    assert "split into 4 separate bodies" in w and "keep region" in w and "41.6%" in w
    assert "islands" in jobs.split_warning({"islands_dropped_pct": 0.4, "bodies": 2})
    e = SimError("bad_setup", "the load face tag:pad_fr is not on the variant",
                 hint="keep the load face inside the design space")
    assert "dropped" in jobs.verify_hint(e, rep)
    assert jobs.verify_hint(e, {"islands_dropped_pct": 0.0}) == e.hint


def test_the_element_cap_fits_the_memory_budget():
    """The budget follows the grid (measured: 216k elements in a 746k grid peak at 2.5 GB) and
    the largest allowed variant still fits under the hard ceiling with 15% headroom."""
    need = (jobs.BASE_MB + (jobs.MAX_ELEMENTS * jobs.KB_PER_ELEMENT
                            + jobs.MAX_CELLS * jobs.KB_PER_CELL) / 1024)
    assert need <= 0.85 * jobs.MAX_MEMORY_MB
    assert jobs.MAX_ELEMENTS > jobs.TARGET_ELEMENTS
    bbox = {"size": [108, 108, 64]}
    assert jobs.variant_memory_mb(216_000, bbox, 1.0) >= 2500
    assert jobs.variant_memory_mb(3072, {"size": [24, 16, 8]}, 1.0) == jobs.MEMORY_MB
