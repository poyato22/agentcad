"""The engineering pipeline record and the lead's finish check (Plan 10 C3)."""
from types import SimpleNamespace

from mises.agent.definitions import load_definitions
from mises.assembly.model import AssemblyStore
from mises.engineering import pipeline
from mises.orchestrator.lead import LEAD_ENGINEERING, LEAD_PROMPT, LEAD_TOOLS
from mises.runtime import Runtime
from mises.types import StepKind

from dyn import quad  # tests/dyn (tests/ is on sys.path)


def test_report_requires_the_done_checks_for_moving_builds(tmp_path):
    quad.write_parts(tmp_path)
    asm = quad.assembly()
    rep = pipeline.report(tmp_path, "quad", asm)
    assert set(rep["missing"]) == set(pipeline.ALL)
    sig = pipeline.signature(asm, tmp_path)
    for c in pipeline.ALL:
        pipeline.record(tmp_path, "quad", c, status="pass", summary="ok", sig=sig)
    rep = pipeline.report(tmp_path, "quad", asm)
    assert rep["missing"] == [] and rep["overall"] == "pass"
    assert pipeline.done_text(rep) is None
    pipeline.record(tmp_path, "quad", "dyn_to_fem", status="fail", summary="SF 0.8", sig=sig)
    rep = pipeline.report(tmp_path, "quad", asm)
    assert rep["failing"] == ["dyn_to_fem"] and "failing: dyn_to_fem" in pipeline.done_text(rep)


def test_a_required_check_that_judged_nothing_is_not_clear(tmp_path):
    """Review I1: not_checked on a required check warns with its reason; n/a is clear."""
    quad.write_parts(tmp_path)
    asm = quad.assembly()
    sig = pipeline.signature(asm, tmp_path, project_id="quad")
    for c in pipeline.ALL:
        pipeline.record(tmp_path, "quad", c, status="pass", summary="ok", sig=sig)
    pipeline.record(tmp_path, "quad", "dyn_torque", status="not_checked", sig=sig,
                    summary="no actuator assigned to moving joint(s) fl_knee: assign actuators")
    rep = pipeline.report(tmp_path, "quad", asm)
    assert [u["check"] for u in rep["unchecked"]] == ["dyn_torque"]
    text = pipeline.done_text(rep)
    assert text and "could not be evaluated" in text and "assign actuators" in text
    pipeline.record(tmp_path, "quad", "dyn_torque", status="pass", summary="ok", sig=sig)
    pipeline.record(tmp_path, "quad", "dyn_stability", status="n/a", sig=sig,
                    summary="not applicable: fixed base")
    assert pipeline.done_text(pipeline.report(tmp_path, "quad", asm)) is None


async def test_dyn_torque_without_actuators_does_not_pass(tmp_path):
    from types import SimpleNamespace as NS

    from mises.dyn import tools as dt
    quad.write_parts(tmp_path)
    store = AssemblyStore(tmp_path / "a.db")
    quad.assembly("quad", store)
    ctx = NS(workdir=tmp_path, run_id=None, events=None, services={"assembly": store},
             call_id="c1", store=None, agent_call=False)
    out = await dt.dyn_torque.handler(ctx, dt.TorqueArgs(project_id="quad"))
    assert out.data["status"] == "not_checked" and "no actuator" in out.summary
    rec = pipeline.load(tmp_path, "quad")["dyn_torque"]
    assert rec["status"] == "not_checked"


def test_a_running_dyn_to_fem_is_still_running_not_missing(tmp_path):
    quad.write_parts(tmp_path)
    rep = pipeline.report(tmp_path, "quad", quad.assembly(), running={"dyn_to_fem"})
    assert "dyn_to_fem" in rep["running"] and "dyn_to_fem" not in rep["missing"]
    assert "still running: dyn_to_fem" in pipeline.done_text(rep)


def test_signature_follows_actuators_materials_and_couplings(tmp_path):
    quad.write_parts(tmp_path)
    asm = quad.assembly()
    base = pipeline.signature(asm, tmp_path, project_id="quad")
    pipeline.note_actuators(tmp_path, "quad", {"fl_knee": "mg996r"})
    s1 = pipeline.signature(asm, tmp_path, project_id="quad")
    assert s1 != base
    j = next(x for x in asm.joints if x.id == "fl_knee")
    object.__setattr__(j, "coupling", {"kind": "belt", "ratio": 2.0, "partner_joint": "fl_hip"})
    s2 = pipeline.signature(asm, tmp_path, project_id="quad")
    assert s2 != s1

    class _Store:
        def __init__(self, mat):
            self.mat = mat

        def get_part(self, pid):
            return NS2(material=self.mat)
    from types import SimpleNamespace as NS2
    a = pipeline.signature(asm, tmp_path, store=_Store("PLA"), project_id="quad")
    b = pipeline.signature(asm, tmp_path, store=_Store("PETG"), project_id="quad")
    assert a != b


def test_a_static_product_needs_no_dynamics(tmp_path):
    quad.write_parts(tmp_path)
    asm = quad.assembly()
    for j in asm.joints:
        j.kind = "fixed"
    rep = pipeline.report(tmp_path, "quad", asm)
    assert rep["moving_joints"] is False
    assert not set(pipeline.DONE) & set(rep["missing"])


def test_finish_check_warns_a_lead_with_missing_checks(tmp_path):
    rt = Runtime(tmp_path / "home")
    quad.write_parts(rt.home)
    project = rt.store.ensure_default_project().id
    quad.assembly(project, rt.assembly_store)
    run = rt.store.create_run("engineer", "fake", "m", "a quadruped")
    rt.store.update_run(run.id, project_id=project, role="lead")
    text = pipeline.finish_warning(rt, run.id)
    assert text and "dyn_torque" in text and "not run" in text
    steps = rt.store.steps(run.id, [StepKind.VERIFY])
    assert steps and steps[-1].payload["kind"] == "engineering_checks"
    # a sub-agent's turn is never warned
    sub = rt.store.create_run("designer", "fake", "m", "a leg")
    rt.store.update_run(sub.id, project_id=project, role="sub")
    assert pipeline.finish_warning(rt, sub.id) is None


def test_prompts_and_tools_carry_the_pipeline():
    for name in ("actuator_size", "electronics_estimate", "power_budget", "dyn_torque",
                 "dyn_stability", "dyn_rom_sweep", "dyn_to_fem", "engineering_checks"):
        assert name in LEAD_TOOLS
        assert name in LEAD_PROMPT + LEAD_ENGINEERING     # FWC M4: the block moved
    eng = load_definitions([])["engineer"]
    assert "BEGIN p10-c3" in eng.body and "dyn_to_fem" in eng.body
    sim = load_definitions([])["simulation"]
    assert "dyn_to_fem" in sim.tools


def test_registry_has_the_c3_tools(tmp_path):
    rt = Runtime(tmp_path / "home")
    names = {t.name for t in rt.registry.list()}
    assert {"dyn_build", "dyn_torque", "dyn_stability", "dyn_reactions", "dyn_rom_sweep",
            "dyn_to_fem", "actuator_size", "electronics_estimate", "power_budget",
            "thermal_estimate", "cable_route", "engineering_checks"} <= names


async def test_spec_get_reports_the_engineering_checks(tmp_path):
    from mises.engineering.tools import ProjectArg, spec_get
    quad.write_parts(tmp_path)
    store = AssemblyStore(tmp_path / "a.db")
    quad.assembly("quad", store)

    class _Store:
        def get_spec(self, pid):
            return None

        def get_run(self, rid):
            raise KeyError(rid)
    ctx = SimpleNamespace(workdir=tmp_path, run_id="r1", store=_Store(), events=None,
                          services={"assembly": store})
    out = await spec_get.handler(ctx, ProjectArg(project_id="quad"))
    ec = out.data["engineering_checks"]
    assert ec["overall"] == "missing" and "dyn_torque" in ec["missing"]
