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
