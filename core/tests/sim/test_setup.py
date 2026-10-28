"""Pure pieces of the sim pipeline: materials, environments, face selection, loads."""
import pytest

from mises.sim.environments import DIRECTIONS, ENVIRONMENTS, FACE_PRESETS, direction_vector
from mises.sim.materials import FDM_KNOCKDOWN, MATERIALS, get_material
from mises.sim.mesh import Mesh, Surface, consistent_loads, mesh_sizes, select_faces
from mises.sim.types import SimError

BBOX = {"min": [0, 0, 0], "max": [100, 10, 10], "size": [100, 10, 10]}


def _box_surfaces():
    # the six faces of a 100x10x10 box, as the mesh worker reports them (one tri each)
    mk = lambda tag, c, lo, hi, area: Surface(tag=tag, center=c, bbox=[*lo, *hi], area=area,
                                              tris=[])
    return [mk(1, [0, 5, 5], [0, 0, 0], [0, 10, 10], 100), mk(2, [100, 5, 5], [100, 0, 0], [100, 10, 10], 100),
            mk(3, [50, 0, 5], [0, 0, 0], [100, 0, 10], 1000), mk(4, [50, 10, 5], [0, 10, 0], [100, 10, 10], 1000),
            mk(5, [50, 5, 0], [0, 0, 0], [100, 10, 0], 1000), mk(6, [50, 5, 10], [0, 0, 10], [100, 10, 10], 1000)]


def test_materials_table_has_required_filaments_with_sources():
    for name in ("PLA", "PETG", "ABS", "ASA", "PA-CF"):
        m = get_material(name)
        assert m.e_mpa > 500 and 0.2 < m.nu < 0.45 and m.yield_mpa > 10 and m.density_g_cm3 > 0.9
        assert m.source
        assert m.allowable_mpa == pytest.approx(m.yield_mpa * FDM_KNOCKDOWN)
    assert FDM_KNOCKDOWN == 0.7
    assert get_material("pla").name == "PLA"
    assert set(MATERIALS) >= {"PLA", "PETG", "ABS", "ASA", "PA-CF"}
    with pytest.raises(SimError) as e:
        get_material("unobtainium")
    assert "PLA" in (e.value.hint or "")


def test_directions_and_presets():
    assert direction_vector("-z") == (0.0, 0.0, -1.0)
    assert direction_vector("+x") == (1.0, 0.0, 0.0)
    assert set(DIRECTIONS) == {"+x", "-x", "+y", "-y", "+z", "-z"}
    assert {"bottom", "top", "x-min", "x-max", "y-min", "y-max", "z-min", "z-max"} <= set(FACE_PRESETS)
    names = {e["name"] for e in ENVIRONMENTS}
    assert {"Desk", "Outdoor", "Bike mount", "Car dashboard 80°C"} <= names
    for e in ENVIRONMENTS:
        assert e["suggested"]["fix"] and e["suggested"]["load"]["direction"] in DIRECTIONS


def test_select_faces_by_preset():
    s = _box_surfaces()
    assert select_faces("x-min", s, BBOX, None) == [1]
    assert select_faces("x-max", s, BBOX, None) == [2]
    assert select_faces("bottom", s, BBOX, None) == [5]
    assert select_faces("top", s, BBOX, None) == [6]
    assert select_faces("y-max", s, BBOX, None) == [4]


def test_select_faces_by_face_id_matches_center():
    s = _box_surfaces()
    faces = [{"id": "f0", "center": [50, 5, 10.0]}, {"id": "f1", "center": [100, 5, 5]}]
    assert select_faces("f1", s, BBOX, faces) == [2]
    assert select_faces("f0", s, BBOX, faces) == [6]
    with pytest.raises(SimError):
        select_faces("f9", s, BBOX, faces)
    with pytest.raises(SimError) as e:
        select_faces("f0", s, BBOX, None)  # part built before face ids existed
    assert "rebuild" in (e.value.hint or "")
    with pytest.raises(SimError):
        select_faces("sideways", s, BBOX, faces)


def test_select_faces_no_flat_face():
    curved = [Surface(tag=1, center=[0, 0, 0], bbox=[-5, -5, -5, 5, 5, 5], area=314, tris=[])]
    with pytest.raises(SimError) as e:
        select_faces("bottom", curved, {"min": [-5] * 3, "max": [5] * 3, "size": [10] * 3}, None)
    assert "face_id" in (e.value.hint or "")


def test_consistent_loads_sum_to_force_on_midside_nodes():
    # two 6-node triangles making a 10x10 square at x=0
    nodes = {1: (0, 0, 0), 2: (0, 10, 0), 3: (0, 10, 10), 4: (0, 0, 10),
             5: (0, 5, 0), 6: (0, 10, 5), 7: (0, 5, 5), 8: (0, 5, 10), 9: (0, 0, 5)}
    tris = [[1, 2, 3, 5, 6, 7], [1, 3, 4, 7, 8, 9]]
    mesh = Mesh(nodes=nodes, tets=[], surfaces=[Surface(tag=7, center=[0, 5, 5],
                                                        bbox=[0, 0, 0, 0, 10, 10], area=100, tris=tris)])
    loads = consistent_loads(mesh, [7], (0.0, 0.0, -12.0))
    assert set(loads) == {5, 6, 7, 8, 9}  # corner nodes carry nothing for quadratic triangles
    assert sum(v[2] for v in loads.values()) == pytest.approx(-12.0)
    assert loads[7][2] == pytest.approx(-4.0)  # shared midside node gets both shares


def test_mesh_sizes_coarse_then_fine_and_capped():
    coarse, fine = mesh_sizes([100, 10, 10], 10_000)
    assert fine == pytest.approx(coarse / 2)
    assert fine <= 2.5
    # a huge solid block is capped so the fine mesh stays within the node budget
    c2, f2 = mesh_sizes([500, 500, 500], 500 ** 3)
    assert f2 > 500 / 40
