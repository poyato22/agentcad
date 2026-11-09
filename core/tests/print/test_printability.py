"""Print simulation: known answers from the mesh check and the real (sandboxed) PrusaSlicer."""
import numpy as np
import pytest

from mises.print import printability as pa
from mises.print import slicer

from .conftest import add_printer  # noqa: F401  (fixtures come from conftest)


def box(x0, y0, z0, x1, y1, z1):
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    f = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
         [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return v[np.array(f)]


def cube20():
    return box(0, 0, 0, 20, 20, 20)


def tee():
    """A 10x10x20 stem under a 40x10x5 bar: 2 x 15x10 mm of flat overhang as built."""
    return np.concatenate([box(15, 0, 0, 25, 10, 25), box(0, 0, 20, 15, 10, 25),
                           box(25, 0, 20, 40, 10, 25)])


def two_blocks(gap):
    return np.concatenate([box(0, 0, 0, 10, 10, 10), box(10 + gap, 0, 0, 20 + gap, 10, 10)])


HAS_SLICER = slicer.find_slicer() is not None
needs_slicer = pytest.mark.skipif(not HAS_SLICER, reason="PrusaSlicer is not installed")


def test_stl_round_trip(tmp_path):
    pa.write_stl(tmp_path / "c.stl", cube20())
    t = pa.read_stl(tmp_path / "c.stl")
    assert t.shape == (12, 3, 3) and np.allclose(t, cube20())
    assert pa._volume(t) == pytest.approx(8000)


def test_orientations_are_rotations_that_put_their_side_down():
    for name, r in pa.ORIENTATIONS.items():
        assert np.allclose(r @ r.T, np.eye(3)) and np.linalg.det(r) == pytest.approx(1)
    assert np.allclose(pa.ORIENTATIONS["x_down"] @ [-1, 0, 0], [0, 0, -1])
    assert np.allclose(pa.ORIENTATIONS["y_down"] @ [0, -1, 0], [0, 0, -1])
    assert np.allclose(pa.ORIENTATIONS["flip_z"] @ [0, 0, 1], [0, 0, -1])


def test_cube_has_no_overhang_and_full_bed_contact():
    p = pa.pose_check(cube20(), "as_built")
    assert p.overhang_area_mm2 == 0
    assert p.bed_contact_mm2 == pytest.approx(400)
    assert p.height_mm == pytest.approx(20)


def test_tee_overhangs_as_built_but_not_flipped():
    built = pa.pose_check(tee(), "as_built")
    flipped = pa.pose_check(tee(), "flip_z")
    assert built.overhang_area_mm2 == pytest.approx(300)          # 2 x 15 x 10 mm under the bar
    assert built.bed_contact_mm2 == pytest.approx(100)
    assert flipped.overhang_area_mm2 == 0
    assert flipped.bed_contact_mm2 == pytest.approx(400)


def test_45_degree_chamfer_is_not_an_overhang_but_60_is():
    # a single downward facet tilted 45 deg (n_z = -sin 45) sits on the limit: not counted
    a = np.radians(45)
    tri45 = np.array([[[0, 0, 5], [10, 0, 5], [0, 10 * np.cos(a), 5 + 10 * np.sin(a)]]])
    tri45 = tri45[:, [0, 2, 1]]                                    # face down
    assert pa._normals(tri45)[0, 2] == pytest.approx(-np.cos(a))
    assert pa.pose_check(tri45, "as_built").overhang_area_mm2 == 0
    b = np.radians(30)                                             # 60 deg from vertical
    tri30 = np.array([[[0, 0, 5], [0, 10 * np.cos(b), 5 + 10 * np.sin(b)], [10, 0, 5]]])
    assert pa._normals(tri30)[0, 2] < -np.sin(np.radians(45))
    assert pa.pose_check(tri30, "as_built").overhang_area_mm2 == pytest.approx(50)


def test_bodies_and_clearance_known_gaps():
    assert len(pa.bodies(cube20())) == 1
    assert len(pa.bodies(two_blocks(0.2))) == 2
    c = pa.clearance_check(two_blocks(0.2), 0.3)
    assert c["min_gap_mm"] == pytest.approx(0.2, abs=1e-4) and c["ok"] is False
    assert pa.clearance_check(two_blocks(0.2), 0.15)["ok"] is True
    assert pa.clearance_check(two_blocks(0.5), 0.3)["ok"] is True
    far = pa.clearance_check(two_blocks(30), 0.3)
    assert far["ok"] is True and far["min_gap_mm"] is None


def test_cavity_joins_its_body():
    outer = box(0, 0, 0, 20, 20, 20)
    inner = box(5, 5, 5, 15, 15, 15)[:, [0, 2, 1]]                 # inward-facing shell
    assert len(pa.bodies(np.concatenate([outer, inner]))) == 1


def test_pin_in_hole_gap_vertex_to_face():
    # a 4 mm square pin inside a square hole 0.25 mm bigger each side: gap 0.25 mm
    ring = np.concatenate([box(-5, -5, 0, -2.25, 5, 5), box(2.25, -5, 0, 5, 5, 5),
                           box(-2.25, -5, 0, 2.25, -2.25, 5), box(-2.25, 2.25, 0, 2.25, 5, 5)])
    pin = box(-2, -2, 0, 2, 2, 5)
    g = pa.min_gap(pin, ring, 2.0)
    assert g == pytest.approx(0.25, abs=1e-6)


def test_support_parse():
    g = (";TYPE:Perimeter\nG1 X1 Y1 E5\n;TYPE:Support material\nG1 X2 E1.5\nG1 E-0.8\n"
         ";TYPE:Support material interface\nG1 X3 E0.5\n;TYPE:Solid infill\nG1 X4 E9\n")
    s = pa.support_from_gcode(g, 1.24)
    assert s["support_filament_mm"] == 2.0
    assert s["support_g"] == pytest.approx(2.0 * np.pi * 0.875 ** 2 / 1000 * 1.24, abs=0.01)


def test_material_sets_slicer_settings():
    tpu = pa.analysis_ini("MK4", "TPU")
    assert "filament_max_volumetric_speed = 3.5" in tpu and "temperature = 230" in tpu
    assert "use_relative_e_distances = 1" in tpu and "support_material_threshold = 45" in tpu
    assert "filament_type = PETG" in pa.analysis_ini("MK4", "PETG")
    assert tpu.count("retract_length = ") == 1                     # overrides, not duplicates


async def test_mesh_only_check_recommends_flipping_the_tee(tmp_path):
    pa.write_stl(tmp_path / "t.stl", tee())
    r = await pa.check_part(tmp_path / "t.stl", tmp_path / "out", pa.CheckOptions(slice=False))
    assert r["fidelity"] == "slicer_estimate"
    # flat on its side (10 mm tall) beats flipped (25 mm tall): same 400 mm2 on the bed
    assert r["recommended"]["orientation"] in ("y_down", "y_up_down")
    assert r["recommended"]["overhang_area_mm2"] == 0
    assert r["recommended"]["bed_contact_mm2"] == pytest.approx(400)
    assert "instead of as built" in r["tradeoff"]


@needs_slicer
async def test_real_slicer_cube_needs_no_supports(tmp_path):
    pa.write_stl(tmp_path / "c.stl", cube20())
    r = await pa.check_part(tmp_path / "c.stl", tmp_path / "out",
                            pa.CheckOptions(slice_candidates=1))
    s = r["as_built"]["slicer"]
    assert s["needs_supports"] is False and s["support_g"] == 0
    assert 1.0 < s["filament_g"] < 10.0                             # 8 cm3 at 15% infill, PLA
    assert 5 * 60 < s["time_s"] < 90 * 60
    assert r["printable"] is True and r["clearance"]["bodies"] == 1


@needs_slicer
async def test_real_slicer_tee_supports_as_built_none_flipped(tmp_path):
    pa.write_stl(tmp_path / "t.stl", tee())
    r = await pa.check_part(tmp_path / "t.stl", tmp_path / "out",
                            pa.CheckOptions(slice_candidates=1, max_print_time_h=0.01,
                                            orientations=["as_built", "flip_z"]))
    assert r["as_built"]["slicer"]["needs_supports"] is True
    assert r["as_built"]["slicer"]["support_g"] > 0.1
    assert r["recommended"]["orientation"] == "flip_z"
    assert r["recommended"]["slicer"]["needs_supports"] is False
    assert r["recommended"]["slicer"]["time_s"] < r["as_built"]["slicer"]["time_s"]
    assert r["time_ok"] is False and r["printable"] is False      # 36 s limit: too long


async def test_print_check_tool_and_types_list(rt, monkeypatch):
    d = rt.home / "parts" / "spinner"
    d.mkdir(parents=True)
    pa.write_stl(d / "model.stl", two_blocks(0.2))
    call = rt.ui_tool_call("print_check", {"part_id": "spinner", "slice": False})
    import asyncio
    await asyncio.wait_for(asyncio.shield(call.task), 20)
    assert call.status == "done", call.error
    data = call.output["data"]
    assert data["not_printable"] == ["spinner"]
    assert data["parts"]["spinner"]["clearance"]["min_gap_mm"] == pytest.approx(0.2, abs=1e-4)
    assert "NOT printable" in call.output["summary"]
    from mises.print.tools import print_study_type
    t = print_study_type()
    assert t["id"] == "print" and t["tool"] == "print_check"
    assert "min_clearance_mm" in t["schema"]["properties"]


@needs_slicer
async def test_print_check_tool_slices_every_part_in_the_sandbox(rt):
    import asyncio
    for pid, tris in (("cube", cube20()), ("tee", tee())):
        d = rt.home / "parts" / pid
        d.mkdir(parents=True)
        pa.write_stl(d / "model.stl", tris)
    call = rt.ui_tool_call("print_check", {"material": "PETG", "slice_candidates": 1,
                                           "max_print_time_h": 5})
    await asyncio.wait_for(asyncio.shield(call.task), 120)
    assert call.status == "done", call.error
    data = call.output["data"]
    assert set(data["parts"]) == {"cube", "tee"} and data["not_printable"] == []
    assert data["parts"]["tee"]["recommended"]["slicer"]["needs_supports"] is False
    assert data["parts"]["cube"]["material"] == "PETG"
    assert data["total_time_s"] > 0 and data["total_filament_g"] > 0
    assert data["parts"]["cube"]["sliced"] is True
