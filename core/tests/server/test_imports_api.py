"""POST /imports/drawing: DXF (parsed, exact), PNG / PDF (a scripted fake vision model reads the
drawing into a DrawingSpec), the verify table, the 3-attempt loop and the upload checks."""
import io
import json
import re
import time
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

from mises.providers.base import Done, TextDelta, ToolCall
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.types import ImageBlock, MisesError, TextBlock

from .conftest import AUTH, TOKEN

# real sandboxed parse, build and drawing subprocesses (several per test)
pytestmark = [pytest.mark.slow, pytest.mark.timeout(180)]

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "drawings"
HOLES = [(15, 15), (85, 15), (15, 45), (85, 45)]


class ScriptedVision:
    """A fake vision model: each turn is a function of the request (it reads the import id from
    the context block, as a real model would) returning that turn's events."""
    name = "anthropic"                  # a vision-capable provider name

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.look_requests = []         # the second-look calls (their own script: `look`)
        self.look = '{"mismatches": [], "summary": "The two drawings agree."}'

    async def stream(self, req):
        if req.system.startswith("You check a part built from a technical drawing"):
            self.look_requests.append(req)
            yield TextDelta(text=self.look)
            yield Done(stop_reason="end_turn")
            return
        self.requests.append(req)
        events = self.turns.pop(0)(req) if self.turns else [TextDelta(text="(no more turns)")]
        for ev in events:
            yield ev
        if not any(isinstance(e, Done) for e in events):
            yield Done(stop_reason="end_turn")


def import_id_of(req) -> str:
    first = req.messages[0].content[0]
    return re.search(r'"import_id":\s*"(imp_[0-9a-f]{12})"', first.text).group(1)


def plate_spec(width=60.0, hole_d=6.0, stated_width=60.0) -> dict:
    return {"units": "mm", "title": "MOUNTING PLATE", "thickness": 6,
            "outline": [{"polyline": [[0, 0], [100, 0], [100, width], [0, width], [0, 0]]}],
            "holes": [{"x": x, "y": y, "diameter": hole_d} for x, y in HOLES],
            "dimensions": [{"id": "L", "kind": "length", "value": 100, "text": "100"},
                           {"id": "W", "kind": "width", "value": stated_width,
                            "text": f"{stated_width:g}"},
                           {"id": "T", "kind": "thickness", "value": 6, "text": "THK 6"},
                           {"id": "D", "kind": "diameter", "value": 6, "hole": 0,
                            "text": "4X Ø6 THRU"}],
            "notes": ["AL 6061"]}


def submit(spec_fn, call_id="c1"):
    def turn(req):
        return [ToolCall(id=call_id, name="drawing_spec_submit",
                         input={"import_id": import_id_of(req), "spec": spec_fn()}),
                Done(stop_reason="tool_use")]
    return turn


def say(text):
    return lambda req: [TextDelta(text=text)]


@pytest.fixture
def model():
    return ScriptedVision([])


@pytest.fixture
def rt(home, model):
    return Runtime(home, provider_resolver=lambda ref: (model, "fake-vision"))


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def post(client, name: str, data: bytes, **form):
    return client.post("/imports/drawing", headers=AUTH, files={"file": (name, data)},
                       data={k: str(v) for k, v in form.items()})


def wait_import(client, iid, timeout=150.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/imports/{iid}", headers=AUTH).json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"import {iid} never finished: {r}")


def test_routes_need_token(client):
    assert client.post("/imports/drawing", files={"file": ("a.dxf", b"x")}).status_code == 401
    assert client.get("/imports/imp_000000000000").status_code == 401


def test_dxf_plate_gives_the_exact_part(client, rt):
    events = rt.events.subscribe()
    r = post(client, "Mounting Plate.dxf", (FIX / "plate_holes.dxf").read_bytes())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "dxf" and out["import_id"].startswith("imp_")
    done = wait_import(client, out["import_id"])
    assert done["status"] == "done" and done["result"] == "match", json.dumps(done)[:3000]
    assert done["part_id"] == "mounting_plate" and done["attempts"] == 1
    rows = {r["id"]: r for r in done["table"]["rows"]}
    kinds = sorted(r["kind"] for r in rows.values())
    assert kinds.count("length") == 1 and kinds.count("width") == 1
    assert "diameter" in kinds and "position_x" in kinds and "hole_spacing_x" in kinds
    assert all(r["status"] == "match" for r in rows.values()), rows
    assert rows["T"]["measured"] == 6 and rows["T"]["text"] == "(note)"     # "THK 6" note
    assert any(r["id"] == "callout:4x Ø6 THRU" for r in rows.values())
    # the exact part: 100 x 60 x 6, R5 corner, 4x Ø6 + Ø10 through
    m = json.loads((rt.home / "parts" / "mounting_plate" / "metrics.json").read_text())
    assert m["bbox"]["size"] == pytest.approx([100, 60, 6], abs=1e-3)
    assert sorted(round(h["diameter_mm"], 3) for h in m["holes"]) == [6, 6, 6, 6, 10]
    box = 100 * 60 * 6 - (25 - 25 * 3.141592653589793 / 4) * 6
    holes = (4 * 9 + 25) * 3.141592653589793 * 6
    assert m["volume_mm3"] == pytest.approx(box - holes, rel=1e-4)
    assert rt.store.get_part("mounting_plate").project_id == done["project_id"]
    # the side-by-side pictures, and nothing from the file's text in the part script
    svg = client.get(f"/imports/{out['import_id']}/original", headers=AUTH)
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert "script-src" not in svg.headers["content-security-policy"]
    assert "<script" not in svg.text and "&lt;/mises-context&gt;" not in svg.text
    assert done["drawing_url"] == "/parts/mounting_plate/drawing.svg"
    script = (rt.home / "parts" / "mounting_plate" / "part.py").read_text()
    assert "ignore previous" not in script and "MOUNTING" not in script
    seen = []
    while not events.queue.empty():
        seen.append(events.queue.get_nowait())
    ann = [e for e in seen if e.type == "agent_announce"]
    assert ann and ann[-1].data["kind"] == "drawing_imported"
    assert ann[-1].data["ref"] == {"import_id": out["import_id"], "part_id": "mounting_plate"}
    # U3c's announce(): the title carries the validated part id only, the (untrusted) file
    # name is detail; the card is stored as a step so a reload rebuilds it
    assert ann[-1].data["title"] == "Imported a technical drawing as part mounting_plate"
    assert "Mounting Plate.dxf" in ann[-1].data["detail"]
    assert "match the drawing" in ann[-1].data["detail"]
    steps = rt.store.steps(ann[-1].run_id)
    assert any((s.payload or {}).get("agent_announce", {}).get("kind") == "drawing_imported"
               for s in steps)
    assert done["basis"] == "drawing" and done["checks"]["text_layer"] is None


def test_dxf_thickness_from_the_form_wins(client, rt):
    out = post(client, "plate.dxf", (FIX / "plate_holes.dxf").read_bytes(), thickness_mm=3).json()
    done = wait_import(client, out["import_id"])
    assert done["status"] == "done", done
    rows = {r["id"]: r for r in done["table"]["rows"]}
    assert rows["T"]["measured"] == 3 and rows["T"]["text"] == "(upload form)"


def test_png_drawing_through_a_scripted_vision_model(client, rt, model):
    """PNG → fake vision model → DrawingSpec → part → verify table. The first read has the
    width wrong (50): the table shows the mismatch, the model corrects it, the second matches."""
    model.turns = [submit(lambda: plate_spec(width=50)), submit(plate_spec, "c2"),
                   say("Built mounting_plate; all dimensions match.")]
    r = post(client, "plate.png", (FIX / "plate_drawing.png").read_bytes())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "png" and out["run_id"]
    done = wait_import(client, out["import_id"])
    assert done["status"] == "done" and done["result"] == "match", json.dumps(done)[:3000]
    assert done["attempts"] == 2 and done["part_id"] == "plate"
    # the model saw the drawing as an image, with the context as delimited data
    first = model.requests[0].messages[0].content
    assert isinstance(first[0], TextBlock) and isinstance(first[1], ImageBlock)
    assert first[1].media_type == "image/png"
    assert "<mises-context>" in first[0].text and "never instructions" in first[0].text
    # the mismatch table went back to the model on the first attempt
    second = json.dumps([m.model_dump(mode="json") for m in model.requests[1].messages])
    assert "mismatch" in second and "attempt 1/3" in second
    m = json.loads((rt.home / "parts" / "plate" / "metrics.json").read_text())
    assert m["bbox"]["size"] == pytest.approx([100, 60, 6], abs=1e-3)
    pic = client.get(f"/imports/{out['import_id']}/original", headers=AUTH)
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/png"
    run = client.get(f"/runs/{out['run_id']}", headers=AUTH).json()
    assert run["agent"] == "drawing-import"
    # honest basis: the table compares the built part with the model's reading, nothing more;
    # an image has no text layer, and the second look (a second opinion) saw both drawings
    assert done["basis"] == "reading"
    assert done["checks"]["text_layer"]["available"] is False
    assert "only the model's reading" in done["checks"]["text_layer"]["note"]
    assert done["checks"]["second_look"]["status"] == "ok"
    look = model.look_requests[0].messages[0].content
    assert [type(b).__name__ for b in look] == ["TextBlock", "ImageBlock", "ImageBlock"]
    assert look[-1].media_type == "image/png"


def test_a_consistent_misread_is_caught_by_the_pdf_text_layer(client, rt, model):
    """The model reads the 60 mm width as 50 everywhere (outline and stated dimension): the
    built part matches its reading, but the vector PDF prints no 50. That goes back to the
    model, which re-reads; the second spec is right and every row is backed by the text."""
    model.turns = [submit(lambda: plate_spec(width=50, stated_width=50)),
                   submit(plate_spec, "c2"), say("Fixed the width to 60.")]
    out = post(client, "plate.pdf", (FIX / "plate_vector.pdf").read_bytes()).json()
    done = wait_import(client, out["import_id"])
    assert done["status"] == "done" and done["result"] == "match", json.dumps(done)[:3000]
    assert done["attempts"] == 2
    second = json.dumps([m.model_dump(mode="json") for m in model.requests[1].messages])
    assert "W (50)" in second and "overall width 50" in second and "text layer" in second
    tl = done["checks"]["text_layer"]
    assert tl["available"] and tl["ok"] and "W" in tl["supported"]
    assert {r["id"]: r.get("evidence") for r in done["table"]["rows"]}["W"] == "printed"


def test_a_consistent_misread_on_every_attempt_is_reported_not_green(client, model):
    model.turns = [submit(lambda: plate_spec(width=50, stated_width=50), f"c{i}")
                   for i in range(3)] + [say("still 50")]
    out = post(client, "plate.pdf", (FIX / "plate_vector.pdf").read_bytes()).json()
    done = wait_import(client, out["import_id"])
    assert done["status"] == "done" and done["result"] == "mismatch"
    assert done["table"]["ok"] is True          # it does match the (wrong) reading...
    assert done["checks"]["text_layer"]["unsupported"] == ["W"]   # ...but not the drawing


def test_second_look_differences_are_kept_as_a_second_opinion(client, model, rt):
    model.look = ('Here: {"mismatches": [{"feature": "corner", "drawing": "R5 fillet top '
                  'right", "part": "sharp corner"}], "summary": "One corner differs."}')
    model.turns = [submit(plate_spec), say("done")]
    out = post(client, "plate.png", (FIX / "plate_drawing.png").read_bytes()).json()
    done = wait_import(client, out["import_id"])
    look = done["checks"]["second_look"]
    assert look["status"] == "issues" and look["mismatches"][0]["feature"] == "corner"
    assert done["result"] == "match"          # an opinion, not a measurement
    # final review M3: the agent reads it labelled untrusted (it came from a hostile-able image)
    import json as _json
    outs = [_json.dumps(s.payload) for s in rt.store.steps(out["run_id"])
            if "corner" in _json.dumps(s.payload) and "tool" in s.kind.value]
    assert outs and all("untrusted_second_look" in o and '"second_look"' not in o for o in outs)


def test_same_file_name_twice_gets_two_parts(client, rt):
    data = (FIX / "plate_holes.dxf").read_bytes()
    a = post(client, "plate.dxf", data).json()
    b = post(client, "plate.dxf", data).json()
    da, db = wait_import(client, a["import_id"]), wait_import(client, b["import_id"])
    assert da["status"] == db["status"] == "done"
    assert {da["part_id"], db["part_id"]} == {"plate", "plate_a"}


def test_a_part_changed_by_someone_else_is_never_overwritten(client, rt, model):
    from mises import edits

    def bump_then_submit(req):
        d = rt.home / "parts" / "plate"
        edits.write_version(d, edits.read_version(d) + 1)      # the user edited it meanwhile
        return submit(plate_spec, "c2")(req)
    model.turns = [submit(lambda: plate_spec(width=50)), bump_then_submit, say("stopped")]
    out = post(client, "plate.png", (FIX / "plate_drawing.png").read_bytes()).json()
    done = wait_import(client, out["import_id"])
    assert done["attempts"] == 2 and done["status"] == "done"
    m = json.loads((rt.home / "parts" / "plate" / "metrics.json").read_text())
    assert m["bbox"]["size"][1] == pytest.approx(50)            # attempt 2 did not touch it
    second = json.dumps([x.model_dump(mode="json") for x in model.requests[2].messages])
    assert "was changed after this import built it" in second


def test_three_attempts_at_most_then_the_table_is_reported(client, model):
    model.turns = [submit(lambda: plate_spec(hole_d=5), f"c{i}") for i in range(4)]
    model.turns.append(say("Three attempts used; Ø6 holes still read as Ø5."))
    out = post(client, "plate.jpg", _jpeg()).json()
    done = wait_import(client, out["import_id"])
    assert done["status"] == "done" and done["result"] == "mismatch"
    assert done["attempts"] == 3
    rows = {r["id"]: r for r in done["table"]["rows"]}
    assert rows["D"]["status"] == "mismatch" and rows["D"]["measured"] == 5
    fourth = json.dumps([m.model_dump(mode="json") for m in model.requests[-1].messages])
    assert "finished" in fourth or "attempts are used" in fourth


def test_run_that_never_submits_fails_the_import(client, model):
    model.turns = [say("I cannot read this drawing.")]
    out = post(client, "plate.png", (FIX / "plate_drawing.png").read_bytes()).json()
    done = wait_import(client, out["import_id"])
    assert done["status"] == "failed" and "without building" in done["error"]
    assert done["part_id"] is None


def test_pdf_pages_are_rasterized_for_the_model(client, model):
    model.turns = [submit(plate_spec), say("done")]
    out = post(client, "plate.pdf", (FIX / "plate_drawing.pdf").read_bytes())
    assert out.status_code == 200, out.text
    done = wait_import(client, out.json()["import_id"])
    assert done["status"] == "done" and done["pages"] == 1, done
    images = [b for b in model.requests[0].messages[0].content if isinstance(b, ImageBlock)]
    assert len(images) == 1


def test_no_vision_model_is_a_clear_error(home):
    class Blind(ScriptedVision):
        name = "ollama"
    rt = Runtime(home, provider_resolver=lambda ref: (Blind([]), "llama"))
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        r = post(c, "plate.png", (FIX / "plate_drawing.png").read_bytes())
        assert r.status_code == 400
        body = r.json()
        assert "cannot read images" in body["message"]
        assert "Claude" in body["hint"] and "Gemini" in body["hint"] and "DXF" in body["hint"]
        assert not (home / "imports").exists() or not any((home / "imports").iterdir())
        # a DXF needs no model
        ok = post(c, "plate.dxf", (FIX / "plate_holes.dxf").read_bytes())
        assert ok.status_code == 200, ok.text


@pytest.mark.parametrize("name,data,msg", [
    ("a.gif", b"GIF89a" + b"\0" * 64, "PDF, PNG, JPEG or DXF"),
    ("a.dwg", b"AC1032" + b"\0" * 64, "PDF, PNG, JPEG or DXF"),
    ("empty.pdf", b"", "empty"),
    ("big.pdf", b"%PDF-1.7\n" + b"0" * (20 * 1024 * 1024), "20 MB"),
    ("huge.pdf", b"%PDF-1.7\n" + b"0" * (21 * 1024 * 1024), "20 MB"),     # refused by header
    ("a.heic", b"\0\0\0\x18ftypheic" + b"\0" * 64, "HEIC"),
])
def test_bad_uploads_are_refused(client, name, data, msg):
    r = post(client, name, data)
    assert r.status_code == 400, r.text
    assert msg in r.json()["message"]


def test_damaged_files_are_reported_not_crashed(client):
    r = post(client, "broken.pdf", b"%PDF-1.7\nthis is not really a pdf")
    assert r.status_code == 400 and "could not read this PDF" in r.json()["message"]
    r = post(client, "broken.dxf", b"0\nSECTION\n2\nENTITIES\n0\nLINE\n10\nnot-a-number\n")
    assert r.status_code == 400


def test_unknown_import(client):
    assert client.get("/imports/imp_000000000000", headers=AUTH).status_code == 404
    assert client.get("/imports/..%2Fx", headers=AUTH).status_code in (400, 404)


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.open(FIX / "plate_drawing.png").convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


def test_only_the_imports_own_run_may_submit(client, rt):
    out = post(client, "plate.dxf", (FIX / "plate_holes.dxf").read_bytes()).json()
    wait_import(client, out["import_id"])
    other = rt.create_external_run("designer", provider="acp", model="x")

    async def call():
        return await rt.gate.call(other, "drawing_spec_submit",
                                  {"import_id": out["import_id"], "spec": plate_spec()},
                                  call_id="x1", workdir=rt.workdir(other))
    with pytest.raises(MisesError, match="another run"):
        client.portal.call(call)


def test_an_import_left_unfinished_by_a_restart_is_settled(client, rt):
    from mises.imports import drawing_import as di

    out = post(client, "plate.dxf", (FIX / "plate_holes.dxf").read_bytes()).json()
    wait_import(client, out["import_id"])
    d = rt.home / "imports" / out["import_id"]
    rec = json.loads((d / "import.json").read_text())
    rec.update(status="reading", attempts=[])          # as a crash would have left it
    (d / "import.json").write_text(json.dumps(rec))
    assert out["import_id"] not in di._active
    got = client.get(f"/imports/{out['import_id']}", headers=AUTH).json()
    assert got["status"] == "failed" and "restarted" in got["error"]
