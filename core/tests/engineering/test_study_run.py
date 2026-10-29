"""study_run / study_apply handlers against a fake build and a fake stress study."""
import asyncio
import json

import pytest

from mises.engineering.tools import (StudyApplyArgs, StudyRunArgs, study_apply, study_get,
                                     study_run, StudyGetArgs)
from mises.types import ErrorCode, MisesError

from .fakes import (BUILD_FAILS_ABOVE, DRONE_SPEC, THIN_FAILS_BELOW, install, make_ctx,
                    make_part)

PID = "default"


def _setup(tmp_path, spec=DRONE_SPEC):
    ctx = make_ctx(tmp_path)
    live = make_part(tmp_path)
    ctx.store.put_spec(PID, spec.model_dump())
    return ctx, live


def _args(**kw):
    base = dict(part_id="frame_x", variables=[{"name": "wall", "lo": 1.0, "hi": 9.0}],
                max_evals=16)
    return StudyRunArgs(**{**base, **kw})


async def test_failed_sims_and_builds_are_infeasible_and_never_best(tmp_path, monkeypatch):
    ctx, live = _setup(tmp_path)
    build, sim = install(monkeypatch)
    out = await study_run.handler(ctx, _args())
    rec = out.data
    its = rec["iterations"]
    thin = [it for it in its if it["vars"]["wall"] < THIN_FAILS_BELOW]
    thick = [it for it in its if it["vars"]["wall"] > BUILD_FAILS_ABOVE]
    assert thin, "the sweep should include variants whose stress study fails"
    for it in thin:
        assert not it["feasible"] and "crash_drop" in it["metrics"]["sim_failed_by_case"]
    for it in thick:
        assert not it["feasible"] and it["metrics"]["build_error"]
    best = next(it for it in its if it["n"] == rec["best_id"])
    assert best["feasible"] and best["vars"]["wall"] >= THIN_FAILS_BELOW
    assert best["metrics"]["sf_by_case"]["crash_drop"] >= 2.0
    # the shared contract metric names, always present
    for it in its:
        assert {"mass_g", "sf_by_case", "max_disp_mm", "bbox", "min_sf"} <= set(it["metrics"])
    assert rec["status"] == "done"


async def test_live_part_is_never_touched(tmp_path, monkeypatch):
    ctx, live = _setup(tmp_path)
    build, sim = install(monkeypatch)
    before = {f: (live / f).read_bytes() for f in ("vars.json", "metrics.json", "model.step")}
    await study_run.handler(ctx, _args())
    assert live.resolve() not in build.dirs
    assert all(c["workdir"] != tmp_path.resolve() for c in sim.calls)
    assert {f: (live / f).read_bytes() for f in before} == before
    assert not (live / "sims").exists()


async def test_coarse_in_loop_fine_confirms_winner(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    _, sim = install(monkeypatch)
    out = await study_run.handler(ctx, _args())
    loop_calls = [c for c in sim.calls if not c["fine"]]
    fine_calls = [c for c in sim.calls if c["fine"]]
    assert loop_calls and len(fine_calls) == 1
    conf = out.data["confirmation"]
    assert conf["confirmed"] and conf["n"] == out.data["best_id"]


async def test_iterations_persist_and_scratch_is_pruned(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    install(monkeypatch)
    out = await study_run.handler(ctx, _args())
    stored = ctx.store.get_study(out.data["id"])
    assert len(stored["iterations"]) == len(out.data["iterations"]) and stored["status"] == "done"
    root = tmp_path / "studies" / out.data["id"]
    left = sorted(p.name for p in root.iterdir())
    assert left == out.data["kept_variants"]
    keep_ns = {it["n"] for it in out.data["iterations"] if it["pareto"]} | {out.data["best_id"]}
    assert len(left) == len(keep_ns)


async def test_cancel_keeps_partial_results(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    install(monkeypatch, sim_delay=0.05)
    task = asyncio.create_task(study_run.handler(ctx, _args(max_evals=24)))
    for _ in range(400):
        await asyncio.sleep(0.01)
        rows = ctx.store.list_studies(PID)
        if rows and len(rows[0]["iterations"]) >= 3:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rec = ctx.store.list_studies(PID)[0]
    assert rec["status"] == "cancelled" and len(rec["iterations"]) >= 3


async def test_time_budget_keeps_partial_results(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    install(monkeypatch, sim_delay=0.2)
    out = await study_run.handler(ctx, _args(max_evals=24, max_minutes=0.01))
    assert out.data["status"] == "budget_exhausted"
    assert 0 < len(out.data["iterations"]) < 24
    assert any("time budget" in w for w in out.warnings)


async def test_no_solver_is_labelled_geometry_only(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    _, sim = install(monkeypatch, solver=False)
    out = await study_run.handler(ctx, _args(max_evals=6))
    assert not sim.calls and out.data["geometry_only"]
    assert "geometry only" in out.summary


async def test_face_id_load_cases_are_refused(tmp_path, monkeypatch):
    spec = DRONE_SPEC.model_copy(deep=True)
    spec.load_cases[0].fix = "f3"
    ctx, _ = _setup(tmp_path, spec)
    install(monkeypatch)
    with pytest.raises(MisesError) as e:
        await study_run.handler(ctx, _args())
    assert e.value.code == ErrorCode.INVALID_INPUT and "presets" in (e.value.hint or "")


async def test_strength_first_objective(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    install(monkeypatch)
    out = await study_run.handler(ctx, _args(objective="max:min_sf"))
    best = next(it for it in out.data["iterations"] if it["n"] == out.data["best_id"])
    feas = [it for it in out.data["iterations"] if it["feasible"]]
    assert best["metrics"]["min_sf"] == max(it["metrics"]["min_sf"] for it in feas)


async def test_study_apply_sets_live_vars_and_checks_version(tmp_path, monkeypatch):
    ctx, live = _setup(tmp_path)
    install(monkeypatch)
    rec = (await study_run.handler(ctx, _args())).data
    best = next(it for it in rec["iterations"] if it["n"] == rec["best_id"])

    bad = next(it for it in rec["iterations"] if not it["feasible"])
    with pytest.raises(MisesError):
        await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                      iteration=bad["n"]))

    (live / "version.json").write_text(json.dumps({"version": 3}))
    with pytest.raises(MisesError) as e:
        await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                      iteration=best["n"], base_version=2))
    assert e.value.code == ErrorCode.STALE_VERSION

    out = await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                        iteration=best["n"], base_version=3))
    assert out.ok
    assert json.loads((live / "vars.json").read_text())["wall"] == pytest.approx(
        best["vars"]["wall"])


async def test_interrupted_study_is_reported(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    ctx.store.create_study("study_abcdefabcdef", PID,
                           {"id": "study_abcdefabcdef", "project_id": PID, "part_id": "frame_x",
                            "run_id": ctx.run_id, "status": "running", "iterations": [],
                            "best_id": None}, "running")
    ctx.store.update_run(ctx.run_id, status="failed")
    out = await study_get.handler(ctx, StudyGetArgs(study_id="study_abcdefabcdef"))
    assert out.data["status"] == "interrupted"
