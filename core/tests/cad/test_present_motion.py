"""present_view kinds `animation` and `section_sweep` (Plan 9 G-D, D11 / D18): an agent presents
its assembly's motion, or a section plane sweeping through it, as a View-stream card the app
opens full window on the assembly's poses."""
from __future__ import annotations

import json
import struct

import pytest

from mises.assembly.model import AssemblyStore, Frame, Instance, Joint, JointRef
from mises.cad import views
from mises.events import EventBus
from mises.store import Store
from mises.tools.registry import ToolContext
from mises.types import ErrorCode, MisesError


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


def _hinge(tmp_path):
    """Two 40x30x4 plates in build pA: `base` grounded, `lid` on a revolute about Y at the
    base's +X edge, limits 0-180 (the app's hinge fixture, in miniature)."""
    store = Store(tmp_path / "db.sqlite")
    run = store.create_run("designer", "fake", "fake/model", "t", project_id="pA")
    for pid in ("plate", "lid_plate"):
        d = tmp_path / "parts" / pid
        d.mkdir(parents=True)
        (d / "model.stl").write_bytes(_stl_box((-20, -15, 0), (20, 15, 4)))
        (d / "metrics.json").write_text(json.dumps(
            {"bbox": {"min": [-20, -15, 0], "max": [20, 15, 4], "size": [40, 30, 4]}}))
        store.register_part(pid, "pA")
    asm = AssemblyStore(tmp_path / "assembly.db")
    base = asm.add_instance("pA", Instance(part_id="plate", name="base", fixed=True))
    lid = asm.add_instance("pA", Instance(part_id="lid_plate", name="lid"))
    joint = asm.add_joint("pA", Joint(
        kind="revolute", limits=[0, 180],
        a=JointRef(instance_id=base.id, frame=Frame(origin=[20, 0, 0], axis=[0, 1, 0])),
        b=JointRef(instance_id=lid.id, frame=Frame(origin=[-20, 0, 0], axis=[0, 1, 0]))))
    ctx = ToolContext(run_id=run.id, user="local", store=store, events=EventBus(),
                      workdir=tmp_path, policy=None, call_id="pv1", services={"assembly": asm})
    return store, ctx, asm, joint


def _cards(sub):
    out = []
    while not sub.queue.empty():
        e = sub.queue.get_nowait()
        if e.type == "agent_announce" and e.data["kind"] == "view_presented":
            out.append(e.data)
    return out


async def test_animation_by_id_carries_the_saved_steps(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    saved = asm.save_animation("pA", "open the lid", {
        "steps": [{"joint_id": joint.id, "from": 0, "to": 120, "duration_s": 2},
                  {"joint_id": joint.id, "from": 120, "to": 0, "duration_s": 1}],
        "loops": 2, "duration_s": 3})
    sub = ctx.events.subscribe(ctx.run_id)
    out = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="animation", animation={"animation_id": saved["id"]},
        caption="the lid swings open"))
    spec = out.data["view_spec"]
    assert spec["kind"] == "animation" and spec["is_project"] is True
    assert spec["animation"] == {"animation_id": saved["id"], "name": "open the lid",
                                 "steps": saved["steps"], "loops": 2, "duration_s": 3}
    assert spec["sweep"] is None and spec["parts"] == ["plate", "lid_plate"]
    cards = _cards(sub)
    assert len(cards) == 1 and cards[0]["ref"]["view_spec"] == spec
    from PIL import Image                   # the still has no backdrop: it sits on either theme
    with Image.open(out.data["thumbnail"]) as im:
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0
    assert cards[0]["title"].startswith("Animation of") and cards[0]["ref"]["thumbnail_id"]
    # by name too
    out2 = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="animation", animation={"animation_id": "open the lid"}))
    assert out2.data["view_spec"]["animation"]["animation_id"] == saved["id"]


async def test_animation_from_inline_keyframes_is_clamped_to_the_joint(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    out = await views.present_view.handler(ctx, views.PresentViewArgs.model_validate({
        "target": {"kind": "project", "id": "pA"}, "kind": "animation",
        "animation": {"keyframes": [{"joint_id": joint.id, "to": 270, "duration_s": 2}],
                      "loops": 3}}))
    a = out.data["view_spec"]["animation"]
    assert a["animation_id"] is None and a["loops"] == 3 and a["duration_s"] == 2
    assert a["steps"] == [{"joint_id": joint.id, "from": 0, "to": 180, "duration_s": 2}]


async def test_animation_needs_a_known_joint_and_the_build(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    with pytest.raises(MisesError) as e:
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target="pA", kind="animation", animation={"animation_id": "nope"}))
    assert e.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(MisesError) as e:
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target="pA", kind="animation",
            animation={"keyframes": [{"joint_id": "zzz", "to": 1}]}))
    assert e.value.code == ErrorCode.NOT_FOUND
    # a motion is the assembly's: a single part target is refused
    with pytest.raises(MisesError) as e:
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target="plate", kind="animation", animation={"animation_id": "x"}))
    assert e.value.code == ErrorCode.INVALID_INPUT
    # another build's saved animation is not found from this run
    other = asm.save_animation("pB", "theirs", {"steps": [], "loops": 1, "duration_s": 1})
    with pytest.raises(MisesError):
        await views.present_view.handler(ctx, views.PresentViewArgs(
            target="pA", kind="animation", animation={"animation_id": other["id"]}))
    with pytest.raises(ValueError):
        views.PresentViewArgs(target="pA", kind="animation")          # needs `animation`
    with pytest.raises(ValueError):
        views.AnimationRef(animation_id="a", keyframes=[{"joint_id": "j", "to": 1}])


async def test_section_sweep_defaults_to_the_assembly_box_and_cuts_near_its_middle(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    sub = ctx.events.subscribe(ctx.run_id)
    out = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="section_sweep", sweep={"axis": "x"}, caption="what is inside"))
    spec = out.data["view_spec"]
    # base spans x -20..20, the lid (at 0 deg, flat beside it) 20..60
    assert spec["sweep"] == {"axis": "x", "from": -20.0, "to": 60.0, "duration_s": 4.0}
    # the thumbnail's cut: 45% along (off the joint line at 20), its kept side facing iso
    assert spec["section"] == {"origin": [16.0, 0.0, 0.0], "normal": [1.0, 0.0, 0.0]}
    y = await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="section_sweep", sweep={"axis": "y"}))
    assert y.data["view_spec"]["section"]["normal"] == [-0.0, -1.0, -0.0]
    assert spec["animation"] is None
    card = _cards(sub)[0]
    assert card["title"].startswith("Section sweep of") and card["detail"] == "what is inside"


async def test_section_sweep_with_a_range(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    out = await views.present_view.handler(ctx, views.PresentViewArgs.model_validate({
        "target": "pA", "kind": "section_sweep",
        "sweep": {"axis": "z", "from": 4, "to": 0, "duration_s": 6}}))
    spec = out.data["view_spec"]
    assert spec["sweep"] == {"axis": "z", "from": 4.0, "to": 0.0, "duration_s": 6.0}
    assert spec["section"]["origin"] == pytest.approx([0.0, 0.0, 2.2])
    with pytest.raises(ValueError):
        views.PresentViewArgs(target="pA", kind="section_sweep")      # needs `sweep`
    with pytest.raises(ValueError):
        views.SweepSpec(axis="w")


async def test_motion_kinds_are_view_stream_cards_in_the_thread(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    await views.present_view.handler(ctx, views.PresentViewArgs(
        target="pA", kind="section_sweep", sweep={"axis": "y"}))
    stored = [s.payload["agent_announce"] for s in store.steps(ctx.run_id)
              if "agent_announce" in s.payload]
    assert stored and stored[-1]["ref"]["view_spec"]["kind"] == "section_sweep"


# ---- fix round 1: untrusted agent numbers never reach a card as NaN / inf / huge

@pytest.mark.parametrize("sweep", [
    '{"axis": "x", "from": -1e308, "to": 1e308}', '{"axis": "x", "from": Infinity}',
    '{"axis": "x", "to": NaN}', '{"axis": "x", "from": 1e6}'])
def test_a_sweep_end_must_be_finite_and_bounded(sweep):
    with pytest.raises(ValueError):
        views.PresentViewArgs.model_validate(json.loads(
            '{"target": "pA", "kind": "section_sweep", "sweep": %s}' % sweep))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e308", "[1, 2, 3, 4]",
                                   "[NaN]"])
def test_a_keyframe_value_must_be_finite_bounded_and_short(value):
    with pytest.raises(ValueError):
        views.PresentViewArgs.model_validate(json.loads(
            '{"target": "pA", "kind": "animation", "animation": {"keyframes": '
            '[{"joint_id": "j", "to": %s}]}}' % value))


def test_more_than_twelve_keyframes_are_refused():
    steps = [{"joint_id": "j", "to": i} for i in range(13)]
    with pytest.raises(ValueError):
        views.AnimationRef(keyframes=steps)
    assert len(views.AnimationRef(keyframes=steps[:12]).keyframes) == 12


async def test_sweep_ends_far_outside_the_model_are_clamped_to_it(tmp_path):
    store, ctx, asm, joint = _hinge(tmp_path)
    out = await views.present_view.handler(ctx, views.PresentViewArgs.model_validate({
        "target": "pA", "kind": "section_sweep",
        "sweep": {"axis": "x", "from": -99999, "to": 99999}}))
    spec = out.data["view_spec"]
    # the model spans -20..60: 5% of it plus 1 mm either side
    assert spec["sweep"]["from"] == -25.0 and spec["sweep"]["to"] == 65.0
    json.dumps(spec, allow_nan=False)                     # strict JSON, as the HTTP routes


def test_saved_animations_are_capped_per_build(tmp_path):
    from mises.assembly import model
    asm = AssemblyStore(tmp_path / "a.db")
    for i in range(model.MAX_ANIMATIONS + 3):
        asm.save_animation("p", f"m{i}", {"steps": [], "loops": 1})
    names = [a["name"] for a in asm.list_animations("p")]
    assert len(names) == model.MAX_ANIMATIONS and names[0] == "m3" and names[-1] == "m52"
