import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.server.app import create_app

from .conftest import AUTH, TOKEN

pytestmark = pytest.mark.slow

PLATE = ('from build123d import *\nVARS={}\ndef build(v):\n'
         '    return Box(40,20,10) - Pos(0,0,0)*Cylinder(4,10)\n')


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


async def build(rt):
    r = await mirror_part_create.handler(SimpleNamespace(workdir=rt.home), CreateArgs(
        part_id="bracket", script=PLATE))
    assert r.ok, r.summary


@pytest.mark.parametrize("path", ["/parts/bracket/drawing.svg", "/parts/bracket/drawing.dxf",
                                  "/parts/bracket/drawing.json"])
def test_drawing_routes_need_token(client, path):
    assert client.get(path).status_code == 401


async def test_drawing_svg_dxf_json(client, rt):
    await build(rt)
    r = client.get("/parts/bracket/drawing.svg", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    for v in ("front", "top", "right", "iso"):
        assert f'id="view-{v}"' in r.text
    assert "MATERIAL: PLA" in r.text and "1x Ø8 THRU" in r.text
    d = client.get("/parts/bracket/drawing.dxf", headers=AUTH)
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    assert "bracket.dxf" in d.headers["content-disposition"]
    info = client.get("/parts/bracket/drawing.json", headers=AUTH).json()
    assert info["views"] == ["front", "top", "right", "iso"] and info["scale_text"]
    assert info["overall_mm"] == [40.0, 20.0, 10.0] and info["holes"] == ["1x Ø8 THRU"]
    assert set(info) == {"sheet", "scale", "scale_text", "views", "overall_mm", "holes", "label",
                         "material", "date", "step_mtime_ns"}
    m = client.get("/parts/bracket/drawing.svg?material=PETG", headers=AUTH)
    assert "MATERIAL: PETG" in m.text


def test_drawing_errors(client, rt):
    assert client.get("/parts/ghost/drawing.svg", headers=AUTH).status_code == 404
    assert client.get("/parts/Bad..Id/drawing.svg", headers=AUTH).status_code in (400, 404)
    assert client.get("/parts/ghost/drawing.svg?material=a%2Fb", headers=AUTH).status_code == 400


async def test_drawing_tool_via_ui_call(rt):
    await build(rt)
    call = rt.ui_tool_call("mirror_drawing", {"part_id": "bracket"})
    import asyncio
    await asyncio.wait_for(asyncio.shield(call.task), 30)
    assert call.status == "done", call.error
    assert call.output["data"]["views"] == ["front", "top", "right", "iso"]
    assert len(call.output["handles"]) == 2
