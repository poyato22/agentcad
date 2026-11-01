"""FWC M4: the engineering pipeline appears once in a lead's prompt (the engineer's own section
or LEAD_ENGINEERING, never both). FWC M5: the turn-end engineering warning is not repeated on
turns where the pipeline did not change."""
from mises.agent.definitions import load_definitions
from mises.engineering import pipeline
from mises.orchestrator import lead as lead_mod
from mises.runtime import Runtime
from mises.types import StepKind

from dyn import quad  # tests/dyn (tests/ is on sys.path)


def _defs():
    return load_definitions()


def test_the_engineer_lead_carries_one_engineering_section():
    eng = lead_mod.lead_definition(_defs()["engineer"]).body
    assert "Engineering checks (anything with moving parts)" not in eng
    assert eng.count("Engineering checks: practical engineering first") == 1


def test_a_lead_without_its_own_section_gets_lead_engineering():
    body = lead_mod.lead_definition(_defs()["designer"]).body
    assert body.count("Engineering checks (anything with moving parts)") == 1
    for tool in lead_mod.ENGINEERING_MARKERS:
        assert tool in body


def _lead(tmp_path):
    rt = Runtime(tmp_path / "home")
    quad.write_parts(rt.home)
    project = rt.store.ensure_default_project().id
    quad.assembly(project, rt.assembly_store)
    run = rt.store.create_run("engineer", "fake", "m", "a quadruped")
    rt.store.update_run(run.id, project_id=project, role="lead")
    return rt, project, run.id


def _warnings(rt, rid):
    return [s for s in rt.store.steps(rid, [StepKind.VERIFY])
            if s.payload.get("kind") == "engineering_checks"]


def test_an_unchanged_pipeline_warns_once(tmp_path):
    rt, project, rid = _lead(tmp_path)
    rt._finish_checks(rid)
    rt._finish_checks(rid)                       # a Q&A turn that changed nothing
    assert len(_warnings(rt, rid)) == 1


def test_a_changed_pipeline_warns_again(tmp_path):
    rt, project, rid = _lead(tmp_path)
    rt._finish_checks(rid)
    asm = rt.assembly_store.get(project)
    pipeline.record(rt.home, project, "dyn_stability", status="fail", summary="tips over",
                    sig=pipeline.signature(asm, rt.home, store=rt.store, project_id=project))
    rt._finish_checks(rid)
    assert len(_warnings(rt, rid)) == 2


def test_a_regression_after_a_clear_turn_warns_again(tmp_path, monkeypatch):
    rt, project, rid = _lead(tmp_path)
    rt._finish_checks(rid)
    real = pipeline.finish_report
    monkeypatch.setattr(pipeline, "finish_report", lambda *a: None)     # clear
    rt._finish_checks(rid)
    monkeypatch.setattr(pipeline, "finish_report", real)                # the same gap again
    rt._finish_checks(rid)
    assert len(_warnings(rt, rid)) == 2
