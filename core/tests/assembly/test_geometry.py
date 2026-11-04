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


ORNI = Path(__file__).parent.parent / "fixtures" / "assembly" / "ornicopter"
# the user's ornicopter (G2): the rear-left wing root and its spar, both at the root's solved
# world pose (a 3.88 deg tilt about Y and +50 mm in Y); the spar is fixed in the root's socket
REAR = [[0.997705, 0.0, 0.067715, -0.306393], [0.0, 1.0, 0.0, 50.0],
        [-0.067715, 0.0, 0.997705, -1.040405], [0.0, 0.0, 0.0, 1.0]]


async def test_interference_depends_only_on_the_pairs_relative_pose(tmp_path):
    """G2 root cause: the OCCT common taken in world space disagreed with itself for the same
    pair at the same relative pose (0 / 0.12 / 9.7 / 36.5 mm^3 on the ornicopter's four
    root/spar pairs). Taken in the first part's own frame it is the real 0.031 mm^3 (the rod's
    end 0.01 mm into the socket floor) wherever the pair sits; hits carry ids and a location."""
    parts = tmp_path / "parts"
    for pid in ("wing_root_left", "spar_rod_left"):
        (parts / pid).mkdir(parents=True)
        (parts / pid / "model.step").write_bytes((ORNI / f"{pid}.step").read_bytes())
    vols = []
    for tf in (REAR, _shift_x(0.0)):
        asm = Assembly(instances=[
            Instance(id="root", part_id="wing_root_left", name="root_rl", transform=tf),
            Instance(id="spar", part_id="spar_rod_left", name="spar_rl", transform=tf),
        ])
        geo = await run_geometry(parts, asm, solve(asm), want="interference")
        assert len(geo.interferences) == 1
        hit = geo.interferences[0]
        assert (hit["a_id"], hit["b_id"]) == ("root", "spar")
        assert len(hit["center"]) == 3 and len(hit["bbox"]["min"]) == 3
        vols.append(hit["volume_mm3"])
    assert vols[0] == pytest.approx(0.0314, abs=0.005) and vols[1] == pytest.approx(vols[0], abs=0.002)


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


def test_sweep_measures_only_what_moves_and_prunes_far_pairs(monkeypatch):
    """DOG demo: an 8-step knee sweep of a 40-instance dog timed out (240 s): every pose took
    OCCT distances of all ~800 pairs. Pairs at the same relative pose as an earlier pose reuse
    it, and a pair whose boxes are farther apart than the closest pair so far is not measured.
    The answers are unchanged."""
    import build123d as b3
    from mises.cad import worker

    calls = []
    real = b3.Shape.distance_to

    def counted(self, other):
        calls.append(1)
        return real(self, other)
    monkeypatch.setattr(b3.Shape, "distance_to", counted)
    box = b3.Box(10, 10, 10)
    loaded = [("a", box, _shift_x(0.0), "a"), ("b", box, _shift_x(13.0), "b"),
              ("far", box, _shift_x(500.0), "far"), ("m", box, _shift_x(40.0), "m")]
    poses = [{"m": _shift_x(x)} for x in (40.0, 30.0, 20.0, 15.0)]   # m moves toward b
    out = worker._sweep_positions(loaded, poses, 0.01, b3.Location)
    assert [p["min_clearance_mm"] for p in out] == pytest.approx([3.0, 3.0, 0.0, 0.0])
    assert out[0]["closest"] == ["a", "b"]
    assert out[3]["interferences"][0]["volume_mm3"] == pytest.approx(800, rel=0.02)
    assert len(calls) <= 6          # all pairs every pose would be 6 x 4 = 24


async def test_resolve_face_reads_metrics(tmp_path):
    parts = await _build_box(tmp_path)
    origin, normal = resolve_face(parts, "cube", "f0")
    assert len(origin) == 3 and len(normal) == 3
