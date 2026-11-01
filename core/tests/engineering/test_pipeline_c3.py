"""The engineering pipeline record and the lead's finish check (Plan 10 C3)."""
from types import SimpleNamespace

from mises.agent.definitions import load_definitions
from mises.assembly.model import AssemblyStore
from mises.engineering import pipeline
from mises.orchestrator.lead import LEAD_PROMPT, LEAD_TOOLS
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
        assert name in LEAD_PROMPT
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
