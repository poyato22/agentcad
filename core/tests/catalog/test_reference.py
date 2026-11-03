import json
import struct

import pytest

from mises.projects import mass_rows
from mises.reference_tools import register_reference_tools, sniff_format
from mises.types import ErrorCode, MisesError
from .test_tools import Env

URL = "https://files.example.com/prusa/mini.step"


def _box_step(tmp_path) -> bytes:
    import build123d as b3
    p = tmp_path / "box.step"
    b3.export_step(b3.Box(30, 20, 10), str(p))
    return p.read_bytes()


def _stl_bytes() -> bytes:
    tris = [((0, 0, 0), (10, 0, 0), (0, 10, 0)), ((0, 0, 0), (0, 0, 10), (10, 0, 0)),
            ((0, 0, 0), (0, 10, 0), (0, 0, 10)), ((10, 0, 0), (0, 0, 10), (0, 10, 0))]
    out = b"\0" * 80 + struct.pack("<I", len(tris))
    for t in tris:
        out += struct.pack("<3f", 0, 0, 0)
        for v in t:
            out += struct.pack("<3f", *v)
        out += b"\0\0"
    return out


def test_sniff():
    assert sniff_format(b"ISO-10303-21;\nHEADER;") == "step"
    assert sniff_format(_stl_bytes()) == "stl"
    assert sniff_format(b"solid x\n facet normal 0 0 1\n") == "stl"
    with pytest.raises(MisesError) as e:
        sniff_format(b"<html>nope</html>", "https://x.example.com/a.step")
    assert e.value.code == ErrorCode.INVALID_INPUT


@pytest.fixture
def env(tmp_path, web, public_dns):
    e = Env(tmp_path, web, public_dns)
    register_reference_tools(e.reg)
    return e


@pytest.mark.slow
async def test_reference_step_is_a_reference_part(env, web, tmp_path):
    web.page(URL, _box_step(tmp_path), ctype="application/octet-stream")
    out = await env.call("reference_import", {"url": URL, "name": "Test printer",
                                              "licence": "CC-BY 4.0", "project_id": "prj"})
    pid = out.data["part_id"]
    assert out.data["bbox"] == [pytest.approx(30), pytest.approx(20), pytest.approx(10)]
    meta = json.loads((env.tmp / "parts" / pid / "part.json").read_text())
    assert meta["kind"] == "reference" and meta["licence"] == "CC-BY 4.0"
    assert meta["source_url"] == URL
    assert env.store.get_part(pid).kind == "reference"
    rows = mass_rows(env.store, env.tmp, "prj", "PLA", versions=False)
    mine = next(r for r in rows if r["id"] == pid)
    assert mine["mass_g"] is None and "reference" in mine["mass_note"]
    from mises.bom import BomStore, view
    bom = view(env.store, BomStore(env.store), env.tmp, "prj")
    assert all(i.get("part_id") != pid for i in bom["items"])
    from mises.print.tools import register_print_tools
    register_print_tools(env.reg)
    with pytest.raises(MisesError) as pe:
        await env.call("print_slice", {"part_id": pid, "printer_id": "x"})
    assert "reference" in pe.value.message
    again = await env.call("reference_import", {"url": URL, "name": "Test printer",
                                                "project_id": "prj"})
    assert again.data["existing"]


async def test_dimension_sheet_needs_sources_and_flags_low_confidence(env):
    ok = await env.call("reference_sheet_set", {
        "ref_id": "prusa_mini", "name": "Prusa MINI+",
        "dims": {"bed_x": {"value_mm": 180, "source": "https://help.prusa3d.com/mini",
                           "confidence": "high"},
                 "frame_w": {"value_mm": 120, "source": "photo estimate", "confidence": "low"}},
        "datums": {"bed_hole_fl": {"kind": "hole", "position_mm": [10, 10, 0], "size_mm": 3.4,
                                   "source": "manual p.12", "confidence": "medium"}}})
    assert ok.data["low_confidence"] == ["dim frame_w"]
    got = await env.call("reference_sheet_get", {"ref_id": "prusa_mini"})
    assert got.data["sheet"]["dims"]["bed_x"]["source"].startswith("https://")
    assert "bed_hole_fl" in got.data["sheet"]["datums"]
    await env.call("reference_sheet_set", {"ref_id": "prusa_mini", "dims": {
        "frame_w": {"value_mm": 118, "source": "user measured", "confidence": "high"}}})
    again = await env.call("reference_sheet_get", {"ref_id": "prusa_mini"})
    assert again.data["low_confidence"] == [] and len(again.data["sheet"]["dims"]) == 2
    with pytest.raises(MisesError):
        await env.call("reference_sheet_set", {"ref_id": "x1", "dims": {
            "a": {"value_mm": 1, "source": "me", "confidence": "sure"}}})
    with pytest.raises(MisesError):
        await env.call("reference_sheet_get", {"ref_id": "nope"})


async def test_reference_rejects_a_web_page(env, web):
    web.page(URL, b"<html>login</html>", ctype="application/octet-stream")
    with pytest.raises(MisesError):
        await env.call("reference_import", {"url": URL, "name": "x", "project_id": "prj"})
