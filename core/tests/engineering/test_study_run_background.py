"""Plan 9 B3 fix round 1 (I1): an agent's study_run runs in the background; wait: true
blocks as before."""
import asyncio
import dataclasses

from mises import jobs
from mises.engineering.tools import StudyGetArgs, study_get, study_run

from .fakes import DRONE_SPEC, install, make_ctx, make_part
from .test_study_run import _args


async def test_study_run_runs_in_the_background_for_agents(tmp_path, monkeypatch):
    ctx = dataclasses.replace(make_ctx(tmp_path), agent_call=True)
    make_part(tmp_path)
    ctx.store.put_spec("default", DRONE_SPEC.model_dump())
    install(monkeypatch)
    out = await study_run.handler(ctx, _args(max_evals=6))
    sid = out.data["study_id"]
    assert out.data["status"] == "running" and out.data["stream"] == "sim"
    mid = await study_get.handler(ctx, StudyGetArgs(study_id=sid))
    assert mid.data.get("status") != "interrupted"   # running here, not interrupted
    await asyncio.wait({jobs.get(sid).task}, timeout=30)
    assert jobs.get(sid).status == "done"
    assert ctx.store.get_study(sid)["status"] == "done"
    blocking = await study_run.handler(ctx, _args(max_evals=4, wait=True))
    assert blocking.data["status"] == "done"
    jobs.reset_for_tests()


async def test_stopping_a_background_design_study_cancels_its_child_studies(tmp_path,
                                                                           monkeypatch):
    """Review M3: cancel_run reaches the study's in-flight stress evaluations."""
    ctx = dataclasses.replace(make_ctx(tmp_path), agent_call=True)
    make_part(tmp_path)
    ctx.store.put_spec("default", DRONE_SPEC.model_dump())
    _build, sim = install(monkeypatch, sim_delay=5.0)
    import mises.engineering.tools as eng
    cancelled = []

    async def tracked(c, a):
        try:
            return await sim(c, a)
        except asyncio.CancelledError:
            cancelled.append(a.part_id)
            raise
    monkeypatch.setattr(eng, "_run_sim", tracked)
    out = await study_run.handler(ctx, _args(max_evals=6))
    sid = out.data["study_id"]
    for _ in range(200):
        await asyncio.sleep(0.02)
        if sim.calls:
            break
    assert sim.calls, "no evaluation started"
    assert jobs.cancel_run({ctx.run_id}) == [sid]
    await asyncio.wait({jobs.get(sid).task}, timeout=5)
    assert jobs.get(sid).status == "cancelled"
    assert ctx.store.get_study(sid)["status"] == "cancelled"
    assert len(cancelled) == len(sim.calls)          # every in-flight child was cancelled
    jobs.reset_for_tests()


async def test_job_wait_reads_a_blocking_design_study(tmp_path, monkeypatch):
    """Review M4: a study that ran with wait (the Studies page) has no job; its record."""
    from mises.sim.tools import JobArgs, job_wait
    ctx = make_ctx(tmp_path)
    make_part(tmp_path)
    ctx.store.put_spec("default", DRONE_SPEC.model_dump())
    install(monkeypatch)
    out = await study_run.handler(dataclasses.replace(ctx, agent_call=False), _args(max_evals=4))
    got = await job_wait.handler(ctx, JobArgs(job_id=out.data["id"], timeout_s=1))
    assert got.data["id"] == out.data["id"] and got.data["status"] == "done"
