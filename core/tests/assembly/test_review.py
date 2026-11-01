"""E33 complete: (b) health overlaps are real OCCT interference volumes (bbox only prefilters),
(a) a whole-assembly render with problem parts tinted reaches the lead, (c) the turn-end finish
check warns a lead that reports done while the assembly has errors."""
import io
import json

import pytest

import mises.runtime as runtime_mod
from mises.agent.loop import RunResult
from mises.assembly import review
from mises.assembly.geometry import GeometryResult
from mises.assembly.model import AssemblyStore, Frame, Instance, Joint, JointRef
from mises.cad.render_job import RenderOut
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.types import StepKind, ToolImage, ToolOutput

BOX = {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]}
Z = Frame(origin=[0, 0, 0], axis=[0, 0, 1])


def _tf(x):
    return [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def _build(tmp_path, xs, *, stl=False):
    root = tmp_path / "parts"
    (root / "box").mkdir(parents=True)
    (root / "box" / "metrics.json").write_text(json.dumps({"bbox": BOX}))
    if stl:
        (root / "box" / "model.stl").write_bytes(b"solid x\nendsolid x\n")
    store = AssemblyStore(tmp_path / "asm.db")
    ids = []
    for n, x in enumerate(xs):
        inst = store.add_instance("p", Instance(id=f"i{n}", part_id="box", name=f"box{n}",
                                                transform=_tf(x), fixed=n == 0))
        ids.append(inst.id)
    return store, root, ids


def _png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(b, "PNG")
    return b.getvalue()


# ---- (b) OCCT refines the bbox candidates
async def test_bbox_overlap_is_replaced_by_real_interference(tmp_path, monkeypatch):
    store, root, _ = _build(tmp_path, [0, 3])          # boxes overlap by bbox
    calls = []

    async def occt(root_, asm, res, **kw):
        calls.append(kw.get("want"))
        return GeometryResult(interferences=[{"a": "box0", "b": "box1", "volume_mm3": 12.5}])
    monkeypatch.setattr(review, "run_geometry", occt)
    rep = await review.assembly_report(store, root, "p")
    assert calls == ["interference"] and rep["overlap_method"] == "occt"
    assert rep["overlaps"][0]["volume_mm3"] == 12.5 and rep["overlaps"][0]["a_id"] == "i0"
    assert not rep["ok"]


async def test_bbox_overlap_without_real_contact_is_clean(tmp_path, monkeypatch):
    store, root, ids = _build(tmp_path, [0, 3])
    store.add_joint("p", Joint(kind="fixed", a=JointRef(instance_id=ids[0], frame=Z),
                               b=JointRef(instance_id=ids[1], frame=Z)))

    async def occt(*a, **k):
        return GeometryResult(interferences=[])       # the solids do not actually intersect
    monkeypatch.setattr(review, "run_geometry", occt)
    rep = await review.assembly_report(store, root, "p")
    assert rep["overlaps"] == [] and rep["ok"]


async def test_no_bbox_overlap_runs_no_occt(tmp_path, monkeypatch):
    store, root, _ = _build(tmp_path, [0, 40])

    async def boom(*a, **k):
        raise AssertionError("OCCT must not run without a bbox candidate")
    monkeypatch.setattr(review, "run_geometry", boom)
    rep = await review.assembly_report(store, root, "p")
    assert rep["overlaps"] == [] and rep["overlap_method"] == "bbox"


# ---- (a) the whole-assembly render
async def test_overview_render_frames_all_and_tints_problems(tmp_path, monkeypatch):
    store, root, ids = _build(tmp_path, [0, 3, 6, 900], stl=True)     # i3 is far away
    seen = {}

    async def render(jobs, meshes, *, renders=(), **kw):
        seen["meshes"], seen["renders"] = meshes, renders
        return RenderOut(pngs={"asm": {"iso": _png(), "top": _png()}})
    import mises.cad.render_job as rj
    monkeypatch.setattr(rj, "run_render_job", render)
    report = {"overlaps": [{"a": "box0", "b": "box1", "a_id": "i0", "b_id": "i1",
                            "volume_mm3": 4.0}],
              "stray": [{"instance": "box3", "instance_id": "i3", "distance_mm": 890.0}],
              "floating": [], "ok": False, "summary": "x"}
    imgs = await review.overview_images(tmp_path, store, "p", report)
    assert len(imgs) == 2 and all(i.media_type == "image/png" for i in imgs)
    assert "red = overlap" in imgs[0].label and "orange = stray" in imgs[0].label
    assert len(seen["meshes"]) == 4                       # the stray is in the frame too
    colors = [m.color for m in seen["meshes"]]
    assert colors[0] == colors[1] == review._RED and colors[3] == review._ORANGE
    assert seen["renders"][0]["size"] == 1024 and len(seen["renders"][0]["views"]) <= 2


# ---- (c) the finish check, and the render handoff
@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    prov = FakeProvider([[TextDelta(text="Done: the arm is complete."),
                          Done(stop_reason="end_turn")],
                         [TextDelta(text="fixing"), Done(stop_reason="end_turn")]])
    return Runtime(tmp_path / "home", provider_resolver=lambda ref: (prov, "m"))


BAD = {"ok": False, "summary": "assembly health: 1 overlapping pair(s)", "stray": [],
       "floating": [], "overlaps": [{"a": "arm", "b": "base", "a_id": "x", "b_id": "y",
                                     "volume_mm3": 30.0}], "overlap_method": "occt"}


def _warnings(rt, run_id):
    return [s.payload for s in rt.store.steps(run_id, [StepKind.VERIFY])
            if s.payload.get("kind") == "engineering_checks"]


def _patch(monkeypatch, report):
    async def rep(*a, **k):
        return report

    async def imgs(*a, **k):
        return [ToolImage(media_type="image/png", data_b64="iVBORw0KGgo=", label="whole")]
    monkeypatch.setattr(review, "assembly_report", rep)
    monkeypatch.setattr(review, "overview_images", imgs)


async def test_done_claim_with_bad_health_warns_and_notes_the_lead(rt, monkeypatch):
    from mises.orchestrator import lead as lead_mod
    pid = rt.active_project_id()
    lead = (await lead_mod.submit(rt, "Build an arm", project_id=pid))["run_id"]
    await rt.wait(lead)
    _patch(monkeypatch, BAD)
    sent = {}

    async def fake_note(rt_, run_id, text, meta, **kw):
        sent.update(run_id=run_id, text=text, meta=meta, images=kw.get("images"))
    monkeypatch.setattr(lead_mod, "deliver_note", fake_note)
    rt.mark_assembly_changed(lead, pid)
    await rt._after_turn(lead, "Done: the arm is complete.")
    warn = _warnings(rt, lead)
    assert len(warn) == 1 and warn[0]["assembly_health"]["overlaps"]
    assert "not clean" in warn[0]["warning"]
    assert sent["run_id"] == lead and "not clean" in sent["text"] and sent["images"]
    assert rt.store.get_run(lead).status != "failed"          # the user is never blocked


async def test_no_done_claim_hands_render_to_next_tool_result(rt, monkeypatch):
    pid = rt.active_project_id()
    run = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    _patch(monkeypatch, BAD)
    rt.mark_assembly_changed(run.id, pid)
    await rt._after_turn(run.id, "I moved the arm; next I will mate it.")
    assert _warnings(rt, run.id) == []
    out = rt.gate._with_assembly_render(run.id, ToolOutput(summary="x"))
    assert len(out.images) == 1 and "render" in out.warnings[-1]
    assert rt.gate._with_assembly_render(run.id, ToolOutput(summary="y")).images == []


async def test_clean_health_never_warns(rt, monkeypatch):
    pid = rt.active_project_id()
    run = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    _patch(monkeypatch, {**BAD, "ok": True, "overlaps": [], "summary": "assembly health: clean"})
    rt.mark_assembly_changed(run.id, pid)
    await rt._after_turn(run.id, "Done, complete.")
    assert _warnings(rt, run.id) == []


async def test_turn_end_schedules_review_only_for_a_changed_root(rt, monkeypatch):
    pid = rt.active_project_id()
    root = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    sub = rt.store.create_run("designer", "fake", "m", "t", parent_id=root.id, project_id=pid)
    ran = []

    async def after(run_id, text, pid=None):
        ran.append(run_id)
    monkeypatch.setattr(rt, "_after_turn", after)
    ok = RunResult(final_text="Done", messages=[], stop_reason="end_turn")
    rt._schedule_after_turn(root.id, ok)                       # nothing changed: no review
    rt.mark_assembly_changed(sub.id, pid)                      # a sub's change marks its root
    assert root.id in rt.assembly_touched
    rt._schedule_after_turn(sub.id, ok)                        # a sub never reviews
    rt._schedule_after_turn(root.id, ok)
    for t in list(rt._after_turn_tasks):
        await t
    assert ran == [root.id]


def test_claims_done():
    assert review.claims_done("All set — the gripper is built.")
    assert not review.claims_done("I moved the base; mating the arm next.")


async def test_c3_gate_and_assembly_health_make_one_warning_step(rt, monkeypatch):
    # a lead whose build has a moving joint (C3's checks not run) AND reports done with an
    # overlap: ONE combined VERIFY step, carrying both the engineering text and the health
    from mises.orchestrator import lead as lead_mod
    pid = rt.active_project_id()
    lead = (await lead_mod.submit(rt, "Build an arm", project_id=pid))["run_id"]
    await rt.wait(lead)
    for t in list(rt._after_turn_tasks):
        await t
    asm = rt.assembly_store
    a = asm.add_instance(pid, Instance(part_id="base", name="base", fixed=True))
    b = asm.add_instance(pid, Instance(part_id="arm", name="arm"))
    asm.add_joint(pid, Joint(kind="revolute", a=JointRef(instance_id=a.id, frame=Z),
                             b=JointRef(instance_id=b.id, frame=Z)))
    _patch(monkeypatch, BAD)

    async def fake_note(*a, **k):
        return None
    monkeypatch.setattr(lead_mod, "deliver_note", fake_note)
    before = len(_warnings(rt, lead))
    rt.mark_assembly_changed(lead, pid)
    await rt._after_turn(lead, "Done: the arm is complete.")
    new = _warnings(rt, lead)[before:]
    assert len(new) == 1
    assert "Engineering checks are not clear" in new[0]["warning"]
    assert "assembly is not clean" in new[0]["warning"] and new[0]["assembly_health"]
    # the fresh health verdict went into C3's pipeline
    from mises.engineering import pipeline
    assert pipeline.load(rt.home, pid)["assembly_health"]["status"] == "fail"


async def test_turn_without_assembly_change_keeps_the_c3_step_alone(rt, monkeypatch):
    from mises.orchestrator import lead as lead_mod
    pid = rt.active_project_id()
    lead = (await lead_mod.submit(rt, "Build an arm", project_id=pid))["run_id"]
    await rt.wait(lead)
    asm = rt.assembly_store
    a = asm.add_instance(pid, Instance(part_id="base", name="base", fixed=True))
    b = asm.add_instance(pid, Instance(part_id="arm", name="arm"))
    asm.add_joint(pid, Joint(kind="revolute", a=JointRef(instance_id=a.id, frame=Z),
                             b=JointRef(instance_id=b.id, frame=Z)))
    before = len(_warnings(rt, lead))
    rt._finish_checks(lead, RunResult(final_text="Done", messages=[], stop_reason="end_turn"))
    new = _warnings(rt, lead)[before:]
    assert len(new) == 1 and "assembly_health" not in new[0]


async def test_assembly_health_tool_reports_and_flags(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mises.assembly.tools import HealthArgs, assembly_health
    from mises.events import EventBus
    store, root, _ = _build(tmp_path, [0, 3, 6, 900])      # an overlap candidate and a stray

    async def occt(*a, **k):
        return GeometryResult(interferences=[{"a": "box0", "b": "box1", "volume_mm3": 8.0}])
    monkeypatch.setattr(review, "run_geometry", occt)
    ctx = SimpleNamespace(workdir=tmp_path, events=EventBus(), services={"assembly": store},
                          run_id=None)
    out = await assembly_health.handler(ctx, HealthArgs(project_id="p", render=False))
    rep = out.data["assembly_health"]
    assert not rep["ok"] and rep["overlap_method"] == "occt"
    assert [s["instance"] for s in rep["stray"]] == ["box3"]
    assert any("overlaps" in w for w in out.warnings) and any("box3" in w for w in out.warnings)
