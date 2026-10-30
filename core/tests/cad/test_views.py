"""render_views / critique_model / present_view tools (G1 / G-R7). No real build needed: a
model.stl is written directly, and the vision model is a scripted fake."""
from __future__ import annotations

import io
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from mises.cad import views
from mises.events import EventBus, Event
from mises.providers.base import TextDelta
from mises.providers.fake import FakeProvider
from mises.store import Store
from mises.tools.registry import ToolContext
from mises.types import ErrorCode, MisesError, SafetyClass


def _cube_stl() -> bytes:
    # unit cube 20mm, 12 triangles, binary STL
    s = 10.0
    v = {0: (-s, -s, -s), 1: (s, -s, -s), 2: (s, s, -s), 3: (-s, s, -s),
         4: (-s, -s, s), 5: (s, -s, s), 6: (s, s, s), 7: (-s, s, s)}
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    tris = []
    for a, b, c, d in quads:
        tris += [(v[a], v[b], v[c]), (v[a], v[c], v[d])]
    out = bytearray(b"\0" * 80) + struct.pack("<I", len(tris))
    for tri in tris:
        out += struct.pack("<3f", 0, 0, 0)
        for vert in tri:
            out += struct.pack("<3f", *vert)
        out += struct.pack("<H", 0)
    return bytes(out)


def _make_part(workdir, part_id: str) -> None:
    d = workdir / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.stl").write_bytes(_cube_stl())
    (d / "metrics.json").write_text("{}")


def _png_size(data: bytes):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


@pytest.fixture(autouse=True)
def _clear_rounds():
    views._runs.clear()
    yield
    views._runs.clear()


def _bare_ctx(tmp_path, **kw):
    base = dict(run_id=None, user="local", store=None, events=EventBus(),
                workdir=tmp_path, policy=None, call_id="call1", services={})
    base.update(kw)
    return SimpleNamespace(**base)


# ---- render_views

async def test_render_views_four_pngs_and_section(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path)
    out = await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="cube", size=256))
    assert out.ok
    assert set(out.data["views"]) == {"front", "top", "side", "iso"}
    from pathlib import Path
    for h in out.handles:
        assert Path(h).exists()
        assert _png_size(Path(h).read_bytes()) == (256, 256)
    sec = await views.render_views.handler(ctx, views.RenderViewsArgs(
        part_id="cube", views=["front"], size=200,
        section=views.SectionSpec(origin=[0, 0, 0], normal=[0, -1, 0])))
    assert sec.ok and "section" in sec.summary


async def test_render_views_unknown_part(tmp_path):
    ctx = _bare_ctx(tmp_path)
    with pytest.raises(MisesError) as e:
        await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="ghost"))
    assert e.value.code == ErrorCode.NOT_FOUND


# ---- critique_model

_GOOD = '{"score": 0.92, "issues": []}'
_BAD = ('{"score": 0.3, "issues": [{"view": "front", "issue": "flat where it should be curved",'
        ' "severity": "high", "suggested_fix": "loft the body"}]}')


def _fake_rt(tmp_path, turns):
    provider = FakeProvider(turns)      # one provider, so turns are popped across rounds
    return SimpleNamespace(home=tmp_path,
                           vision_model_for=lambda rid: (provider, "fake/model", None))


async def test_critique_stops_when_score_passes(tmp_path):
    _make_part(tmp_path, "cube")
    turns = [[TextDelta(text=_BAD)], [TextDelta(text=_GOOD)]]
    rt = _fake_rt(tmp_path, turns)
    ctx = _bare_ctx(tmp_path, run_id="r1", services={"runtime": rt})
    r1 = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="a wing"))
    assert r1.data["available"] and r1.data["round"] == 1
    assert r1.data["done"] is False and r1.data["score"] == pytest.approx(0.3)
    # a separate model's critique text is labelled untrusted, never top-level
    crit = r1.data["untrusted_critique"]
    assert crit["issues_open"][0]["severity"] == "high" and "not instructions" in crit["note"]
    assert "issues_open" not in r1.data and "issues" not in r1.data
    r2 = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="a wing"))
    assert r2.data["round"] == 2 and r2.data["done"] is True
    assert r2.data["stop_reason"] == "score_threshold"


async def test_critique_budget_caps_rounds(tmp_path):
    _make_part(tmp_path, "cube")
    turns = [[TextDelta(text=_BAD)] for _ in range(5)]
    rt = _fake_rt(tmp_path, turns)
    ctx = _bare_ctx(tmp_path, run_id="r2", services={"runtime": rt})
    done = None
    for _ in range(3):
        out = await views.critique_model.handler(
            ctx, views.CritiqueArgs(part_id="cube", request="a wing", max_rounds=3))
        done = out.data
    assert done["round"] == 3 and done["done"] is True and done["stop_reason"] == "max_rounds"


async def test_critique_reports_iou_against_reference(tmp_path):
    from mises import references as refs
    from mises.references import PhotoIn, ReferenceUpload
    _make_part(tmp_path, "cube")
    import base64
    from PIL import Image
    im = Image.new("RGB", (64, 64), (255, 255, 255))
    im.paste((20, 20, 20), (12, 12, 52, 52))                      # a square silhouette
    buf = io.BytesIO()
    im.save(buf, "PNG")
    saved = refs.save_reference(tmp_path, ReferenceUpload(
        image=PhotoIn(data_b64=base64.b64encode(buf.getvalue()).decode())))
    rt = _fake_rt(tmp_path, [[TextDelta(text=_BAD)]])
    ctx = _bare_ctx(tmp_path, run_id="rio", services={"runtime": rt})
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="a wing", reference_image_ids=[saved["reference_id"]]))
    # an untagged reference is scored against its single best view only
    assert len(out.data["iou"]) == 1
    (entry,) = out.data["reference_scores"]
    assert entry["note"] == "untagged reference" and entry["tagged"] is False
    assert entry["iou"] > 0.9                                       # a cube face is a square


async def test_critique_no_high_severity_stops(tmp_path):
    _make_part(tmp_path, "cube")
    low = '{"score": 0.5, "issues": [{"view": "top", "issue": "minor", "severity": "low"}]}'
    rt = _fake_rt(tmp_path, [[TextDelta(text=low)]])
    ctx = _bare_ctx(tmp_path, run_id="r3", services={"runtime": rt})
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="x"))
    assert out.data["done"] is True and out.data["stop_reason"] == "no_high_severity"


async def test_critique_unavailable_without_vision(tmp_path):
    _make_part(tmp_path, "cube")
    rt = SimpleNamespace(home=tmp_path,
                         vision_model_for=lambda rid: (None, None, "no vision model"))
    ctx = _bare_ctx(tmp_path, run_id="r4", services={"runtime": rt})
    out = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="x"))
    assert out.ok and out.data["available"] is False
    assert "no vision model" in out.data["reason"]


async def test_critique_emits_round_card(tmp_path):
    _make_part(tmp_path, "cube")
    bus = EventBus()
    seen: list[Event] = []
    sub = bus.subscribe("r5")
    rt = _fake_rt(tmp_path, [[TextDelta(text=_BAD)]])
    ctx = _bare_ctx(tmp_path, run_id="r5", events=bus, services={"runtime": rt})
    await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="x"))
    # drain
    while not sub.queue.empty():
        seen.append(sub.queue.get_nowait())
    cards = [e for e in seen if e.type == "agent_announce" and e.data["kind"] == "critique_round"]
    assert len(cards) == 1
    card = cards[0].data
    assert set(card) == {"run_id", "kind", "title", "detail", "ref"}     # U3c's format
    assert card["ref"]["round"] == 1 and card["ref"]["done"] is False
    assert card["ref"]["threshold"] == pytest.approx(0.85)            # the default
    assert card["ref"]["issues_open"][0]["severity"] == "high" and card["ref"]["issues_fixed"] == []
    assert card["ref"]["after_image_id"] == card["ref"]["after"]["iso"]
    assert "/" not in card["ref"]["after_image_id"]                       # an id, not a path


# ---- present_view (G-R7)

def _store_ctx(tmp_path, project_id, run_project):
    store = Store(tmp_path / "db.sqlite")
    run = store.create_run("designer", "fake", "fake/model", "t", project_id=run_project)
    bus = EventBus()
    return store, ToolContext(run_id=run.id, user="local", store=store, events=bus,
                              workdir=tmp_path, policy=None, call_id="pv1", services={})


async def test_present_view_emits_spec_and_thumbnail(tmp_path):
    _make_part(tmp_path, "cube")
    store, ctx = _store_ctx(tmp_path, "pA", "pA")
    store.register_part("cube", "pA")
    seen = []
    sub = ctx.events.subscribe(ctx.run_id)
    out = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="cube", kind="orbit", caption="overall shape"))
    while not sub.queue.empty():
        seen.append(sub.queue.get_nowait())
    cards = [e for e in seen if e.type == "agent_announce" and e.data["kind"] == "view_presented"]
    assert len(cards) == 1
    spec = cards[0].data["ref"]["view_spec"]
    assert spec["kind"] == "orbit" and spec["parts"] == ["cube"]
    assert spec["target"] == {"kind": "part", "id": "cube", "part_id": "cube",
                              "parts": ["cube"], "ghost": []}
    assert spec["thumbnail_id"] == cards[0].data["ref"]["thumbnail_id"]
    assert cards[0].data["detail"] == "overall shape"
    from pathlib import Path
    assert Path(out.data["thumbnail"]).exists()
    # stored as a thread step, so a reloaded thread rebuilds the card
    stored = [s.payload["agent_announce"] for s in store.steps(ctx.run_id)
              if "agent_announce" in s.payload]
    assert stored == [cards[0].data]


async def test_present_view_refuses_part_from_another_build(tmp_path):
    _make_part(tmp_path, "cube")        # belongs to build pB, run is in pA
    store, ctx = _store_ctx(tmp_path, "pA", "pA")
    store.register_part("cube", "pB")
    with pytest.raises(MisesError) as e:
        await views.present_view.handler(ctx, views.PresentViewArgs(target="cube", kind="orbit"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


async def test_present_view_section_and_exploded(tmp_path):
    _make_part(tmp_path, "body")
    _make_part(tmp_path, "lid")
    store, ctx = _store_ctx(tmp_path, "pA", "pA")
    store.register_part("body", "pA")
    store.register_part("lid", "pA")
    out = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="exploded"))
    assert out.data["view_spec"]["explode"] > 0
    out2 = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="section",
        section=views.SectionSpec(origin=[0, 0, 0], normal=[0, 0, 1])))
    assert out2.data["view_spec"]["section"] is not None


def test_render_views_is_read_only_tool():
    assert views.render_views.safety == SafetyClass.READ
    assert views.critique_model.safety == SafetyClass.READ
    assert views.present_view.safety == SafetyClass.READ


# ---- self-critique: the calling model can see images (Claude Code, a vision API model)

def _seeing_rt(tmp_path):
    def no_second_model(rid):
        raise AssertionError("self-critique must not call a separate vision model")
    return SimpleNamespace(home=tmp_path, run_sees_images=lambda rid: True,
                           vision_model_for=no_second_model)


def _decoded_png_size(img):
    import base64
    return _png_size(base64.b64decode(img.data_b64))


async def test_render_views_attaches_images_for_a_seeing_model(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="s0", services={"runtime": _seeing_rt(tmp_path)})
    out = await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="cube", size=1024))
    assert [i.label for i in out.images] == ["front", "top", "side", "iso"]
    assert all(i.media_type == "image/png" for i in out.images)
    assert all(max(_decoded_png_size(i)) <= 512 for i in out.images)   # capped at 512 px
    assert out.data["image_tokens"] == 4 * (512 * 512 // 750)


async def test_render_views_no_images_for_a_blind_model(tmp_path):
    _make_part(tmp_path, "cube")
    rt = SimpleNamespace(home=tmp_path, run_sees_images=lambda rid: False)
    ctx = _bare_ctx(tmp_path, run_id="s1", services={"runtime": rt})
    out = await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="cube"))
    assert out.images == [] and out.handles


_BAD_REPORT = views.CritiqueReport(score=0.3, issues=[views.CritiqueIssue(
    view="front", issue="flat planes where the wing should be curved", severity="high",
    suggested_fix="loft NACA sections")])
_GOOD_REPORT = views.CritiqueReport(score=0.9, issues=[])


async def test_self_critique_loop_stops_on_passing_report(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="s2", services={"runtime": _seeing_rt(tmp_path)})
    args = dict(part_id="cube", request="a B-2 bomber")
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    assert r.data["mode"] == "self" and r.data["awaiting_report"] and len(r.images) == 4
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=_BAD_REPORT))
    assert r.data["round"] == 1 and r.data["done"] is False and r.images == []
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    assert len(r.images) == 4 and r.data["round"] == 2
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=_GOOD_REPORT))
    assert r.data["done"] is True and r.data["stop_reason"] == "score_threshold"


async def test_self_critique_round_cap_and_no_more_renders(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="s3", services={"runtime": _seeing_rt(tmp_path)})
    args = dict(part_id="cube", request="x", max_rounds=2)
    for _ in range(2):
        await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
        r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=_BAD_REPORT))
    assert r.data["round"] == 2 and r.data["done"] and r.data["stop_reason"] == "max_rounds"
    again = await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    assert again.data["done"] and again.data["stop_reason"] == "max_rounds" and again.images == []


async def test_self_critique_image_token_budget(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="s4", services={"runtime": _seeing_rt(tmp_path)})
    args = dict(part_id="cube", request="x", max_image_tokens=1000)   # < one round of 4 views
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    assert r.data["image_tokens"] >= 1000
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=_BAD_REPORT))
    assert r.data["done"] and r.data["stop_reason"] == "image_budget"


async def test_self_critique_report_without_renders_is_refused(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="s5", services={"runtime": _seeing_rt(tmp_path)})
    with pytest.raises(MisesError) as e:
        await views.critique_model.handler(ctx, views.CritiqueArgs(
            part_id="cube", request="x", report=_GOOD_REPORT))
    assert e.value.code == ErrorCode.INVALID_INPUT


async def test_self_critique_attaches_reference_images(tmp_path):
    import base64
    from mises import references as refs
    from mises.references import PhotoIn, ReferenceUpload
    from PIL import Image
    _make_part(tmp_path, "cube")
    im = Image.new("RGB", (900, 600), (255, 255, 255))
    im.paste((30, 30, 30), (300, 150, 600, 450))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    rid = refs.save_reference(tmp_path, ReferenceUpload(
        image=PhotoIn(data_b64=base64.b64encode(buf.getvalue()).decode()),
        view="top"))["reference_id"]
    ctx = _bare_ctx(tmp_path, run_id="s6", services={"runtime": _seeing_rt(tmp_path)})
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="x", reference_image_ids=[rid]))
    assert len(r.images) == 5 and r.images[-1].label == f"reference {rid}"
    assert max(_decoded_png_size(r.images[-1])) <= 512
    assert set(r.data["iou"]) == {"top"}                          # a tagged view only


# ---- the shared card contract (G1 <-> G4): stored steps, ids, fixed/open, structured targets

def _two_part_build(tmp_path):
    _make_part(tmp_path, "body")
    _make_part(tmp_path, "lid")
    store, ctx = _store_ctx(tmp_path, "pA", "pA")
    store.register_part("body", "pA")
    store.register_part("lid", "pA")
    return store, ctx


def _cards(store, run_id, kind):
    return [s.payload["agent_announce"] for s in store.steps(run_id)
            if s.payload.get("agent_announce", {}).get("kind") == kind]


async def test_subset_view_ghosts_the_rest_of_the_build(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    out = await views.present_view.handler(ctx, views.PresentViewArgs(
        target={"kind": "project", "id": "pA", "parts": ["lid"]}, kind="subset"))
    spec = out.data["view_spec"]
    assert spec["parts"] == ["lid"] and spec["ghost"] == ["body"]
    assert spec["target"] == {"kind": "project", "id": "pA", "project_id": "pA",
                              "parts": ["lid"], "ghost": ["body"]}
    assert _cards(store, ctx.run_id, "view_presented")[0]["ref"]["project_id"] == "pA"


async def test_present_view_refuses_another_builds_project(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    with pytest.raises(MisesError) as e:
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target={"kind": "project", "id": "pB"}, kind="orbit"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE
    store.register_part("alien", "pB")
    _make_part(tmp_path, "alien")
    with pytest.raises(MisesError) as e:            # a ghost from another build is refused too
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target={"kind": "project", "id": "pA", "parts": ["lid"], "ghost": ["alien"]},
            kind="subset"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


async def test_critique_cards_split_fixed_and_open_and_are_stored(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    ctx.services = {"runtime": _seeing_rt(tmp_path)}
    two = views.CritiqueReport(score=0.4, issues=[
        views.CritiqueIssue(view="front", issue="Wing is flat", severity="high"),
        views.CritiqueIssue(view="top", issue="no sawtooth trailing edge", severity="medium")])
    one = views.CritiqueReport(score=0.6, issues=[
        views.CritiqueIssue(view="top", issue="No sawtooth trailing edge!", severity="high")])
    args = dict(part_id="body", request="a B-2", score_threshold=0.75)
    for rep in (two, one):
        await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
        await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=rep))
    first, second = (c["ref"] for c in _cards(store, ctx.run_id, "critique_round"))
    assert len(first["issues_open"]) == 2 and first["issues_fixed"] == []
    assert [i["issue"] for i in second["issues_fixed"]] == ["Wing is flat"]
    assert [i["view"] for i in second["issues_open"]] == ["top"]   # same issue, still open
    assert second["threshold"] == pytest.approx(0.75) and second["part_id"] == "body"
    assert second["before"] == first["after"] and second["before_image_id"].endswith("_iso")


async def test_cards_are_copied_to_the_lead(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    lead = ctx.run_id
    child = store.create_run("designer", "fake", "m", "sub", parent_id=lead, project_id="pA").id
    ctx.run_id = child
    await views.present_view.handler(ctx, views.PresentViewArgs(target="lid", kind="orbit"))
    assert _cards(store, child, "view_presented") and _cards(store, lead, "view_presented")
    assert _cards(store, lead, "view_presented")[0]["run_id"] == child     # the acting run


# ---- scope (I1): every id a call touches must be in the run's scope

def _scoped(ctx, *scope):
    ctx.policy = SimpleNamespace(edit_scope=[], read_scope=list(scope))
    return ctx


async def test_parts_outside_scope_are_refused(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    _scoped(ctx, "body")
    with pytest.raises(MisesError) as e:                 # part_id in scope, parts not
        await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="body",
                                                                    parts=["lid"]))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE and "lid" in e.value.message
    with pytest.raises(MisesError) as e:                 # a ghost outside scope
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target={"kind": "part", "id": "body", "ghost": ["lid"]}, kind="orbit"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE
    with pytest.raises(MisesError) as e:                 # the whole build needs build scope
        await views.render_views.handler(ctx, views.RenderViewsArgs())
    assert e.value.code == ErrorCode.OUT_OF_SCOPE
    ok = await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="body",
                                                                     views=["iso"]))
    assert ok.data["parts"] == ["body"]


async def test_whole_build_allowed_when_the_build_is_in_scope(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    _scoped(ctx, "pA")
    out = await views.render_views.handler(ctx, views.RenderViewsArgs(project_id="pA",
                                                                      views=["iso"]))
    assert sorted(out.data["parts"]) == ["body", "lid"]
    with pytest.raises(MisesError) as e:                 # another build's id
        await views.render_views.handler(ctx, views.RenderViewsArgs(project_id="pB"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


def test_gate_scope_keys_name_every_id():
    keys = views.critique_model.scope_keys(views.CritiqueArgs(part_id="a1", parts=["b1", "c1"],
                                                              request="x"))
    assert keys == ["a1", "b1", "c1"]
    pv = views.present_view.scope_keys(views.PresentViewArgs(
        target={"kind": "project", "id": "pA", "parts": ["b1"], "ghost": ["c1"]}, kind="subset",
        xray_parts=["d1"]))
    assert pv == ["pA", "b1", "c1", "d1"]
    assert views.render_views.scope_keys(views.RenderViewsArgs()) == ["this_build"]


# ---- budgets (I4): fixed by the user's profile; the agent can only lower them

def _limited_rt(tmp_path, **prof):
    rt = _seeing_rt(tmp_path)
    rt.profile = lambda: SimpleNamespace(**{"critique_max_rounds": 3,
                                            "critique_time_budget_s": 300.0,
                                            "critique_max_image_tokens": 12_000, **prof})
    return rt


async def _round(ctx, report=_BAD_REPORT, **kw):
    args = {"part_id": "cube", "request": "x", **kw}
    await views.critique_model.handler(ctx, views.CritiqueArgs(**args))
    return await views.critique_model.handler(ctx, views.CritiqueArgs(**args, report=report))


async def test_agent_cannot_raise_the_round_limit(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="b1", services={"runtime": _limited_rt(
        tmp_path, critique_max_rounds=2)})
    r = await _round(ctx, max_rounds=6)
    assert r.data["max_rounds"] == 2
    r = await _round(ctx, max_rounds=6)
    assert r.data["done"] and r.data["stop_reason"] == "max_rounds" and r.data["round"] == 2
    again = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="x", max_rounds=6))
    assert again.data["done"] and again.images == []


async def test_agent_can_lower_the_round_limit(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="b2", services={"runtime": _limited_rt(tmp_path)})
    r = await _round(ctx, max_rounds=1)
    assert r.data["done"] and r.data["stop_reason"] == "max_rounds"


async def test_round_count_survives_a_change_of_parts(tmp_path):
    store, ctx = _two_part_build(tmp_path)
    ctx.services = {"runtime": _limited_rt(tmp_path, critique_max_rounds=2)}
    for _ in range(2):
        await _round(ctx, part_id="body")
    for parts in (["body", "lid"], ["lid", "body"]):       # a new set, any order: no reset
        r = await views.critique_model.handler(ctx, views.CritiqueArgs(
            part_id=None, parts=parts, request="x"))
        assert r.data["done"] and r.data["stop_reason"] == "max_rounds"


async def test_time_budget(tmp_path, monkeypatch):
    _make_part(tmp_path, "cube")
    clock = [1000.0]
    monkeypatch.setattr(views, "_now", lambda: clock[0])
    ctx = _bare_ctx(tmp_path, run_id="b3", services={"runtime": _limited_rt(
        tmp_path, critique_time_budget_s=60.0)})
    r = await _round(ctx, time_budget_s=600)             # raising is ignored: 60 s holds
    assert not r.data["done"]
    clock[0] += 61
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="x"))
    assert r.data["done"] and r.data["stop_reason"] == "time_budget"


async def test_usd_budget(tmp_path):
    from mises.types import Caps
    _make_part(tmp_path, "cube")
    store, ctx = _store_ctx(tmp_path, "pA", "pA")
    store.register_part("cube", "pA")
    store.create_policy(ctx.run_id, ["*"], ["*"], Caps(usd_run=1.0), [SafetyClass.READ])
    ctx.services = {"runtime": _limited_rt(tmp_path)}
    r = await _round(ctx)
    assert not r.data["done"]
    store.add_usage(ctx.run_id, input_tokens=1, output_tokens=1, usd=2.0)   # over the cap
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube", request="x"))
    assert r.data["done"] and r.data["stop_reason"] == "budget" and r.images == []


async def test_image_budget_cannot_be_raised(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="b4", services={"runtime": _limited_rt(
        tmp_path, critique_max_image_tokens=1000)})
    r = await _round(ctx, max_image_tokens=100_000)
    assert r.data["done"] and r.data["stop_reason"] == "image_budget"


async def test_run_state_is_lru_evicted_not_wiped(tmp_path, monkeypatch):
    monkeypatch.setattr(views, "_RUNS_MAX", 3)
    for rid in ("r1", "r2", "r3"):
        views._run_state(rid)
    views._run_state("r1")                                # r1 used again: r2 is now oldest
    views._run_state("r4")
    assert list(views._runs) == ["r3", "r1", "r4"]


async def test_same_renders_are_not_sent_twice_in_a_round(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="d1", services={"runtime": _limited_rt(tmp_path)})
    first = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube",
                                                                       request="x"))
    again = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube",
                                                                       request="x"))
    assert len(first.images) == 4 and again.images == [] and again.data["already_sent"]
    assert again.data["render_ids"] == first.data["render_ids"]
    from mises import edits
    edits.write_version(tmp_path / "parts" / "cube", 7)  # the part changed: render again
    third = await views.critique_model.handler(ctx, views.CritiqueArgs(part_id="cube",
                                                                       request="x"))
    assert len(third.images) == 4


async def test_rounds_report_the_limit_the_opening_call_asked_for(tmp_path):
    # benchmark: the agent asked for 2 rounds; the report call omitted it and the card said 3
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="k1", services={"runtime": _limited_rt(tmp_path)})
    opened = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="x", max_rounds=2))
    assert opened.data["max_rounds"] == 2
    closed = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="x", report=_BAD_REPORT))
    assert closed.data["max_rounds"] == 2 and "round 1/2" in closed.summary
    r = await _round(ctx)                                 # no max_rounds passed at all
    assert r.data["done"] and r.data["stop_reason"] == "max_rounds" and r.data["round"] == 2
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(   # nor raise it again
        part_id="cube", request="x", max_rounds=6))
    assert r.data["done"] and r.data["max_rounds"] == 2 and r.images == []


_MEDIUM_OPEN = views.CritiqueReport(score=0.9, issues=[views.CritiqueIssue(
    view="top", issue="wingtips 24 mm aft of the tail", severity="medium")])
_LOW_ONLY = views.CritiqueReport(score=0.9, issues=[views.CritiqueIssue(
    view="iso", issue="no cockpit windows", severity="low")])


async def test_critique_keeps_going_while_a_medium_issue_is_open(tmp_path):
    # benchmark: it stopped at 0.82 (over the old 0.8 threshold) with a medium issue open
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="k2", services={"runtime": _limited_rt(tmp_path)})
    r = await _round(ctx, report=_MEDIUM_OPEN)
    assert r.data["done"] is False and r.data["stop_reason"] is None
    assert "medium" in r.summary and r.data["threshold"] == pytest.approx(0.85)
    r = await _round(ctx, report=_LOW_ONLY)              # only a low issue left: done
    assert r.data["done"] and r.data["stop_reason"] == "score_threshold"


async def test_medium_issue_still_open_at_the_round_limit_is_reported(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="k3", services={"runtime": _limited_rt(tmp_path)})
    r = await _round(ctx, report=_MEDIUM_OPEN, max_rounds=1)
    assert r.data["done"] and r.data["stop_reason"] == "max_rounds"
    assert "still open" in r.summary


async def test_low_score_with_only_low_issues_stops_without_passing(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="k4", services={"runtime": _limited_rt(tmp_path)})
    r = await _round(ctx, report=views.CritiqueReport(score=0.5, issues=_LOW_ONLY.issues))
    assert r.data["done"] and r.data["stop_reason"] == "no_high_severity"


# ---- view naming: aircraft names, and every render says where its cameras sit

async def test_render_views_label_views_by_axis(tmp_path):
    _make_part(tmp_path, "cube")
    out = await views.render_views.handler(_bare_ctx(tmp_path), views.RenderViewsArgs(
        part_id="cube", size=128))
    assert out.data["view_axes"]["front"].startswith("camera on -Y")
    assert out.data["view_axes"]["side"].startswith("camera on +X")
    assert "front = camera on -Y" in out.summary


async def test_aircraft_view_naming(tmp_path):
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id="a1", services={"runtime": _seeing_rt(tmp_path)})
    out = await views.render_views.handler(ctx, views.RenderViewsArgs(
        part_id="cube", size=128, naming="aircraft", nose="-x"))
    assert list(out.data["views"]) == ["nose", "top", "left", "iso"]
    assert [i.label for i in out.images] == ["nose", "top", "left", "iso"]
    assert "head-on" in out.data["view_axes"]["nose"] and "-X" in out.data["view_axes"]["nose"]
    picked = await views.render_views.handler(ctx, views.RenderViewsArgs(
        part_id="cube", size=128, naming="aircraft", views=["tail", "bogus", "right"]))
    assert list(picked.data["views"]) == ["tail", "right"]
    crit = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="a B-2", naming="aircraft"))
    assert crit.data["views"] == ["nose", "top", "left", "iso"]
    assert "nose = head-on" in crit.summary


def test_aircraft_cameras_follow_the_nose():
    cams, where = views.aircraft_cameras("-x")
    assert cams["nose"][0] == (1.0, 0.0, 0.0)          # camera ahead of a -X nose looks +X
    assert cams["left"][0] == (0.0, 1.0, 0.0)          # port is -Y: it looks toward +Y
    cams, _ = views.aircraft_cameras("+y")
    assert cams["nose"][0] == (0.0, -1.0, 0.0)


# ---- whole-build renders draw the assembly: instances at their solved transforms

def _stl_box(lo, hi) -> bytes:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = {0: (x0, y0, z0), 1: (x1, y0, z0), 2: (x1, y1, z0), 3: (x0, y1, z0),
         4: (x0, y0, z1), 5: (x1, y0, z1), 6: (x1, y1, z1), 7: (x0, y1, z1)}
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    out = bytearray(b"\0" * 80) + struct.pack("<I", 12)
    for a, b, c, d in quads:
        for tri in ((v[a], v[b], v[c]), (v[a], v[c], v[d])):
            out += struct.pack("<3f", 0, 0, 0)
            for vert in tri:
                out += struct.pack("<3f", *vert)
            out += struct.pack("<H", 0)
    return bytes(out)


def _tf(x, y, z):
    return [[1.0, 0, 0, x], [0, 1.0, 0, y], [0, 0, 1.0, z], [0, 0, 0, 1.0]]


def _pi_case(tmp_path):
    """The Pi-case benchmark's layout (harness/pi_check.py): every part is modelled at its own
    origin and placed only by its assembly transform; one built part is in no instance."""
    from mises.assembly.model import AssemblyStore, Instance
    parts = {"case_base": ((-58, -52, 0), (58, 52, 28)),
             "lipo_battery": ((-51, -17, 0), (51, 17, 17)),
             "cooling_fan": ((-15, -15, 0), (15, 15, 10)),
             "raspberry_pi_detail": ((-42.5, -28, 0), (42.5, 28, 2)),
             "raspberry_pi_board": ((-42.5, -28, 0), (42.5, 28, 1.6))}     # unused
    store, ctx = _store_ctx(tmp_path, "pA", "pA")
    for pid, (lo, hi) in parts.items():
        d = tmp_path / "parts" / pid
        d.mkdir(parents=True)
        (d / "model.stl").write_bytes(_stl_box(lo, hi))
        (d / "metrics.json").write_text("{}")
        store.register_part(pid, "pA")
    asm = AssemblyStore(tmp_path / "assembly.db")
    for pid, tf in (("case_base", _tf(0, 0, 0)), ("lipo_battery", _tf(2.5, 32, 2)),
                    ("cooling_fan", _tf(-3, -16.5, 18)),
                    ("raspberry_pi_detail", _tf(10.5, -20.5, 7))):
        asm.add_instance("pA", Instance(part_id=pid, name=pid, transform=tf, fixed=True))
    ctx.services = {"assembly": asm}
    return store, ctx


def _world_z(meshes) -> dict[str, tuple[float, float]]:
    """Each mesh's world z range, from its STL and the transform the worker is handed."""
    from mises.cad import render
    out = {}
    for m in meshes:
        tris = render.load_stl((m.part_dir / "model.stl").read_bytes())
        if m.transform is not None:
            T = np.asarray(m.transform)
            tris = tris @ T[:3, :3].T + T[:3, 3]
        out[m.name] = (float(tris[..., 2].min()), float(tris[..., 2].max()))
    return out


@pytest.fixture
def captured(monkeypatch):
    seen: list = []
    real = views._render

    async def spy(ctx, meshes, **kw):
        seen.append(list(meshes))
        return await real(ctx, meshes, **kw)
    monkeypatch.setattr(views, "_render", spy)
    return seen


async def test_pi_case_renders_with_the_fan_above_the_chip(tmp_path, captured):
    store, ctx = _pi_case(tmp_path)
    out = await views.render_views.handler(ctx, views.RenderViewsArgs(views=["front"],
                                                                      size=128))
    assert out.data["parts"] == ["case_base", "lipo_battery", "cooling_fan",
                                 "raspberry_pi_detail"]           # the unused board is not drawn
    z = _world_z(captured[-1])
    assert set(z) == set(out.data["parts"])
    assert z["cooling_fan"][0] >= z["raspberry_pi_detail"][1] + 5   # above the chip...
    assert z["cooling_fan"][0] > z["case_base"][0] + 10             # ...not on the floor
    assert z["lipo_battery"][0] == pytest.approx(2.0)


async def test_named_parts_are_placed_but_a_single_part_keeps_its_frame(tmp_path, captured):
    store, ctx = _pi_case(tmp_path)
    await views.render_views.handler(ctx, views.RenderViewsArgs(
        parts=["cooling_fan", "raspberry_pi_board"], views=["iso"], size=128))
    z = _world_z(captured[-1])
    assert z["cooling_fan"][0] == pytest.approx(18.0)                # its instance
    assert z["raspberry_pi_board"][0] == pytest.approx(0.0)          # in no instance: own frame
    await views.render_views.handler(ctx, views.RenderViewsArgs(part_id="cooling_fan",
                                                                views=["iso"], size=128))
    assert _world_z(captured[-1])["cooling_fan"][0] == pytest.approx(0.0)


async def test_build_views_and_critique_draw_the_assembly(tmp_path, captured):
    store, ctx = _pi_case(tmp_path)
    pv = await views.present_view.handler(ctx, views.PresentViewArgs(
        target={"kind": "project", "id": "pA"}, kind="orbit"))
    assert "raspberry_pi_board" not in pv.data["parts"]
    assert _world_z(captured[-1])["cooling_fan"][0] == pytest.approx(18.0)
    sub = await views.present_view.handler(ctx, views.PresentViewArgs(
        target={"kind": "project", "id": "pA", "parts": ["cooling_fan"]}, kind="subset"))
    assert "raspberry_pi_board" not in sub.data["ghost"]
    assert _world_z(captured[-1])["raspberry_pi_detail"][0] == pytest.approx(7.0)  # a ghost
    ctx.services["runtime"] = _seeing_rt(tmp_path)
    await views.critique_model.handler(ctx, views.CritiqueArgs(request="a Pi case"))
    assert _world_z(captured[-1])["cooling_fan"][0] == pytest.approx(18.0)


async def test_joints_place_instances_through_the_solve(tmp_path, captured):
    from mises.assembly.model import Frame, Joint, JointRef
    store, ctx = _pi_case(tmp_path)
    asm = ctx.services["assembly"]
    fan = next(i for i in asm.get("pA").instances if i.part_id == "cooling_fan")
    base = next(i for i in asm.get("pA").instances if i.part_id == "case_base")
    asm.update_instance("pA", fan.id, fixed=False, transform=_tf(0, 0, 0))
    asm.add_joint("pA", Joint(kind="fixed",
                              a=JointRef(instance_id=base.id,
                                         frame=Frame(origin=[0, 0, 28], axis=[0, 0, 1])),
                              b=JointRef(instance_id=fan.id,
                                         frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1]))))
    await views.render_views.handler(ctx, views.RenderViewsArgs(views=["iso"], size=128))
    assert _world_z(captured[-1])["cooling_fan"][0] == pytest.approx(28.0)


async def test_each_user_turn_on_a_lead_gets_a_fresh_critique_loop(tmp_path, monkeypatch):
    # final review I2: the loop's clock, rounds and image budget were per run, so a build's
    # long-lived lead got "stopped (time_budget)" on a follow-up request 10 minutes later
    import mises.runtime as runtime_mod
    from mises.runtime import Runtime
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (FakeProvider([]), "m"))
    lead = rt.store.create_run("engineer", "fake", "fake/model", "a Pi case").id
    _make_part(tmp_path, "cube")
    ctx = _bare_ctx(tmp_path, run_id=lead, services={"runtime": _limited_rt(
        tmp_path, critique_max_rounds=2)})
    clock = [1000.0]
    monkeypatch.setattr(views, "_now", lambda: clock[0])
    rounds: list = []

    async def turn(run_id, text):              # the lead critiques the same part each turn
        for _ in range(2):
            rounds.append((await _round(ctx)).data)
    monkeypatch.setattr(rt, "_send", turn)
    await rt.send(lead, "make the case")
    assert [r["round"] for r in rounds] == [1, 2] and rounds[-1]["stop_reason"] == "max_rounds"
    clock[0] += 600                                # the next request, 10 minutes later
    await rt.send(lead, "now add vents to the lid")
    assert [r["round"] for r in rounds[2:]] == [1, 2]
    assert rounds[2]["done"] is False and rounds[2]["image_tokens"] < rounds[1]["image_tokens"]


def test_section_thumbnail_faces_the_cut():
    assert views._section_view([0, -1, 0]) == "front"     # -Y half removed: look along +Y
    assert views._section_view([0, 0, 1]) == "top"        # +Z half removed: look down
    assert views._section_view([1, 0, 0]) == "side"       # +X half removed: look along -X


async def test_critique_model_only_reads_references_of_its_own_build(tmp_path, monkeypatch):
    """Release merge (b), M7: a run's critique_model reads its build's references (and ones tied to
    no build, which its context lists); another build's reference id is refused, not read."""
    import base64
    from mises import references as refs
    from mises.references import PhotoIn, ReferenceUpload
    from mises.types import MisesError
    from PIL import Image
    _make_part(tmp_path, "cube")
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 20, 20)).save(buf, "PNG")

    def ref(project):
        return refs.save_reference(tmp_path, ReferenceUpload(
            image=PhotoIn(data_b64=base64.b64encode(buf.getvalue()).decode()), view="top",
            project_id=project))["reference_id"]
    mine, loose, theirs = ref("buildA"), ref(None), ref("buildB")
    monkeypatch.setattr(views, "_run_project_id", lambda ctx: "buildA")
    ctx = _bare_ctx(tmp_path, run_id="m7", services={"runtime": _seeing_rt(tmp_path)})
    r = await views.critique_model.handler(ctx, views.CritiqueArgs(
        part_id="cube", request="x", reference_image_ids=[mine, loose]))
    assert {i.label for i in r.images} >= {f"reference {mine}", f"reference {loose}"}
    ctx2 = _bare_ctx(tmp_path, run_id="m7b", services={"runtime": _seeing_rt(tmp_path)})
    with pytest.raises(MisesError) as e:
        await views.critique_model.handler(ctx2, views.CritiqueArgs(
            part_id="cube", request="x", reference_image_ids=[mine, theirs]))
    assert e.value.code == "out_of_scope" and theirs in e.value.message
