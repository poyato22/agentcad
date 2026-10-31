"""Plan 9 D27 wiring: a run is classified when it starts, the class rides on the run payload,
the lead / designer prompt carries the surfacing rules only for design and mixed builds, a
sub-agent inherits its lead's class, a follow-up message can only raise it, and the optional
cheap-model rubric can raise it but never fail a run."""
import pytest

import mises.runtime as runtime_mod
from mises import design_intensity as di
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)


def make(tmp_path, n=4, text="ok"):
    fake = FakeProvider([[TextDelta(text=text), Done(stop_reason="end_turn")] for _ in range(n)])
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "fake-model"))
    return rt, fake


@pytest.mark.parametrize("prompt, cls", [
    ("a humanoid robot", "design"), ("build me a robotic dog", "design"),
    ("design a car body", "design"), ("injection mold for a cap", "engineering"),
    ("an L bracket with four M3 holes", "engineering"), ("jet engine compressor", "mixed")])
async def test_start_run_classifies_and_stores_the_design_class(tmp_path, prompt, cls):
    rt, fake = make(tmp_path)
    rid = await rt.start_run("designer", prompt)
    await rt.wait(rid)
    run = rt.store.get_run(rid)
    assert run.design_class["cls"] == cls
    assert run.model_dump()["design_class"]["score"] == run.design_class["score"]
    system = fake.requests[0].system
    if cls == "engineering":
        assert di.SURFACING_HEADING not in system and di.SECTION_BEGIN not in system
    else:
        assert di.SURFACING_HEADING in system and "Never a straight bar" in system
    await rt.aclose()


async def test_only_lead_designer_and_engineer_get_the_block(tmp_path):
    rt, fake = make(tmp_path)
    rid = await rt.start_run("verifier", "check the humanoid robot covers")
    await rt.wait(rid)
    assert rt.store.get_run(rid).design_class["cls"] == "design"
    assert di.SECTION_BEGIN not in fake.requests[0].system
    await rt.aclose()


async def test_sub_agent_inherits_its_leads_class(tmp_path):
    rt, fake = make(tmp_path)
    lead = await rt.start_run("engineer", "a sleek humanoid robot", role="lead")
    await rt.wait(lead)
    sub = await rt.start_run("designer", "design the hip servo bracket", parent_id=lead,
                             role="sub", title="Hip bracket")
    await rt.wait(sub)
    dc = rt.store.get_run(sub).design_class
    assert dc["cls"] == "design"
    assert di.SURFACING_HEADING in fake.requests[-1].system
    await rt.aclose()


async def test_follow_up_message_raises_but_never_lowers(tmp_path):
    rt, fake = make(tmp_path, n=6)
    rid = await rt.start_run("designer", "a bracket for a camera", role="lead")
    await rt.wait(rid)
    assert rt.store.get_run(rid).design_class["cls"] == "engineering"
    await rt.send(rid, "now turn it into a sleek humanoid robot head shell")
    await rt.wait(rid)
    assert rt.store.get_run(rid).design_class["cls"] == "design"
    assert di.SURFACING_HEADING in fake.requests[-1].system
    await rt.send(rid, "add an M3 hole with 0.1 mm tolerance")
    await rt.wait(rid)
    assert rt.store.get_run(rid).design_class["cls"] == "design"
    await rt.aclose()


async def test_model_rubric_can_raise_the_class(tmp_path):
    rt, fake = make(tmp_path)
    rubric = FakeProvider([[TextDelta(text='{"score": 0.9}'), Done(stop_reason="end_turn")]])
    rt.provider_resolver = lambda ref: ((rubric, "haiku") if "haiku" in ref
                                        else (fake, "fake-model"))
    prof = rt.profile().model_copy(update={"design_classifier_model": "anthropic/claude-haiku-5-5"})
    rt.profile = lambda: prof
    rid = await rt.start_run("designer", "a desk organiser")
    await rt.wait(rid)
    dc = rt.store.get_run(rid).design_class
    assert dc["cls"] == "design" and dc["source"] == "prior+model"
    assert "design-heavy" in rubric.requests[0].system
    await rt.aclose()


async def test_model_rubric_failure_keeps_the_prior(tmp_path):
    rt, fake = make(tmp_path)

    def resolver(ref):
        if "haiku" in ref:
            raise RuntimeError("no key")
        return fake, "fake-model"
    rt.provider_resolver = resolver
    prof = rt.profile().model_copy(update={"design_classifier_model": "anthropic/claude-haiku-5-5"})
    rt.profile = lambda: prof
    rid = await rt.start_run("designer", "injection mold for a cap")
    r = await rt.wait(rid)
    assert r.stop_reason == "end_turn"
    assert rt.store.get_run(rid).design_class == di.design_intensity("injection mold for a cap")
    await rt.aclose()


def test_claude_code_rules_file_carries_the_block(tmp_path):
    """The Claude Code session's appended system prompt gets the same block (via rules_for)."""
    from mises.agent.definitions import load_definitions
    defs = load_definitions()
    lead = di.rules_for(defs["engineer"], "lead", di.design_intensity("a car body"))
    assert di.SURFACING_HEADING in lead.body
    plain = di.rules_for(defs["sim"], "sub", di.design_intensity("a car body"))
    assert plain.body == defs["sim"].body
    again = di.rules_for(lead, "lead", di.design_intensity("a car body"))
    assert again.body.count(di.SECTION_BEGIN) == 1
