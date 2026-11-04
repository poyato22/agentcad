"""Plan 13 H4: whole-assembly studies (walk, drop, mechanism on the quadruped fixture; launch on
the rocketry reference rocket): a background job with progress events, a record with checks,
charts and the assembly player's playback, the build-type proposal and the FEM hand-off."""
from types import SimpleNamespace

import pytest

from mises import jobs
from mises.assembly.model import AssemblyStore, Instance
from mises.sim import system_study as ss
from mises.store import AgentPolicy
from mises.types import Caps, MisesError, SafetyClass

from rocketry import reference as ref
from .test_tools import ctx  # noqa: F401 - the quadruped fixture

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _jobs():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def _types(ctx):  # noqa: F811
    return [e.type for e in ctx.events.events]


async def test_walk_study_record(ctx):  # noqa: F811
    out = await ss.assembly_study.handler(ctx, ss.StudyArgs(
        project_id="quad", kind="walk",
        settings={"cycles": 1, "period_s": 1.2, "rom_poses": 50, "min_travel_mm": 0}))
    sid = out.data["study_id"]
    rec = ss.load(ctx.workdir, sid)
    assert rec["status"] == "done" and rec["verdict"] in ("pass", "warn", "fail", "not_checked")
    names = [c["name"] for c in rec["checks"]]
    assert "joint torque vs actuators" in names and "range of motion collisions" in names
    assert any(n.startswith("stability:") for n in names) and "travel" in names
    assert rec["charts"] and rec["charts"][0]["series"]
    steps = rec["playback"]["steps"]
    assert 1 <= len(steps) <= ss.MAX_STEPS
    assert steps[0]["follow"] and all(len(f["values"]) >= 2 for f in steps[0]["follow"])
    assert rec["fem"]["available"] and rec["fem"]["via"] == "dyn_to_fem"
    prog = [e.data for e in ctx.events.events if e.type == "assembly_study_progress"]
    assert [p["phase"] for p in prog][:2] == ["queued", "dynamics"] and prog[-1]["phase"] == "done"
    assert "sim_finished" in _types(ctx)
    assert ss.list_records(ctx.workdir, "quad")[0]["study_id"] == sid


async def test_a_trot_is_judged_by_its_body_not_the_cop_on_the_support_line(ctx):  # noqa: F811
    """DOG demo: every trot failed "CoP leaves the support polygon by 0 mm: it tips mid-step"
    (two feet down: the CoP lies on their line, margin 0 by construction)."""
    out = await ss.assembly_study.handler(ctx, ss.StudyArgs(
        project_id="quad", kind="walk",
        settings={"gait": "trot", "cycles": 2, "period_s": 0.8, "step_mm": 30, "lift_mm": 15,
                  "rom_poses": 20}))
    rec = ss.load(ctx.workdir, out.data["study_id"])
    by = {c["name"]: c for c in rec["checks"]}
    gait = by["stability: during the gait"]
    assert gait["unit"] == "deg" and "of level" in gait["reason"]
    assert "stability: CoP during the crawl" not in by
    assert any(m["label"] == "max body tilt" for m in rec["metrics"])


async def test_drop_and_mechanism(ctx):  # noqa: F811
    out = await ss.assembly_study.handler(ctx, ss.StudyArgs(
        project_id="quad", kind="drop", settings={"drop_m": 0.5, "max_g": 5}))
    rec = ss.load(ctx.workdir, out.data["study_id"])
    assert rec["verdict"] == "fail"           # ~7 g at 0.5 m with k = 10 > 5 g
    st = rec["playback"]["steps"]
    assert st[0]["joint_id"] == ss.ROOT and st[0]["from"][2] > st[-1]["to"][2] == 0
    assert {c["id"] for c in rec["charts"]} == {"impact_g", "impact_force"}
    out = await ss.assembly_study.handler(ctx, ss.StudyArgs(
        project_id="quad", kind="mechanism", settings={"rom_poses": 20, "period_s": 0.5}))
    rec = ss.load(ctx.workdir, out.data["study_id"])
    assert rec["status"] == "done" and rec["playback"]["steps"]


async def test_bad_settings_and_proposal(ctx):  # noqa: F811
    with pytest.raises(MisesError, match="bad walk settings"):
        await ss.assembly_study.handler(ctx, ss.StudyArgs(project_id="quad", kind="walk",
                                                          settings={"cycles": 99}))
    rows = ss.propose(ctx, "quad")
    assert rows[0]["kind"] == "walk" and rows[0]["recommended"]
    assert {r["kind"] for r in rows} == set(ss.KINDS)


async def test_fem_hand_off_runs_dyn_to_fem(ctx, monkeypatch):  # noqa: F811
    out = await ss.assembly_study.handler(ctx, ss.StudyArgs(
        project_id="quad", kind="drop", settings={"drop_m": 0.3}))
    sid = out.data["study_id"]
    seen = []

    async def fake_sim_run(c, payload, k):
        from mises.types import ToolOutput
        seen.append(payload)
        return ToolOutput(summary="ok", data={"sim_id": f"s{k}",
                                              "results": {"safety_factor": 3.0}})
    from mises.dyn import tools as dt
    monkeypatch.setattr(dt, "_sim_run", fake_sim_run)
    fem = await ss.assembly_study_fem.handler(ctx, ss.FemArgs(project_id="quad", study_id=sid,
                                                              n_links=2))
    assert fem.data["fem"]["status"] == "pass" and len(seen) == 2
    assert seen[0]["type"] == "drop" and seen[0]["settings"]["drop_m"] == 0.3
    assert ss.load(ctx.workdir, sid)["fem"]["rows"][0]["sim_id"] == "s1"


@pytest.fixture
def rctx(tmp_path):
    ref.write_parts(tmp_path)
    store = AssemblyStore(tmp_path / "assembly.db")
    for i, pid in enumerate(ref.PARTS_A):
        store.add_instance("rkt", Instance(id=f"i{i}", part_id=pid, name=pid, fixed=i == 0))
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id=None, events=None,
                           services={"assembly": store}, policy=pol, call_id="c1",
                           store=None, agent_call=False)


async def test_launch_study(rctx):
    out = await ss.assembly_study.handler(rctx, ss.StudyArgs(
        project_id="rkt", kind="launch",
        settings={"motors": ["F52C"], "max_payload_g": 2, "geometry": ref.geometry("A")}))
    rec = ss.load(rctx.workdir, out.data["study_id"])
    assert rec["status"] == "done" and rec["verdict"] == "fail"   # boost g > 2 g
    assert any(c["name"].startswith("payload g") for c in rec["checks"])
    assert any(c["id"] == "altitude" for c in rec["charts"])
    st = rec["playback"]["steps"]
    assert st[0]["joint_id"] == ss.ROOT and max(s["to"][2] for s in st) > 0
    assert not rec["fem"]["available"]
    assert ss.propose(rctx, "rkt")[0]["kind"] == "launch"
