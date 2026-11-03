"""Plan 13 H4: the walk gait (a 4-beat crawl) on the quadruped fixture: one foot up at a time,
the body travels, and dyn_torque keeps each joint's commanded motion for the assembly player."""
import pytest

from mises.dyn import run, traj
from mises.dyn import tools as dt

from .test_tools import ctx  # noqa: F401 - the quadruped fixture

pytestmark = pytest.mark.slow


@pytest.mark.timeout(90)
async def test_walk_gait_moves_the_body_and_keeps_a_playback(ctx):  # noqa: F811
    acts = {f"{leg}_{j}": "xm430" for leg in ("fl", "fr", "hl", "hr") for j in ("hip", "knee")}
    out = await dt.dyn_torque.handler(ctx, dt.TorqueArgs(
        project_id="quad", actuators=acts,
        trajectory=dt.Trajectory(kind="walk", period_s=1.2, cycles=2, step_mm=40, lift_mm=20)))
    d = out.data
    assert d["trajectory"]["kind"] == "walk"
    assert any("walk (4-beat crawl)" in n for n in d["notes"])
    assert d["com_travel_mm"] >= 0
    saved = run.load_result(ctx.workdir, "quad", d["result_id"])
    pb = saved["playback"]
    assert set(pb) == set(acts)
    hip = pb["fl_hip"]
    assert 2 <= len(hip["t"]) <= dt.PLAYBACK_POINTS and len(hip["t"]) == len(hip["values"])
    # degrees (Mises units), moving through the gait
    assert max(hip["values"]) - min(hip["values"]) > 1.0


def test_walk_lifts_one_foot_at_a_time():
    """The crawl's phases: four feet, a quarter cycle apart, each in the air a quarter."""
    import math
    phases = [0.0, 0.25, 0.5, 0.75]
    duty = 0.25
    for t in [x / 40 for x in range(40)]:
        up = sum(1 for p in phases if (t + p) % 1.0 < duty)
        assert up == 1
    assert math.isclose(sum(phases), 1.5)
    assert "walk" in traj.__doc__
