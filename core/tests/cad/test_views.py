"""render_views / critique_model / present_view tools (G1 / G-R7). No real build needed: a
model.stl is written directly, and the vision model is a scripted fake."""
from __future__ import annotations

import io
import struct
from types import SimpleNamespace

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
    views._rounds.clear()
    yield
    views._rounds.clear()


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
    assert r1.data["issues"][0]["severity"] == "high"
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
    assert cards[0].data["round"] == 1 and cards[0].data["done"] is False


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
    spec = cards[0].data["view_spec"]
    assert spec["kind"] == "orbit" and spec["parts"] == ["cube"]
    from pathlib import Path
    assert Path(out.data["thumbnail"]).exists()


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
