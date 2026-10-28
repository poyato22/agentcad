import json
import math
import struct

import pytest

from mises.sim.mesh import Mesh, Surface
from mises.sim.results import error_band, parse_frd, read_glb, von_mises, write_result_glb

FRD = """    1C
    2C                             3                                     1
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -3
  100CL  101 1.000000000           3                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -5  D2          1    2    2    0
 -5  D3          1    2    3    0
 -5  ALL         1    2    0    0    1ALL
 -1         1 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2 3.00000E-01 0.00000E+00-4.00000E-01
 -1        13 1.00000E-02 2.00000E-02 3.00000E-02
 -3
  100CL  101 1.000000000           3                     0    1           1
 -4  STRESS      6    1
 -5  SXX         1    4    1    1
 -1         1 1.00000E+01 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00
 -1         2-2.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00
 -1        13 0.00000E+00 0.00000E+00 0.00000E+00 1.00000E+00 0.00000E+00 0.00000E+00
 -3
 9999
"""


def test_von_mises():
    assert von_mises([10, 0, 0, 0, 0, 0]) == pytest.approx(10)
    assert von_mises([0, 0, 0, 1, 0, 0]) == pytest.approx(math.sqrt(3))
    assert von_mises([5, 5, 5, 0, 0, 0]) == pytest.approx(0)


def test_parse_frd_fixed_width_with_negative_values(tmp_path):
    p = tmp_path / "job.frd"
    p.write_text(FRD)
    r = parse_frd(p)
    assert r["DISP"][2] == pytest.approx([0.3, 0.0, -0.4])
    assert r["STRESS"][2][0] == pytest.approx(-2.0)
    assert r["STRESS"][13][3] == pytest.approx(1.0)
    assert set(r["DISP"]) == {1, 2, 13}


def test_error_band():
    assert error_band(coarse=9.0, fine=10.0) == pytest.approx(0.1)
    assert error_band(coarse=11.0, fine=10.0) == pytest.approx(0.1)
    assert error_band(coarse=1.0, fine=0.0) is None


def test_result_glb_has_per_vertex_colors(tmp_path):
    nodes = {1: (0, 0, 0), 2: (10, 0, 0), 3: (0, 10, 0), 4: (5, 0, 0), 5: (5, 5, 0), 6: (0, 5, 0)}
    mesh = Mesh(nodes=nodes, tets=[], surfaces=[Surface(tag=1, center=[3, 3, 0],
                                                        bbox=[0, 0, 0, 10, 10, 0], area=50,
                                                        tris=[[1, 2, 3, 4, 5, 6]])])
    vm = {1: 0.0, 2: 10.0, 3: 5.0, 4: 2.0, 5: 7.0, 6: 1.0}
    path = tmp_path / "result.glb"
    write_result_glb(path, mesh, vm, vmax=10.0, extras={"sim_id": "sim_x"})
    data = path.read_bytes()
    assert data[:4] == b"glTF" and struct.unpack("<I", data[8:12])[0] == len(data)
    gltf, colors = read_glb(path)
    prim = gltf["meshes"][0]["primitives"][0]
    assert "COLOR_0" in prim["attributes"] and "POSITION" in prim["attributes"]
    assert len(colors) == 6  # one colour per surface vertex
    assert len(set(colors)) == 6  # six different stresses, six colours
    assert colors[0][2] > colors[0][0]  # vertex 0 is node 1 (0 MPa): blue-ish
    assert any(c[0] > 5 * c[2] for c in colors)  # the 10 MPa peak is red-ish
    assert gltf["asset"]["extras"]["mises_sim"]["max"] == 10.0
    assert gltf["asset"]["extras"]["mises_sim"]["units"] == "MPa"
    assert gltf["accessors"][prim["indices"]]["count"] == 12  # 1 quadratic tri -> 4 linear


def test_peaks_never_claim_an_exclusion_they_could_not_make():
    from mises.sim.results import peaks
    mesh = Mesh(nodes={1: (0, 0, 0), 2: (1, 0, 0)}, tets=[], surfaces=[])
    frd = {"STRESS": {1: [5, 0, 0, 0, 0, 0], 2: [3, 0, 0, 0, 0, 0]},
           "DISP": {1: [0, 0, 0], 2: [0, 0, 0.1]}}
    p = peaks(mesh, frd, exclude={1})
    assert p["excludes_fixture"] is True and p["max_von_mises_mpa"] == pytest.approx(3)
    assert p["max_von_mises_at_fixture_mpa"] == pytest.approx(5)
    every = peaks(mesh, frd, exclude={1, 2})  # every stressed node sits on the fixed face
    assert every["excludes_fixture"] is False and every["max_von_mises_mpa"] == pytest.approx(5)
    assert peaks(mesh, frd, exclude=set())["excludes_fixture"] is False
