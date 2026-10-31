"""Plan 9 G-B: critique_model for design and mixed builds (research §6 rubric, §8 G1 row).

- the build's design class sets rounds (lowered, never raised past the profile), views and
  image size (hero / side / front at 1024 px), and the rubric weights;
- a design build is judged in a separate context when a vision model is available, even if
  the builder can see images; the judge gets checkable questions and the fixes already tried;
- the round's score is the class-weighted mean of the checkable-question answers;
- a part that reads as a straight bar auto-fails the round;
- an issue that survives a round, or any serious issue from round 3 on, asks for a strategy
  switch; engineering builds keep the Plan 8 behaviour.
"""
from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

import pytest

from mises import design_intensity as di
from mises.cad import views
from mises.events import EventBus
from mises.providers.base import TextDelta
from mises.providers.fake import FakeProvider
from mises.store import Store
from mises.tools.registry import ToolContext

from .test_views import _BAD_REPORT, _make_part

_BAR_METRICS = {"bbox": {"size": [200.0, 20.0, 20.0]},
                "face_list": [{"type": "plane", "area": 4000.0}] * 4
                + [{"type": "plane", "area": 400.0}] * 2, "faces": 6}


@pytest.fixture(autouse=True)
def _clear_rounds():
    views._runs.clear()
    yield
    views._runs.clear()


def _ctx(tmp_path, prompt: str | None, rt, *, parent: bool = False):
    store = Store(tmp_path / "db.sqlite")
    run = store.create_run("designer", "fake", "fake/model", "t")
    if prompt is not None:
        target = run
        if parent:        # the class lives on the lead; the critique runs in its sub-agent
            target = run
            run = store.create_run("designer", "fake", "fake/model", "t", parent_id=target.id)
        store.set_design_class(target.id, di.design_intensity(prompt))
    return store, ToolContext(run_id=run.id, user="local", store=store, events=EventBus(),
                              workdir=tmp_path, policy=None, call_id="c1",
                              services={"runtime": rt})


def _rt(tmp_path, *, sees: bool, turns=None, rounds: int = 6):
    provider = FakeProvider(turns or [])
    rt = SimpleNamespace(home=tmp_path, run_sees_images=lambda rid: sees,
                         vision_model_for=lambda rid: (provider, "fake/judge", None))
    rt.profile = lambda: SimpleNamespace(critique_max_rounds=rounds, critique_time_budget_s=300.0,
                                         critique_max_image_tokens=100_000)
    return rt, provider


def _judge(score=0.4, checks=None, issues=None) -> str:
    return json.dumps({"score": score, "issues": issues or [], "checks": checks or []})


def _size(img) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(img.data_b64))) as im:
        return im.size


async def test_design_build_uses_a_separate_judge_with_the_rubric(tmp_path):
    _make_part(tmp_path, "cube")
    checks = [{"id": "silhouette", "score": 3}, {"id": "proportion", "score": 3},
              {"id": "zebra", "score": 0}, {"id": "no_bars", "score": 3},
              {"id": "function", "score": 3}, {"id": "dfm", "score": 3}]
    rt, judge = _rt(tmp_path, sees=True, turns=[[TextDelta(text=_judge(0.9, checks))]])
    store, ctx = _ctx(tmp_path, "a sleek humanoid robot", rt, parent=True)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube",
                                                                     request="humanoid"))
    assert out.data["mode"] == "vision_model" and out.data["design_class"] == "design"
    req = judge.requests[0]
    assert "checkable question" in req.system and "no_bars" in req.system
    # images come first, then the text that asks about them; hero views at 1024 px
    blocks = req.messages[0].content
    assert type(blocks[-1]).__name__ == "TextBlock"
    assert all(type(b).__name__ == "ImageBlock" for b in blocks[:-1]) and len(blocks) == 4
    # the score is the weighted rubric: surface 0.2 weight scored 0 -> below the judge's 0.9
    w = di.KNOBS["design"]["critique"]["weights"]
    expect = (w["silhouette"] + w["proportion"] + w["details"] + w["function"] + w["dfm"]) / (
        sum(w.values()))
    assert out.data["score"] == pytest.approx(expect, abs=1e-3)
    assert out.data["judge_score"] == pytest.approx(0.9)
    assert out.data["untrusted_critique"]["checks"][2] == {"id": "zebra", "score": 0, "note": ""}
    assert out.data["max_rounds"] == 4          # design: 4 rounds (profile allows 6)
    assert out.data["weights"] == w


async def test_design_views_are_hero_side_front_at_1024(tmp_path):
    _make_part(tmp_path, "cube")
    rt, _ = _rt(tmp_path, sees=True)
    rt.vision_model_for = lambda rid: (None, None, "none configured")
    store, ctx = _ctx(tmp_path, "a robotic dog", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="dog"))
    assert out.data["mode"] == "self"            # no separate judge: the builder looks itself
    assert [i.label for i in out.images] == ["iso", "side", "front"]
    assert all(max(_size(i)) == 1024 for i in out.images)
    assert {c["id"] for c in out.data["checks"]} >= {"silhouette", "no_bars", "zebra"}
    assert "checks" in out.data["report_format"]


async def test_self_report_checks_drive_the_score(tmp_path):
    _make_part(tmp_path, "cube")
    rt, _ = _rt(tmp_path, sees=True)
    rt.vision_model_for = lambda rid: (None, None, "none")
    store, ctx = _ctx(tmp_path, "a car body", rt)
    args = dict(part_id="cube", request="car")
    await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    rep = views.CritiqueReport(score=0.95, issues=[], checks=[
        {"id": "silhouette", "score": 1}, {"id": "proportion", "score": 1}])
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=rep))
    assert out.data["score"] == pytest.approx(1 / 3, abs=1e-3)
    assert out.data["done"] and out.data["stop_reason"] == "no_high_severity"


async def test_engineering_build_keeps_plan8_behaviour(tmp_path):
    _make_part(tmp_path, "cube")
    rt, judge = _rt(tmp_path, sees=False, turns=[[TextDelta(text=_judge(0.3))]] * 3)
    store, ctx = _ctx(tmp_path, "injection mold for a cap", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="x"))
    assert judge.requests[0].system == views.CRITIQUE_SYSTEM
    assert "design_class" not in out.data and "weights" not in out.data
    assert out.data["max_rounds"] == 2            # engineering: 2 rounds


async def test_class_rounds_never_raise_the_profile_ceiling(tmp_path):
    _make_part(tmp_path, "cube")
    rt, _ = _rt(tmp_path, sees=True, rounds=3)
    rt.vision_model_for = lambda rid: (None, None, "none")
    store, ctx = _ctx(tmp_path, "a humanoid robot", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="h"))
    assert out.data["max_rounds"] == 3


async def test_a_straight_bar_auto_fails_a_design_round(tmp_path):
    _make_part(tmp_path, "cube")
    (tmp_path / "parts" / "cube" / "metrics.json").write_text(json.dumps(_BAR_METRICS))
    rt, _ = _rt(tmp_path, sees=False, turns=[[TextDelta(text=_judge(0.97))]])
    store, ctx = _ctx(tmp_path, "a humanoid robot", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="h"))
    assert out.data["score"] == 0.5 and out.data["bars"] == ["cube"]
    open_ = out.data["untrusted_critique"]["issues_open"]
    assert open_[-1]["severity"] == "high" and "straight bar" in open_[-1]["issue"]
    assert out.data["done"] is False


async def test_bars_do_not_fail_an_engineering_round(tmp_path):
    _make_part(tmp_path, "cube")
    (tmp_path / "parts" / "cube" / "metrics.json").write_text(json.dumps(_BAR_METRICS))
    rt, _ = _rt(tmp_path, sees=False, turns=[[TextDelta(text=_judge(0.97))]])
    store, ctx = _ctx(tmp_path, "a bracket", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="b"))
    assert out.data["score"] == pytest.approx(0.97) and out.data["done"]


async def test_repeated_issue_switches_strategy_and_feeds_tried_fixes_to_the_judge(tmp_path):
    _make_part(tmp_path, "cube")
    issue = [{"view": "iso", "issue": "the chest is lumpy", "severity": "high",
              "suggested_fix": "add a 5 mm fillet"}]
    rt, judge = _rt(tmp_path, sees=False,
                    turns=[[TextDelta(text=_judge(0.4, issues=issue))] for _ in range(3)])
    store, ctx = _ctx(tmp_path, "a humanoid robot", rt)
    args = views.CritiqueArgs(part_id="cube", request="h")
    r1 = await views.critique_model.handler(ctx, args)
    assert r1.data["strategy_switch"] is False and r1.data["repeated"] == []
    r2 = await views.critique_model.handler(ctx, args)
    assert r2.data["strategy_switch"] is True and len(r2.data["repeated"]) == 1
    assert "Switch strategy" in r2.summary and r2.data["tried_fixes"] == ["add a 5 mm fillet"]
    assert "add a 5 mm fillet" in judge.requests[1].system      # do not repeat a failed fix


async def test_design_round_that_passes_recommends_a_final_review(tmp_path):
    _make_part(tmp_path, "cube")
    rt, _ = _rt(tmp_path, sees=False, turns=[[TextDelta(text=_judge(0.95))]])
    store, ctx = _ctx(tmp_path, "a humanoid robot", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="h"))
    assert out.data["done"] and out.data["final_review"] == "fable-5.1 high"
    assert "final design review" in out.summary


async def test_mixed_build_gets_three_views_at_768(tmp_path):
    _make_part(tmp_path, "cube")
    rt, judge = _rt(tmp_path, sees=False, turns=[[TextDelta(text=_judge(0.95))]])
    store, ctx = _ctx(tmp_path, "jet engine compressor", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="j"))
    assert out.data["design_class"] == "mixed" and out.data["max_rounds"] == 3
    imgs = [b for b in judge.requests[0].messages[0].content if type(b).__name__ == "ImageBlock"]
    assert len(imgs) == 3 and all(max(_size(i)) == 768 for i in imgs)


async def test_zebra_view_is_requested_when_the_renderer_has_styles(tmp_path, monkeypatch):
    """G-R's renderer takes style=; until then the zebra render is simply not requested. A
    renderer that refuses the style falls back to the shaded views."""
    _make_part(tmp_path, "cube")
    asked = []
    real = views._render

    async def spy(ctx, meshes, *, renders=(), masks=()):
        asked.append([r.get("style") for r in renders])
        if any(r.get("style") for r in renders):
            raise views.MisesError(views.ErrorCode.TOOL_FAILED, "unknown style 'zebra'")
        return await real(ctx, meshes, renders=renders, masks=masks)
    monkeypatch.setattr(views, "_render", spy)
    monkeypatch.setattr(views, "_renderer_styles", lambda: True)
    rt, _ = _rt(tmp_path, sees=True)
    rt.vision_model_for = lambda rid: (None, None, "none")
    store, ctx = _ctx(tmp_path, "a humanoid robot", rt)
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="h"))
    assert asked == [[None, "zebra"], [None]]
    assert [i.label for i in out.images] == ["iso", "side", "front"]


async def test_bad_report_shape_still_works_for_design(tmp_path):
    _make_part(tmp_path, "cube")
    rt, _ = _rt(tmp_path, sees=True)
    rt.vision_model_for = lambda rid: (None, None, "none")
    store, ctx = _ctx(tmp_path, "a humanoid robot", rt)
    args = dict(part_id="cube", request="h")
    await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=_BAD_REPORT))
    assert out.data["score"] == pytest.approx(0.3) and "checks" not in out.data
    assert out.data["done"] is False
