"""study_run / study_apply handlers against a fake build and a fake stress study."""
import asyncio
import json
from pathlib import Path

import pytest

from mises.engineering.tools import (StudyApplyArgs, StudyRunArgs, study_apply, study_get,
                                     study_run, StudyGetArgs)
from mises.engineering.spec import Spec
from mises.types import ErrorCode, MisesError, ToolOutput

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
    rec = ctx.store.get_study(out.data["id"])   # full record on disk (the tool result is compact)
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
    assert len(stored["iterations"]) == out.data["iterations_total"] and stored["status"] == "done"
    root = tmp_path / "studies" / out.data["id"]
    left = sorted(p.name for p in root.iterdir())
    assert left == out.data["kept_variants"]
    keep_ns = {it["n"] for it in stored["iterations"] if it["pareto"]} | {out.data["best_id"]}
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
    assert 0 < out.data["iterations_total"] < 24
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
    rec = ctx.store.get_study(out.data["id"])
    best = next(it for it in rec["iterations"] if it["n"] == rec["best_id"])
    feas = [it for it in rec["iterations"] if it["feasible"]]
    assert best["metrics"]["min_sf"] == max(it["metrics"]["min_sf"] for it in feas)


async def test_study_apply_sets_live_vars_and_checks_version(tmp_path, monkeypatch):
    ctx, live = _setup(tmp_path)
    install(monkeypatch)
    out = await study_run.handler(ctx, _args())
    rec = ctx.store.get_study(out.data["id"])
    best = next(it for it in rec["iterations"] if it["n"] == rec["best_id"])

    bad = next(it for it in rec["iterations"] if not it["feasible"])
    with pytest.raises(MisesError):
        await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                      iteration=bad["n"]))

    from mises import edits
    edits.write_version(live, 3)      # C6: versions live in the host-only meta dir
    with pytest.raises(MisesError) as e:
        await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                      iteration=best["n"], base_version=2))
    assert e.value.code == ErrorCode.STALE_VERSION

    out = await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                        iteration=best["n"], base_version=3))
    assert out.ok
    assert json.loads((live / "vars.json").read_text())["wall"] == pytest.approx(
        best["vars"]["wall"])


async def test_study_results_fit_under_the_gate_cap_at_24_evals(tmp_path, monkeypatch):
    """I-2: study_run and study_get stay under the 16 KB Gate cap at the default 24-eval budget,
    with the best + Pareto + lightest-feasible summary; study_get(page) pages the rest."""
    import json as _j

    from mises.gate import MAX_OUTPUT_BYTES
    ctx, _ = _setup(tmp_path)
    install(monkeypatch)
    out = await study_run.handler(ctx, _args(max_evals=24))
    assert len(_j.dumps(out.data, default=float).encode()) <= MAX_OUTPUT_BYTES
    assert out.data["iterations_total"] >= len(out.data["iterations"])
    assert out.data["best_id"] in {it["n"] for it in out.data["iterations"]}

    got = await study_get.handler(ctx, StudyGetArgs(study_id=out.data["id"]))
    assert len(_j.dumps(got.data, default=float).encode()) <= MAX_OUTPUT_BYTES

    # a page reads every evaluation; fields narrows each row
    page = await study_get.handler(ctx, StudyGetArgs(study_id=out.data["id"], page=0,
                                                     fields=["n", "metrics"]))
    assert len(_j.dumps(page.data, default=float).encode()) <= MAX_OUTPUT_BYTES
    assert page.data["iterations"] and set(page.data["iterations"][0]) == {"n", "metrics"}
    assert len(page.data["iterations"]) == page.data["iterations_total"]  # all 24 fit one page


async def test_study_apply_passes_base_version_through_as_is(tmp_path, monkeypatch):
    """C6/I1: an omitted base_version is passed to set_vars as None (the run's last read), not
    forced to the current version, so an apply cannot overwrite an edit the run never saw."""
    import mises.engineering.tools as et
    ctx, _ = _setup(tmp_path)
    install(monkeypatch)
    out = await study_run.handler(ctx, _args())
    rec = ctx.store.get_study(out.data["id"])
    best = next(it for it in rec["iterations"] if it["n"] == rec["best_id"])

    seen = {}

    async def fake_set_vars(c, a):
        seen["base_version"] = a.base_version
        return ToolOutput(summary="ok", data={"part_id": a.part_id})
    from types import SimpleNamespace
    monkeypatch.setattr(et, "mirror_part_set_vars", SimpleNamespace(handler=fake_set_vars))

    await study_apply.handler(ctx, StudyApplyArgs(study_id=rec["id"], part_id="frame_x",
                                                  iteration=best["n"]))
    assert seen["base_version"] is None                 # not forced to the current version


MODAL_SPEC = DRONE_SPEC.model_copy(update={"requirements": [
    *DRONE_SPEC.requirements,
    *Spec.model_validate({"requirements": [{"id": "prop_band", "kind": "custom",
                                            "target": {"avoid_band_hz": [380, 420]}}]}
                         ).requirements]})


def _fake_modal(calls, fail_below_wall):
    """First mode = 100 Hz per mm of wall: a variant under `fail_below_wall` resonates."""
    import mises.engineering.tools as et

    async def dyn(sctx, args):
        v = json.loads((Path(sctx.workdir) / "parts" / args.part_id / "vars.json").read_text())
        calls.append((args.type, v["wall"]))
        status = "fail" if v["wall"] < fail_below_wall else "pass"
        return ToolOutput(summary=args.type, data={"checks": [
            {"requirement_id": r["id"], "kind": r["kind"], "target": r["target"],
             "measured": 100 * v["wall"], "unit": "Hz", "status": status}
            for r in args.requirements]})
    return et, dyn


async def test_modal_targets_are_checked_on_the_winners_only(tmp_path, monkeypatch):
    """I-3: an avoid_band_hz target is checked by a modal study on the winners (best first, at
    most DYNAMIC_MAX_VARIANTS), never on every evaluation; a winner that resonates is dropped
    and the best is re-picked."""
    import mises.engineering.tools as et
    ctx, _ = _setup(tmp_path, MODAL_SPEC)
    install(monkeypatch)
    calls: list = []
    _, dyn = _fake_modal(calls, fail_below_wall=0.0)              # first: everything passes
    monkeypatch.setattr(et, "_run_dyn_sim", dyn)
    out = await study_run.handler(ctx, _args())
    rec = ctx.store.get_study(out.data["id"])
    assert calls and {t for t, _ in calls} == {"modal"}
    assert len(calls) <= et.DYNAMIC_MAX_VARIANTS < len(rec["iterations"])
    assert rec["dynamic"]["failed"] == [] and rec["best_id"] in rec["dynamic"]["checked"]
    first_best = rec["best_id"]
    best_wall = next(i for i in rec["iterations"] if i["n"] == first_best)["vars"]["wall"]

    # now the lightest winner resonates: it is made infeasible and a heavier one wins
    ctx2, _ = _setup(tmp_path / "b", MODAL_SPEC)
    calls.clear()
    _, dyn = _fake_modal(calls, fail_below_wall=best_wall + 1e-6)
    monkeypatch.setattr(et, "_run_dyn_sim", dyn)
    out = await study_run.handler(ctx2, _args())
    rec = ctx2.store.get_study(out.data["id"])
    assert rec["dynamic"]["failed"]
    bad = next(i for i in rec["iterations"] if i["n"] == rec["dynamic"]["failed"][0])
    assert not bad["feasible"] and bad["metrics"]["dynamic_checks"][0]["status"] == "fail"
    if rec["best_id"] is not None:
        best = next(i for i in rec["iterations"] if i["n"] == rec["best_id"])
        assert best["vars"]["wall"] > best_wall and best["feasible"]


async def test_interrupted_study_is_reported(tmp_path, monkeypatch):
    ctx, _ = _setup(tmp_path)
    ctx.store.create_study("study_abcdefabcdef", PID,
                           {"id": "study_abcdefabcdef", "project_id": PID, "part_id": "frame_x",
                            "run_id": ctx.run_id, "status": "running", "iterations": [],
                            "best_id": None}, "running")
    ctx.store.update_run(ctx.run_id, status="failed")
    out = await study_get.handler(ctx, StudyGetArgs(study_id="study_abcdefabcdef"))
    assert out.data["status"] == "interrupted"
