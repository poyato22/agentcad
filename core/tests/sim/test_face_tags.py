"""I-7: named load faces. A part script tags faces (FACES = {"motor_mount": fn}); every build
re-resolves them, so a study's variants keep the same physical faces, unlike face ids."""
import json
from types import SimpleNamespace

import pytest

import mises.runtime as runtime_mod
from mises.cad.runner import run_build
from mises.engineering.spec import LoadCase
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.sim.ccx import find_ccx
from mises.sim.mesh import is_face_spec, select_faces
from mises.sim.settings import check_face
from mises.sim.types import SimError

# a cantilever: tag its root (x-min end) and tip (x-max end) by geometry, not by id
TAGGED = ('from build123d import *\nVARS={"length":100.0}\n'
          'FACES = {"root": lambda p: p.faces().sort_by(Axis.X)[0],\n'
          '         "tip": lambda p: p.faces().sort_by(Axis.X)[-1],\n'
          '         "Bad Name": lambda p: p.faces()[0],\n'
          '         "broken": lambda p: 1 / 0}\n'
          'def build(v):\n'
          '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')


def test_tag_specs_validate():
    assert is_face_spec("tag:motor_mount") and is_face_spec("bottom") and is_face_spec("f3")
    assert not is_face_spec("tag:Bad") and not is_face_spec("motor_mount")
    assert check_face("tag:stack") == "tag:stack"
    LoadCase(id="t", name="thrust", fix="tag:stack", load="tag:motor_mount", newtons=6,
             direction="+z")


@pytest.mark.slow
async def test_worker_exports_tags_that_follow_the_geometry(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text(TAGGED)
    for length in (100.0, 160.0):                     # two "variants"
        r = await run_build(d, {"length": length})
        assert r.ok, r.error
        tags, fl = r.metrics["face_tags"], {f["id"]: f for f in r.metrics["face_list"]}
        assert set(tags) == {"root", "tip"}
        assert fl[tags["root"][0]]["center"][0] == pytest.approx(0, abs=1e-6)
        assert fl[tags["tip"][0]]["center"][0] == pytest.approx(length)
        errs = " ".join(r.metrics["face_tag_errors"])
        assert "Bad Name" in errs and "broken" in errs     # bad selectors never fail the build


def test_unknown_tag_is_a_clear_setup_error():
    surf = [SimpleNamespace(tag=1, bbox=[0, 0, 0, 0, 1, 1], center=[0, 0.5, 0.5])]
    with pytest.raises(SimError) as e:
        select_faces("tag:stack", surf, {"min": [0, 0, 0], "max": [1, 1, 1]}, [],
                     {"motor_mount": ["f0"]})
    assert "stack" in e.value.message and "motor_mount" in e.value.hint


@pytest.mark.slow
@pytest.mark.timeout(180)
@pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")
async def test_static_study_on_tagged_faces(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(h))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    rt = Runtime(h, provider_resolver=lambda ref: (fake, "fake-model"))
    rid = rt.create_external_run("tester", provider="x", model="y")
    out = await rt.gate.call(rid, "mirror_part_create", {"part_id": "beam", "script": TAGGED},
                             call_id="b1", workdir=rt.workdir(rid))
    assert out.ok, out.summary
    out = await rt.gate.call(rid, "sim_static_run", {
        "part_id": "beam", "fix": "tag:root",
        "load": {"face": "tag:tip", "newtons": 10, "direction": "-z"}, "material": "PETG"},
        call_id="s1", workdir=rt.workdir(rid))
    assert out.ok, out.summary
    rec = json.loads(next((h / "parts" / "beam" / "sims").glob("*/result.json")).read_text())
    assert rec["status"] == "done" and rec["results"]["max_displacement_mm"] > 0
