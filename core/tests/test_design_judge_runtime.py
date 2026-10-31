"""Plan 9 G-B fix round 1: the design judge as a fresh sub-agent on the run's own runtime
(review I6), and the optional classifier model off the critical path, bounded and billed
(review M4)."""
import asyncio
import time

import pytest

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta, Usage
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)


def _turn(text):
    return [TextDelta(text=text), Done(stop_reason="end_turn")]


async def test_judge_subagent_is_a_fresh_tool_less_child_with_the_renders(tmp_path):
    fake = FakeProvider([_turn("ok"), _turn('{"score": 0.8, "issues": []}')])
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "fake-model"))
    lead = await rt.start_run("engineer", "a humanoid robot", role="lead")
    await rt.wait(lead)
    out = await rt.judge_subagent(lead, "RUBRIC SYSTEM", "REQUEST TEXT",
                                  [("image/png", "iVBORw0KGgo=")])
    assert out == '{"score": 0.8, "issues": []}'
    child = [r for r in rt.store.list_runs(50) if r.parent_id == lead][0]
    assert child.title == "Design judge" and child.role == "sub" and child.agent == "verifier"
    assert rt.store.current_policy(child.id).allowed_classes == []
    req = fake.requests[-1]
    assert req.tools == []                                  # a judge cannot act
    first = req.messages[0].content[0].text
    assert "RUBRIC SYSTEM" in first and "REQUEST TEXT" in first
    assert len(req.messages) == 1                           # a fresh context: no history
    await rt.aclose()


async def test_judge_runs_under_a_parallel_limit_of_one(tmp_path, monkeypatch):
    """Re-review N2: the parent holds the only slot while it critiques; its judge must not
    queue behind it (the parent yields its slot while it waits, like any sub-agent wait)."""
    fake = FakeProvider([_turn('{"score": 0.7, "issues": []}')])
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "fake-model"))
    monkeypatch.setattr(rt, "_total_limit", lambda prof=None: 1)
    monkeypatch.setattr(rt, "JUDGE_TIMEOUT_S", 10.0)
    parent = rt.create_external_run("designer", provider="fake", model="fake-model",
                                    slot_ref="fake/fake-model")
    assert rt.parallel.active() == {parent}               # the parent holds the only slot
    out = await asyncio.wait_for(rt.judge_subagent(parent, "S", "T", []), 15)
    assert out == '{"score": 0.7, "issues": []}'
    assert rt.parallel.active() == {parent}               # and takes it back afterwards
    await rt.aclose()


async def test_judge_subagent_failure_returns_none(tmp_path):
    fake = FakeProvider([_turn("ok")])                      # no turn left for the judge
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "fake-model"))
    lead = await rt.start_run("designer", "a humanoid robot")
    await rt.wait(lead)
    assert await rt.judge_subagent(lead, "S", "T", []) is None
    assert await rt.judge_subagent("no-such-run", "S", "T", []) is None
    await rt.aclose()


class _SlowRubric:
    name = "slow"

    def __init__(self, delay):
        self.delay, self.requests = delay, []

    async def stream(self, req):
        self.requests.append(req)
        await asyncio.sleep(self.delay)
        yield Usage(input_tokens=40, output_tokens=5, usd=0.001)
        yield TextDelta(text='{"score": 0.95}')
        yield Done(stop_reason="end_turn")


def _with_rubric(tmp_path, rubric):
    fake = FakeProvider([_turn("ok") for _ in range(4)])
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (
        (rubric, "haiku") if "haiku" in ref else (fake, "fake-model")))
    prof = rt.profile().model_copy(update={"design_classifier_model":
                                           "anthropic/claude-haiku-5-5"})
    rt.profile = lambda: prof
    return rt, fake


async def test_classifier_model_is_off_the_critical_path_and_bounded(tmp_path):
    rt, fake = _with_rubric(tmp_path, _SlowRubric(30.0))
    t0 = time.monotonic()
    rid = await rt.start_run("designer", "a desk organiser")
    assert time.monotonic() - t0 < 1.5                      # start does not wait for it
    await rt.wait(rid)
    await asyncio.sleep(2.5)                                # past the 2 s bound
    dc = rt.store.get_run(rid).design_class
    assert dc["source"] == "prior" and dc["cls"] == "engineering"
    await rt.aclose()


async def test_classifier_model_is_billed_and_skipped_for_sub_agents(tmp_path):
    rubric = _SlowRubric(0.0)
    rt, fake = _with_rubric(tmp_path, rubric)
    rid = await rt.start_run("designer", "a desk organiser", role="lead")
    await rt.wait(rid)
    for _ in range(50):
        if rt.store.get_run(rid).design_class.get("source") == "prior+model":
            break
        await asyncio.sleep(0.05)
    run = rt.store.get_run(rid)
    assert run.design_class["cls"] == "design" and run.input_tokens >= 40
    sub = await rt.start_run("designer", "a bracket", parent_id=rid, role="sub", title="x")
    await rt.wait(sub)
    await asyncio.sleep(0.2)
    assert len(rubric.requests) == 1                        # the sub-agent inherits, unrated
    await rt.aclose()
