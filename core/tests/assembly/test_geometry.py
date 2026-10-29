import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from mises.assembly.geometry import resolve_face, run_geometry
from mises.assembly.model import Assembly, Instance
from mises.assembly.solve import solve
from mises.cad.tools import CreateArgs, mirror_part_create

pytestmark = pytest.mark.slow

BOX = ('from build123d import *\nVARS={"s":10.0}\n'
       'def build(v):\n    return Box(v["s"], v["s"], v["s"])\n')


async def _build_box(tmp_path, part_id="cube"):
    ctx = SimpleNamespace(workdir=tmp_path)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id=part_id, script=BOX))
    assert r.ok, r.summary
    return Path(tmp_path) / "parts"


def _shift_x(dx):
    return [[1, 0, 0, dx], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


async def test_interference_is_detected(tmp_path):
    parts = await _build_box(tmp_path)
    asm = Assembly(instances=[
        Instance(id="a", part_id="cube", name="a"),
        Instance(id="b", part_id="cube", name="b", transform=_shift_x(5.0)),
    ])
    res = solve(asm)
    geo = await run_geometry(parts, asm, res, want="interference")
    assert len(geo.interferences) == 1
    hit = geo.interferences[0]
    assert {hit["a"], hit["b"]} == {"a", "b"}
    # two 10mm cubes offset 5mm overlap 5*10*10 = 500 mm^3
    assert hit["volume_mm3"] == pytest.approx(500, rel=0.02)


async def test_no_interference_when_apart(tmp_path):
    parts = await _build_box(tmp_path)
    asm = Assembly(instances=[
        Instance(id="a", part_id="cube", name="a"),
        Instance(id="b", part_id="cube", name="b", transform=_shift_x(100.0)),
    ])
    geo = await run_geometry(parts, asm, solve(asm), want="interference")
    assert geo.interferences == []


async def test_glb_has_one_node_per_instance(tmp_path):
    parts = await _build_box(tmp_path)
    asm = Assembly(instances=[
        Instance(id="a", part_id="cube", name="base"),
        Instance(id="b", part_id="cube", name="flap", transform=_shift_x(20.0)),
    ])
    geo = await run_geometry(parts, asm, solve(asm), want="glb", out_dir=tmp_path / "out")
    data = geo.glb_path.read_bytes()
    assert data[:4] == b"glTF"
    assert struct.unpack("<I", data[8:12])[0] == len(data)
    jlen = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + jlen])
    assert len(gltf["nodes"]) == 2
    assert len(gltf["meshes"]) == 2
    assert [n["name"] for n in gltf["nodes"]] == ["base", "flap"]
    # the second node carries its world transform as the glTF (column-major) node matrix
    assert gltf["nodes"][1]["matrix"][12] == pytest.approx(20.0)


async def test_resolve_face_reads_metrics(tmp_path):
    parts = await _build_box(tmp_path)
    origin, normal = resolve_face(parts, "cube", "f0")
    assert len(origin) == 3 and len(normal) == 3
