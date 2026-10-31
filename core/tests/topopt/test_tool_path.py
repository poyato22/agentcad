"""The real tool path: a built part -> topopt_run in the sandboxed worker -> CalculiX ->
topopt_accept builds the new part. Slow; needs ccx."""
import asyncio

import pytest

from mises.cad.tools import CreateArgs, PartArgs, mirror_measure, mirror_part_create
from mises.sim.ccx import find_ccx
from mises.topopt import jobs, tools
from mises.topopt.spec import AcceptArgs, IdArgs, TopoptRunArgs

from .test_jobs import _ctx

pytestmark = pytest.mark.slow
needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")
BLOCK = ('VARS = {}\ndef build(v):\n'
         '    return Box(24, 16, 8, align=(Align.MIN, Align.MIN, Align.MIN))\n')
ARGS = dict(part_id="block", fixtures=[{"face": "x-min"}], resolution_mm=1.0,
            load_cases=[{"name": "end", "loads": [{"face": "x-max", "newtons": 40,
                                                   "direction": "-z"}]}],
            volume_fraction=0.4, material="PLA")


@pytest.mark.timeout(900)
@needs_ccx
async def test_run_verify_and_accept_through_the_sandbox(tmp_path):
    ctx = _ctx(tmp_path)
    assert (await mirror_part_create.handler(ctx, CreateArgs(part_id="block", script=BLOCK))).ok
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**ARGS, wait=True))
    assert out.ok and out.data["status"] == "done", out.summary
    v = out.data["variants"][0]
    assert v["verified"] and v["verify"]["safety_factor"] > 1.5
    assert abs(v["compliance_error_pct"]) < 5.0
    assert 0 < v["mass_g"] < 0.5 * 3072 / 1000 * 1.24
    assert any(e.type == "topopt_progress" and e.data.get("preview")
               for e in ctx.events.events)
    acc = await tools.topopt_accept.handler(ctx, AcceptArgs(topopt_id=out.data["topopt_id"],
                                                            part_id="block"))
    assert acc.ok, acc.summary
    m = await mirror_measure.handler(ctx, PartArgs(part_id="block_generative"))
    assert m.data["volume_mm3"] == pytest.approx(v["mass_g"] / 1.24 * 1000, rel=0.01)


@pytest.mark.timeout(300)
async def test_cancel_kills_the_sandboxed_worker(tmp_path):
    ctx = _ctx(tmp_path)
    assert (await mirror_part_create.handler(ctx, CreateArgs(part_id="block", script=BLOCK))).ok
    out = await tools.topopt_run.handler(ctx, TopoptRunArgs(**{**ARGS, "resolution_mm": 0.5},
                                                            verify=False))
    tid = out.data["topopt_id"]
    for _ in range(600):
        rec = jobs.read_record(tmp_path, tid)
        if rec["variants"][0].get("it"):
            break
        await asyncio.sleep(0.1)
    assert (await tools.topopt_cancel.handler(ctx, IdArgs(topopt_id=tid))).ok
    t = jobs._active.get(tid)
    if t is not None:
        with pytest.raises(asyncio.CancelledError):
            await t
    assert jobs.read_record(tmp_path, tid)["status"] == "cancelled"
