"""Exact B-rep measurements (POST /parts/{id}/measure, cad/worker.py --measure), checked against
geometry whose answers are known: a 60 × 40 × 5 plate with two Ø4.2 through holes."""
import asyncio
import json
import math
import time
from types import SimpleNamespace

import pytest

from mises import measure as M
from mises.cad.tools import CreateArgs, SetVarsArgs, mirror_part_create, mirror_part_set_vars
from mises.measure import MeasureError, measure_part

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
async def _close_warm_workers():
    yield
    await M.close_servers()
    M._cache.clear()

PLATE = ('from build123d import *\nVARS={}\ndef build(v):\n'
         '    with BuildPart() as p:\n'
         '        Box(60, 40, 5)\n'
         '        with Locations((-20, 0, 0), (20, 0, 0)):\n'
         '            Hole(2.1)\n'
         '    return p.part\n')
BOX = ('from build123d import *\nVARS={}\ndef build(v):\n'
       '    return Box(60, 40, 5)\n')


async def _build(tmp_path, part_id: str, script: str):
    r = await mirror_part_create.handler(SimpleNamespace(workdir=tmp_path),
                                         CreateArgs(part_id=part_id, script=script, vars={}))
    assert r.ok, r.summary
    d = tmp_path / "parts" / part_id
    return d, json.loads((d / "metrics.json").read_text())


@pytest.fixture
async def plate(tmp_path):
    return await _build(tmp_path, "plate", PLATE)


@pytest.fixture
async def box(tmp_path):
    return await _build(tmp_path, "box", BOX)


def _values(res: dict) -> dict:
    return {v["key"]: v["value"] for v in res["values"]}


def _face(m: dict, kind: str, normal=None):
    for f in m["face_list"]:
        if f["type"] != kind:
            continue
        if normal is not None and f["normal"] != normal:
            continue
        return f
    raise AssertionError(f"no {kind} face")


async def test_a_40mm_edge_measures_its_length(box):
    d, m = box
    e = next(e for e in m["edge_list"] if e["type"] == "line" and abs(e["length"] - 40) < 1e-6)
    res = await measure_part(d, [{"edge_id": e["id"]}])
    assert res["items"][0]["kind"] == "edge" and res["items"][0]["type"] == "line"
    assert _values(res)["length"] == pytest.approx(40.0, abs=1e-9)


async def test_a_hole_edge_and_wall_measure_diameter_4_2(plate):
    d, m = plate
    circle = next(e for e in m["edge_list"] if e["type"] == "circle")
    v = _values(await measure_part(d, [{"edge_id": circle["id"]}]))
    assert v["diameter"] == pytest.approx(4.2, abs=1e-9)
    assert v["radius"] == pytest.approx(2.1, abs=1e-9)
    assert v["length"] == pytest.approx(math.pi * 4.2, abs=1e-6)
    assert "arc_angle" not in v                       # a full circle
    wall = next(f for f in m["face_list"] if f["type"] == "cylinder")
    w = _values(await measure_part(d, [{"face_id": wall["id"]}]))
    assert w["diameter"] == pytest.approx(4.2, abs=1e-9)


async def test_a_60x40_face_measures_2400_mm2(box, plate):
    d, m = box
    top = _face(m, "plane", [0.0, 0.0, 1.0])
    res = await measure_part(d, [{"face_id": top["id"]}])
    assert _values(res)["area"] == pytest.approx(2400.0, abs=1e-9)
    assert res["items"][0]["normal"] == [0.0, 0.0, 1.0]
    # the same face with the two Ø4.2 holes through it: 2400 − 2·π·2.1²
    pd, pm = plate
    ptop = _face(pm, "plane", [0.0, 0.0, 1.0])
    pv = _values(await measure_part(pd, [{"face_id": ptop["id"]}]))
    assert pv["area"] == pytest.approx(2400 - 2 * math.pi * 2.1 ** 2, abs=1e-6)


async def test_parallel_faces_5mm_apart_give_distance_and_angle(box):
    d, m = box
    top = _face(m, "plane", [0.0, 0.0, 1.0])
    bottom = _face(m, "plane", [0.0, 0.0, -1.0])
    v = _values(await measure_part(d, [{"face_id": top["id"]}, {"face_id": bottom["id"]}]))
    assert v["distance"] == pytest.approx(5.0, abs=1e-9)
    assert v["normal_distance"] == pytest.approx(5.0, abs=1e-9)
    assert v["angle"] == pytest.approx(0.0, abs=1e-9)
    assert v["dz"] == pytest.approx(5.0, abs=1e-9)
    side = _face(m, "plane", [1.0, 0.0, 0.0])
    s = _values(await measure_part(d, [{"face_id": top["id"]}, {"face_id": side["id"]}]))
    assert s["angle"] == pytest.approx(90.0, abs=1e-9)
    assert s["distance"] == pytest.approx(0.0, abs=1e-9)   # they share an edge
    assert "normal_distance" not in s


async def test_two_points_snap_to_vertices_and_give_dx_dy_dz(box):
    d, _ = box
    res = await measure_part(d, [{"point": [-30.004, -20.003, 2.5]}, {"point": [30, 20, 2.5]}])
    assert [i["kind"] for i in res["items"]] == ["vertex", "vertex"]
    assert res["items"][0]["point"] == [-30.0, -20.0, 2.5]
    v = _values(res)
    assert v["distance"] == pytest.approx(math.hypot(60, 40), abs=1e-6)
    assert (v["dx"], v["dy"], v["dz"]) == pytest.approx((60.0, 40.0, 0.0), abs=1e-9)


async def test_a_point_off_a_vertex_lands_on_the_surface(box):
    d, _ = box
    res = await measure_part(d, [{"point": [1.0, 2.0, 2.6]}])
    assert res["items"][0]["kind"] == "point"
    assert res["items"][0]["point"] == pytest.approx([1.0, 2.0, 2.5], abs=1e-9)


async def test_hole_to_hole_center_distance(plate):
    d, m = plate
    circles = [e for e in m["edge_list"] if e["type"] == "circle"
               and e["center"] is not None and e["center"][2] > 0]
    assert len(circles) == 2
    v = _values(await measure_part(d, [{"edge_id": circles[0]["id"]}, {"edge_id": circles[1]["id"]}]))
    assert v["center_distance"] == pytest.approx(40.0, abs=1e-9)
    assert v["distance"] == pytest.approx(40.0 - 4.2, abs=1e-9)
    walls = [f["id"] for f in m["face_list"] if f["type"] == "cylinder"]
    w = _values(await measure_part(d, [{"face_id": walls[0]}, {"face_id": walls[1]}]))
    assert w["center_distance"] == pytest.approx(40.0, abs=1e-9)


async def test_every_metrics_edge_id_maps_to_its_own_edge(plate):
    # the STEP round trip reorders edges: each metrics id must still name the edge it did
    d, m = plate
    edges = m["edge_list"]
    for i in range(0, len(edges), 8):        # 8 per call (the cap): each item is its own edge
        batch = edges[i:i + 8]
        res = await measure_part(d, [{"edge_id": e["id"]} for e in batch])
        for e, item in zip(batch, res["items"]):
            assert (item["id"], item["type"]) == (e["id"], e["type"])
            assert item["length"] == pytest.approx(e["length"], abs=2e-3)


async def test_several_edges_total_and_errors(box):
    d, m = box
    ids = [{"edge_id": e["id"]} for e in m["edge_list"][:3]]
    v = _values(await measure_part(d, ids))
    assert v["total_length"] == pytest.approx(sum(e["length"] for e in m["edge_list"][:3]), abs=1e-2)
    with pytest.raises(MeasureError) as bad:
        await measure_part(d, [{"face_id": "f999"}])
    assert bad.value.code == "not_found"
    with pytest.raises(MeasureError) as bad2:
        await measure_part(d, [{"nope": 1}])
    assert bad2.value.code == "invalid_input"
    with pytest.raises(MeasureError) as bad3:
        await measure_part(d, [])
    assert bad3.value.code == "invalid_input"


async def test_unbuilt_part_is_not_found(tmp_path):
    (tmp_path / "p").mkdir()
    with pytest.raises(MeasureError) as e:
        await measure_part(tmp_path / "p", [{"face_id": "f0"}])
    assert e.value.code == "not_found"


async def test_metrics_carry_hole_radii_for_the_selection_label(plate):
    # the app's click label ("Edge · circle Ø4.2") reads these without a measure round trip
    _, m = plate
    assert {e.get("radius") for e in m["edge_list"] if e["type"] == "circle"} == {2.1}
    assert {f.get("radius") for f in m["face_list"] if f["type"] == "cylinder"} == {2.1}
    assert all("radius" not in e for e in m["edge_list"] if e["type"] == "line")


async def test_a_hole_wall_against_the_plate_top_is_90_degrees(plate):
    # a cylinder's axis is a line: a hole drilled straight through the plate is at 90°
    d, m = plate
    top = _face(m, "plane", [0.0, 0.0, 1.0])
    wall = next(f for f in m["face_list"] if f["type"] == "cylinder")
    v = _values(await measure_part(d, [{"face_id": top["id"]}, {"face_id": wall["id"]}]))
    assert v["angle"] == pytest.approx(90.0, abs=1e-9)
    side = _face(m, "plane", [1.0, 0.0, 0.0])
    s = _values(await measure_part(d, [{"face_id": side["id"]}, {"face_id": wall["id"]}]))
    assert s["angle"] == pytest.approx(0.0, abs=1e-9)      # the axis lies along the side


async def test_a_warm_worker_answers_new_pick_sets_fast(box):
    d, m = box
    faces = [f["id"] for f in m["face_list"]]
    await measure_part(d, [{"face_id": faces[0]}])           # starts the warm worker (cold)
    w = M._warm[str(d.resolve())]
    times = []
    for refs in ([{"face_id": faces[0]}, {"face_id": faces[1]}], [{"face_id": faces[2]}],
                 [{"face_id": faces[0]}, {"face_id": faces[3]}]):
        t = time.perf_counter()
        await measure_part(d, refs)
        times.append(time.perf_counter() - t)
    assert M._warm[str(d.resolve())] is w                    # the same process answered
    assert max(times) < 1.0, times                           # ~10 ms; generous under CI load
    await M.close_servers()
    assert w.proc.returncode is not None and not M._warm


async def test_a_rebuilt_part_gets_a_new_worker_and_a_stale_pick_is_refused(tmp_path):
    d, m = await _build(tmp_path, "slab", 'from build123d import *\nVARS={"t": 5.0}\n'
                        'def build(v):\n    return Box(60, 40, v["t"])\n')
    top = _face(m, "plane", [0.0, 0.0, 1.0])
    first = await measure_part(d, [{"face_id": top["id"]}], base_version=1)
    assert first["version"] == 1
    w = M._warm[str(d.resolve())]
    r = await mirror_part_set_vars.handler(SimpleNamespace(workdir=tmp_path), SetVarsArgs(
        part_id="slab", vars={"t": 8.0}, base_version=1))
    assert r.ok, r.summary
    with pytest.raises(MeasureError) as e:
        await measure_part(d, [{"face_id": top["id"]}], base_version=1)
    assert e.value.code == "stale_version" and e.value.current_version == 2
    m2 = json.loads((d / "metrics.json").read_text())
    top2 = _face(m2, "plane", [0.0, 0.0, 1.0])
    bottom2 = _face(m2, "plane", [0.0, 0.0, -1.0])
    v = _values(await measure_part(d, [{"face_id": top2["id"]}, {"face_id": bottom2["id"]}]))
    assert v["distance"] == pytest.approx(8.0, abs=1e-9)    # the new shape, not the old worker's
    assert M._warm[str(d.resolve())] is not w and w.proc.returncode is not None


async def test_at_most_two_workers_stay(box, plate, tmp_path):
    d1, _ = box
    d2, _ = plate
    d3, _ = await _build(tmp_path, "third", BOX)
    for d in (d1, d2, d3):
        assert await M.warm_part(d)
    assert len(M._warm) == M.MAX_WARM and str(d1.resolve()) not in M._warm   # LRU closed


async def test_an_idle_worker_closes(box, monkeypatch):
    monkeypatch.setattr(M, "IDLE_S", 1.0)
    assert await M.warm_part(box[0])
    for _ in range(50):
        if not M._warm:
            break
        await asyncio.sleep(0.1)
    assert not M._warm                                        # idle → closed


async def test_a_dead_worker_falls_back_to_the_one_shot(box):
    d, m = box
    assert await M.warm_part(d)
    w = M._warm[str(d.resolve())]
    w.proc.kill()
    await w.proc.wait()
    v = _values(await measure_part(d, [{"face_id": _face(m, "plane", [0.0, 0.0, 1.0])["id"]}]))
    assert v["area"] == pytest.approx(2400.0, abs=1e-9)
