"""Plan 9 G-A: the concept stage — sketch versions, the user's marks round-trip, the concept
gate on mirror_part_create, feedback to the lead with the annotated images, approve / skip,
and pitch choice."""
from __future__ import annotations

import io
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.concept import ConceptStore, FeedbackIn
from mises.concept import gate as gate_mod
from mises.concept import sketch as sk
from mises.concept import tools as ctools
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.store import Store
from mises.types import Caps, ErrorCode, ImageBlock, MisesError, SafetyClass as C, TextBlock

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _png(w=64, h=64, rgb=(238, 238, 240)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), rgb).save(buf, "PNG")
    return buf.getvalue()


def _image(view: str, rid: str) -> dict:
    return {"view": view, "label": sk.VIEW_LABEL[view], "render_id": rid, "width": 64,
            "height": 64, "camera": sk.camera_record(view, {"min": [0, 0, 0],
                                                            "max": [1, 1, 1]}, 64, "sketch")}


class Capture:
    """A provider that records every request's last user message and answers 'ok'."""
    name = "fake"

    def __init__(self):
        self.users: list[list] = []

    async def stream(self, req):
        last = req.messages[-1].content
        self.users.append(last if isinstance(last, list) else [TextBlock(text=last)])
        async for ev in FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]]
                                     ).stream(req):
            yield ev


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return tmp_path / "home"


def _wait_idle(client, rid, n=300):
    for _ in range(n):
        r = client.get(f"/runs/{rid}", headers=AUTH).json()
        if r["status"] in ("done", "failed", "cancelled"):
            time.sleep(0.05)
            return r
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def _seed_sketch(rt: Runtime, project: str, views=("three_quarter", "side")) -> dict:
    sid = "cks_" + "a" * 12 if not rt.concepts.sketches(project) else None
    from mises.concept import new_id
    sid = sid or new_id("cks")
    images = []
    for v in views:
        rid = ctools.save_png(rt.home, f"{sid}_{v}", _png())
        images.append(_image(v, rid))
    return rt.concepts.add_sketch(project, {"sketch_id": sid, "parent_sketch_id": None,
                                            "run_id": None, "images": images, "notes": "",
                                            "callouts": [], "overlay_svg": {}})


# ---- the store: versions, status

def test_sketch_versions_and_status(tmp_path):
    cs = ConceptStore(Store(tmp_path / "m.db"))
    assert cs.get("p1")["status"] == "none"
    a = cs.add_sketch("p1", {"sketch_id": "cks_" + "1" * 12, "images": []})
    b = cs.add_sketch("p1", {"sketch_id": "cks_" + "2" * 12, "images": [],
                             "parent_sketch_id": a["sketch_id"]})
    assert (a["version"], b["version"]) == (1, 2)
    assert b["parent_sketch_id"] == a["sketch_id"] and b["build_id"] == "p1"
    rec = cs.get("p1")
    assert rec["status"] == "sketching" and rec["sketch_ids"] == [a["sketch_id"], b["sketch_id"]]
    assert cs.latest_sketch("p1")["version"] == 2
    cs.update("p1", status="approved")
    cs.add_sketch("p1", {"sketch_id": "cks_" + "3" * 12, "images": []})
    assert cs.get("p1")["status"] == "approved"          # a later re-sketch never re-closes
    assert cs.add_sketch("p2", {"sketch_id": "cks_" + "4" * 12, "images": []})["version"] == 1
    with pytest.raises(MisesError):
        cs.update("p1", status="nonsense")


def test_marks_validate_normalised_coordinates():
    ok = FeedbackIn.model_validate({"marks": [
        {"kind": "circle", "cx": 0.5, "cy": 0.4, "r": 0.1, "comment": "slimmer"},
        {"kind": "lasso", "points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.3]], "image": 1},
        {"kind": "pin", "x": 0.9, "y": 0.2}]})
    assert [m.kind for m in ok.marks] == ["circle", "lasso", "pin"]
    for bad in ({"kind": "pin", "x": 1.4, "y": 0.2},
                {"kind": "circle", "cx": 0.5, "cy": 0.5, "r": 0},
                {"kind": "lasso", "points": [[0.1, 0.1], [0.2, 0.2]]},
                {"kind": "arrow", "x": 0.1, "y": 0.1}):
        with pytest.raises(ValueError):
            FeedbackIn.model_validate({"marks": [bad]})


# ---- the gate and the design class

def test_design_class_reads_the_lead_then_falls_back(monkeypatch):
    lead = SimpleNamespace(id="r1", task="a sleek humanoid", design_class={
        "score": 0.8, "cls": "design", "reasons": ["humanoid"]})
    monkeypatch.setattr("mises.orchestrator.lead.find_lead", lambda store, pid: lead)
    assert gate_mod.design_class(None, "p")["cls"] == "design"
    lead.design_class = None
    monkeypatch.setattr(gate_mod, "_classify", lambda task: None)
    assert gate_mod.design_class(None, "p") == {"cls": "mixed", "source": "fallback"}
    monkeypatch.setattr(gate_mod, "_classify", lambda task: {"cls": "engineering"})
    assert gate_mod.design_class(None, "p")["cls"] == "engineering"
    assert gate_mod.required("design") == "pitch" and gate_mod.required("engineering") == "off"


def _gate_rt(home, cls):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    project = rt.store.ensure_default_project().id
    run = rt.store.create_run("engineer", "fake", "m", "a sleek humanoid robot")
    rt.store.update_run(run.id, project_id=project, role="lead")
    rt.store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
    return rt, project, run


def _ctx(rt, run, workdir):
    return SimpleNamespace(run_id=run.id, store=rt.store, services=rt.gate.services,
                           workdir=workdir)


@pytest.mark.parametrize("cls,refused", [("design", True), ("mixed", False),
                                         ("engineering", False)])
def test_gate_refuses_new_parts_for_design_until_approved(home, monkeypatch, cls, refused):
    rt, project, run = _gate_rt(home, cls)
    monkeypatch.setattr(gate_mod, "design_class", lambda store, pid, rid=None: {"cls": cls})
    ctx = _ctx(rt, run, rt.home)
    args = SimpleNamespace(part_id="torso_shell")
    if refused:
        with pytest.raises(MisesError) as e:
            gate_mod.check_call(ctx, "mirror_part_create", args)
        assert e.value.code == ErrorCode.NEEDS_APPROVAL
        assert e.value.message.startswith("needs concept approval")
    else:
        gate_mod.check_call(ctx, "mirror_part_create", args)
    # no name is exempt: massing_* / concept_* parts are parts like any other
    for pid in ("massing_body", "concept_torso", "massing"):
        if refused:
            with pytest.raises(MisesError):
                gate_mod.check_call(ctx, "mirror_part_create", SimpleNamespace(part_id=pid))
        else:
            gate_mod.check_call(ctx, "mirror_part_create", SimpleNamespace(part_id=pid))
    # an Onshape import starts a part too
    if refused:
        with pytest.raises(MisesError):
            gate_mod.check_call(ctx, "onshape_import_element", SimpleNamespace(url="x"))
    # a part id that exists in ANOTHER build is not a rebuild
    other = rt.store.create_project("Other build").id
    rt.store.register_part("leg_fl", other)
    (rt.home / "parts" / "leg_fl").mkdir(parents=True)
    (rt.home / "parts" / "leg_fl" / "metrics.json").write_text("{}")
    if refused:
        with pytest.raises(MisesError):
            gate_mod.check_call(ctx, "mirror_part_create", SimpleNamespace(part_id="leg_fl"))
    # other tools, and rebuilds of a part of THIS build, always pass
    gate_mod.check_call(ctx, "mirror_part_set_vars", args)
    rt.store.register_part("old_part", project)
    gate_mod.check_call(ctx, "mirror_part_create", SimpleNamespace(part_id="old_part"))
    # approve (or skip) opens the gate
    rt.concepts.update(project, status="approved")
    gate_mod.check_call(ctx, "mirror_part_create", args)
    rt.concepts.update(project, status="sketching")
    if refused:
        with pytest.raises(MisesError):
            gate_mod.check_call(ctx, "mirror_part_create", args)
    rt.concepts.update(project, status="skipped")
    gate_mod.check_call(ctx, "mirror_part_create", args)


@pytest.mark.parametrize("where", ["run", "build"])
async def test_the_gate_reads_design_class_from_the_lead_or_the_build(home, where):
    """Wave-3 contract: G-B stores the class as `design_class` (JSON) on the lead's run (and/or
    the build). Set it as G-B's migration will, with no monkeypatch: new parts are refused."""
    rt, project, run = _gate_rt(home, "design")
    table, row = ("runs", run.id) if where == "run" else ("projects", project)
    with rt.store._tx() as db:
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if "design_class" not in cols:          # G-B's migration adds it on runs
            db.execute(f"ALTER TABLE {table} ADD COLUMN design_class TEXT")
        db.execute(f"UPDATE {table} SET design_class=? WHERE id=?",
                   ('{"score": 0.82, "cls": "design", "per_part": {}, "reasons": ["humanoid"]}', row))
    cls = gate_mod.design_class(rt.store, project, run.id)
    assert cls["cls"] == "design" and cls["source"] == where
    with pytest.raises(MisesError) as e:
        await rt.gate.call(run.id, "mirror_part_create",
                           {"part_id": "massing_torso", "script": "VARS={}\ndef build(v): pass"},
                           call_id="c1", workdir=rt.home)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL
    assert "massing_" not in (e.value.hint or "")
    rt.concepts.update(project, status="approved")
    gate_mod.check_call(_ctx(rt, run, rt.home), "mirror_part_create",
                        SimpleNamespace(part_id="massing_torso"))


def test_design_class_on_an_object_attribute_too(monkeypatch):
    lead = SimpleNamespace(id="r1", task="t", design_class="design")
    monkeypatch.setattr("mises.orchestrator.lead.find_lead", lambda store, pid: lead)
    assert gate_mod.design_class(None, "p")["cls"] == "design"


def test_pitch_cap(tmp_path):
    from mises.concept import MAX_PITCHES
    cs = ConceptStore(Store(tmp_path / "m.db"))
    for i in range(MAX_PITCHES):
        cs.add_pitch("p1", {"pitch_id": f"ckp_{i:012x}", "options": []})
    with pytest.raises(MisesError):
        cs.add_pitch("p1", {"pitch_id": "ckp_ffffffffffff", "options": []})
    assert cs.add_pitch("p2", {"pitch_id": "ckp_eeeeeeeeeeee", "options": []})


async def test_concept_status_stays_under_the_gate_output_cap(home):
    """Many long rounds: the latest round comes back in full (comments cut), older rounds as
    summaries, and the result is never spilled to a file."""
    from mises.gate import MAX_OUTPUT_BYTES
    rt, project, run = _gate_rt(home, "design")
    sketch = _seed_sketch(rt, project)
    for k in range(4):
        rt.concepts.add_feedback(project, sketch["sketch_id"], {
            "feedback_id": f"ckf_{k:012x}", "version": 1, "comment": "z" * 4000, "annotated": [],
            "marks": [{"kind": "pin", "x": 0.5, "y": 0.5, "image": 0, "n": n + 1, "view": "side",
                       "comment": "<" + "y" * 999} for n in range(40)]})
    out = await rt.gate.call(run.id, "concept_status", {}, call_id="st", workdir=rt.home)
    assert out.handles == [] and out.data["concept"]["status"] == "sketching"
    assert len(out.model_dump_json(exclude={"images"}).encode()) <= MAX_OUTPUT_BYTES
    block = out.data["untrusted_user_feedback"]
    assert '"rounds_total": 4' in block and "ckf_000000000003" in block
    assert "<y" not in block                                   # still escaped inside the fence


async def test_feedback_images_are_fitted_for_the_lead():
    from mises.concept.service import ATTACH_MAX_BYTES, fit_attachment
    from PIL import Image
    rng = np.random.default_rng(0)
    buf = io.BytesIO()
    Image.fromarray(rng.integers(0, 255, (2600, 2600, 3), dtype=np.uint8)).save(buf, "PNG")
    assert len(buf.getvalue()) > ATTACH_MAX_BYTES
    out = await fit_attachment(buf.getvalue())
    w, h = Image.open(io.BytesIO(out)).size
    assert max(w, h) <= 1024 and len(out) < ATTACH_MAX_BYTES


async def test_the_gate_pipeline_runs_the_concept_check(home, monkeypatch):
    rt, project, run = _gate_rt(home, "design")
    monkeypatch.setattr(gate_mod, "design_class", lambda store, pid, rid=None: {"cls": "design"})
    with pytest.raises(MisesError) as e:
        await rt.gate.call(run.id, "mirror_part_create",
                           {"part_id": "torso_shell", "script": "VARS={}\ndef build(v): pass"},
                           call_id="c1", workdir=rt.home)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL
    assert not (rt.home / "parts" / "torso_shell").exists()     # refused before the handler
    assert "concept_sketch" in [t.name for t in rt.registry.list()]


# ---- the tools (real sandboxed build + render)

MASSING = '''VARS = {}
def build(v):
    body = Pos(0, 0, 600) * Box(360, 220, 700)
    head = Pos(0, 0, 1080) * Sphere(120)
    return body + head
'''


async def test_concept_sketch_builds_renders_and_versions(home):
    rt, project, run = _gate_rt(home, "design")
    sub = rt.events.subscribe(run.id)
    out = await rt.gate.call(run.id, "concept_sketch", {
        "massing_script": MASSING, "notes": "first massing",
        "callouts": [{"view": "side", "x": 0.1, "y": 0.1, "text": "<b>tall</b> head",
                      "to": [0.5, 0.2]}]}, call_id="s1", workdir=rt.home)
    assert out.ok and out.data["version"] == 1 and out.data["views"] == ["three_quarter", "side"]
    sk1 = rt.concepts.get_sketch(out.data["sketch_id"])
    assert sk1["parent_sketch_id"] is None and sk1["build_id"] == project
    for img in sk1["images"]:
        png = ctools.read_png(rt.home, img["render_id"])
        assert png and png.startswith(b"\x89PNG") and len(png) < 1_000_000
        assert img["width"] == sk.SKETCH_SIZE
        cam = img["camera"]
        assert cam["style"] == "sketch" and len(cam["forward"]) == 3
        assert cam["projection"] == ("perspective" if img["view"] == "three_quarter"
                                     else "orthographic")
    assert sk1["scale"]["figure"] is True                       # 1.2 m tall: person-sized
    svg = sk1["overlay_svg"]["side"]
    assert "&lt;b&gt;tall&lt;/b&gt;" in svg and "<b>" not in svg  # text escaped, never markup
    types = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        types.append(ev.type)
        if ev.type == "concept_sketch":
            assert ev.data["stream"] == "view" and ev.data["version"] == 1
    assert {"concept_sketch", "concept_updated", "agent_announce"} <= set(types)
    out2 = await rt.gate.call(run.id, "concept_sketch", {
        "massing_script": MASSING, "views": ["front"], "scale_figure": "off"},
        call_id="s2", workdir=rt.home)
    sk2 = rt.concepts.get_sketch(out2.data["sketch_id"])
    assert sk2["version"] == 2 and sk2["parent_sketch_id"] == sk1["sketch_id"]
    assert sk2["scale"]["figure"] is False
    assert rt.concepts.get(project)["status"] == "sketching"
    status = await rt.gate.call(run.id, "concept_status", {}, call_id="s3", workdir=rt.home)
    assert [s["version"] for s in status.data["sketches"]] == [1, 2]


async def test_pitch_options_rules_and_cards(home):
    rt, project, run = _gate_rt(home, "design")
    opt = {"mood": ["calm", "soft", "white"], "massing_script": MASSING,
           "cmf": [{"name": "Shell", "color": "#f2f2ee", "finish": "gloss"},
                   {"name": "Joints", "color": "#2a2c30", "finish": "matte"}],
           "tradeoffs": {"mass_delta": "+8%", "part_count": 24, "molding": "2-part shells",
                         "cost_tier": "$$", "risk": "hip cover pinch"}}
    with pytest.raises(MisesError):          # no baseline
        await rt.gate.call(run.id, "pitch_options", {"options": [
            {**opt, "name": "A"}, {**opt, "name": "B"}]}, call_id="p0", workdir=rt.home)
    out = await rt.gate.call(run.id, "pitch_options", {"options": [
        {**opt, "name": "Monolith", "recommended": True, "reason": "calmest read"},
        {**opt, "name": "Honest", "baseline": True}]}, call_id="p1", workdir=rt.home)
    assert out.ok and out.data["baseline"] == "Honest"
    pitch = rt.concepts.get_pitch(out.data["pitch_id"])
    o = pitch["options"][0]
    assert o["sketch"]["view"] == "three_quarter" and set(o["silhouettes"]) == {"front", "side"}
    sil = ctools.read_png(rt.home, o["silhouettes"]["front"])
    from PIL import Image
    a = np.asarray(Image.open(io.BytesIO(sil)))
    assert a.shape[2] == 4 and (a[..., 3] > 0).mean() > 0.05      # a black fill, not empty
    assert rt.concepts.get(project)["status"] == "pitching"


# ---- the app's routes: feedback round-trip, approve, skip, choose

def test_feedback_round_trip_reaches_the_lead_with_marked_images(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        r = client.post("/runs", headers=AUTH, json={"prompt": "a sleek humanoid robot",
                                                     "model_ref": "anthropic/claude-opus-5-5"})
        lead = r.json()["run_id"]
        _wait_idle(client, lead)
        project = rt.store.get_run(lead).project_id
        sketch = _seed_sketch(rt, project)
        body = {"comment": "overall a bit bulky", "marks": [
            {"kind": "circle", "cx": 0.5, "cy": 0.3, "r": 0.1, "comment": "smaller head"},
            {"kind": "lasso", "image": 1, "comment": "slimmer waist",
             "points": [[0.3, 0.4], [0.5, 0.42], [0.45, 0.6]]},
            {"kind": "pin", "x": 0.2, "y": 0.8, "comment": "<script>alert(1)</script>"}]}
        r = client.post(f"/concept/sketches/{sketch['sketch_id']}/feedback", headers=AUTH,
                        json=body)
        assert r.status_code == 200, r.text
        fb = r.json()
        assert fb["run_id"] == lead and fb["text"] == "Sketch feedback v1: 3 marks and a comment…"
        assert len(fb["annotated_ids"]) == 2 and fb["images"] == 2
        _wait_idle(client, lead)
        # stored per image, normalised, with the image's camera
        got = client.get(f"/concept/sketches/{sketch['sketch_id']}", headers=AUTH).json()
        marks = got["feedback"][0]["marks"]
        assert [m["n"] for m in marks] == [1, 2, 3]
        assert marks[0]["cx"] == 0.5 and marks[0]["view"] == "three_quarter"
        assert marks[1]["view"] == "side" and marks[1]["camera"]["view"] == "side"
        assert marks[2]["comment"] == "<script>alert(1)</script>"
        # the marked image differs from the clean sketch (blue marks drawn on it)
        from PIL import Image
        marked = np.asarray(Image.open(io.BytesIO(ctools.read_png(rt.home,
                                                                  fb["annotated_ids"][0]))))
        blue = (marked[..., 2] > 200) & (marked[..., 0] < 120)
        assert blue.sum() > 50
        # the lead's message: the annotated images first, then the text with the data block
        msg = prov.users[-1]
        imgs = [b for b in msg if isinstance(b, ImageBlock)]
        texts = [b.text for b in msg if isinstance(b, TextBlock)]
        assert len(imgs) == 2 and isinstance(msg[0], ImageBlock)
        assert "Sketch feedback" not in texts[0] and "revised concept_sketch" in texts[0]
        assert '"smaller head"' in texts[0] and "\\u003cscript" in texts[0]
        assert "<script>" not in texts[0]
        # the thread shows the short line, not the wrapped block
        steps = [s.payload for s in rt.store.steps(lead) if s.payload.get("role") == "user"]
        assert steps[-1]["text"] == "Sketch feedback v1: 3 marks and a comment…"
        assert steps[-1]["concept_feedback"]["marks"] == 3 and steps[-1]["images"] == 2
        # concept_status gives the marks back as data (in the untrusted block)
        view = client.get(f"/projects/{project}/concept", headers=AUTH).json()
        assert view["sketches"][0]["feedback"][0]["feedback_id"] == fb["feedback_id"]
        assert view["design_class"]["cls"] in ("engineering", "mixed", "design")


def test_feedback_refuses_bad_input(home):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    project = rt.store.ensure_default_project().id
    sketch = _seed_sketch(rt, project, views=("side",))
    with TestClient(create_app(rt, TOKEN)) as client:
        url = f"/concept/sketches/{sketch['sketch_id']}/feedback"
        assert client.post(url, headers=AUTH, json={}).status_code == 400
        r = client.post(url, headers=AUTH, json={"marks": [{"kind": "pin", "x": .1, "y": .1,
                                                            "image": 3}]})
        assert r.status_code == 400 and "image 3" in r.json()["message"]
        assert client.post(url, headers=AUTH, json={"marks": [
            {"kind": "pin", "x": 2, "y": .1}]}).status_code == 422
        assert client.get("/concept/sketches/cks_000000000000", headers=AUTH).status_code == 404


def test_approve_and_skip_open_the_gate_and_tell_the_lead(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead = client.post("/runs", headers=AUTH, json={"prompt": "a car body"}).json()["run_id"]
        _wait_idle(client, lead)
        project = rt.store.get_run(lead).project_id
        r = client.post(f"/projects/{project}/concept/approve", headers=AUTH, json={})
        assert r.status_code == 400                                  # nothing to approve yet
        s1 = _seed_sketch(rt, project)
        _seed_sketch(rt, project)
        r = client.post(f"/projects/{project}/concept/approve", headers=AUTH,
                        json={"sketch_id": s1["sketch_id"]})
        assert r.status_code == 200, r.text
        rec = r.json()["concept"]
        assert rec["status"] == "approved" and rec["approved_sketch_id"] == s1["sketch_id"]
        _wait_idle(client, lead)
        assert "approved concept sketch v1" in prov.users[-1][0].text
        r = client.post(f"/projects/{project}/concept/skip", headers=AUTH)
        assert r.json()["concept"]["status"] == "skipped"


def test_approval_drops_the_concept_messages_still_queued_for_the_lead(home):
    """DOG demo: the user picked a pitch and marked sketch v1 while the lead was busy; it
    approved v3 later, and the lead, on reading the stale "Chose Whippet" after finishing the
    build, started re-sketching. An approval supersedes queued picks and sketch marks."""
    from mises.concept import service
    from mises.orchestrator import lead as lead_mod
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    lid = rt.create_external_run("engineer", provider="ui", model="m", task="dog")
    rt.lead_queues[lid] = [
        {"message_id": "msg_a", "text": "t", "shown": "Chose Whippet",
         "log": {"text": "Chose Whippet", "concept_choice": {"pitch_id": "p", "label": "W"}}},
        {"message_id": "msg_b", "text": "t", "shown": "Sketch feedback v1",
         "log": {"text": "Sketch feedback v1", "concept_feedback": {"sketch_id": "s"}}},
        {"message_id": "msg_c", "text": "and make it orange", "shown": "and make it orange",
         "log": None}]
    dropped = service.drop_superseded(rt, lid)
    assert dropped == ["msg_a", "msg_b"]
    assert [m["message_id"] for m in lead_mod.queued_messages(rt, lid)] == ["msg_c"]


def test_choose_a_pitch_option_or_a_mix(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead = client.post("/runs", headers=AUTH, json={"prompt": "a robot dog"}).json()["run_id"]
        _wait_idle(client, lead)
        project = rt.store.get_run(lead).project_id
        p = rt.concepts.add_pitch(project, {"pitch_id": "ckp_" + "b" * 12, "options": [
            {"name": "Athlete"}, {"name": "Honest", "baseline": True}], "chosen": None})
        url = f"/concept/pitches/{p['pitch_id']}/choose"
        assert client.post(url, headers=AUTH, json={"option": "Nope"}).status_code == 400
        r = client.post(url, headers=AUTH, json={"mix": [
            {"option": "Athlete", "take": "legs"}, {"option": "Honest", "take": "body"}],
            "comment": "keep it cheap"})
        assert r.status_code == 200, r.text
        assert r.json()["concept"]["chosen_option"] == "Athlete's legs + Honest's body"
        assert r.json()["concept"]["status"] == "sketching"
        assert rt.concepts.get_pitch(p["pitch_id"])["chosen"]["comment"] == "keep it cheap"
        _wait_idle(client, lead)
        assert "concept_sketch the chosen direction" in prov.users[-1][0].text


def test_lead_prompt_and_tools_carry_the_concept_stage():
    from mises.orchestrator.lead import LEAD_PROMPT, LEAD_TOOLS
    for t in ("pitch_options", "concept_sketch", "concept_status"):
        assert t in LEAD_TOOLS
    assert "Concept first" in LEAD_PROMPT and "Never parts first" in LEAD_PROMPT
