"""Wind studies of a whole build (per-shell voxelization, wetted area) and 3D streamlines."""
import numpy as np
import pytest

from mises.sim.assembly_study import assembly_tris
from mises.sim.cfd.setup import plan_grid, voxelize
from mises.sim.cfd.wind import outside_fluid, voxelize_shells, wetted_area_mm2
from mises.sim.fields import Fields, save_grid


def box(lo, hi):
    """A closed box, outward-facing triangles."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return v[np.array(f)]


def _grid(tris):
    p = tris.reshape(-1, 3)
    return plan_grid(p.min(0), p.max(0), 12, 3.0)


def test_overlapping_shells_stay_solid_where_one_mesh_would_cancel():
    a, b = box((0, 0, 0), (60, 40, 40)), box((30, 0, 0), (90, 40, 40))
    tris = np.concatenate([a, b])
    g = _grid(tris)
    i = int(round((45 - g.origin[0]) / g.h_mm))
    j = int(round((20 - g.origin[1]) / g.h_mm))
    k = int(round((20 - g.origin[2]) / g.h_mm))
    assert not voxelize(tris, g)[i, j, k]      # parity over both meshes empties the overlap
    assert voxelize_shells(tris, g, [len(a), len(b)])[i, j, k]


def test_a_sealed_cavity_is_not_outside_flow():
    solid = np.zeros((12, 12, 12), bool)
    solid[4:9, 4:9, 4:9] = True
    solid[5:8, 5:8, 5:8] = False
    out = outside_fluid(solid)
    assert not out[6, 6, 6] and out[1, 1, 1] and out[11, 11, 11] and not out[4, 4, 4]


def test_interior_parts_carry_no_skin_friction():
    outer = box((0, 0, 0), (120, 120, 120))
    inner = box((50, 50, 50), (70, 70, 70))
    tris = np.concatenate([outer, inner])
    g = _grid(tris)
    solid = voxelize_shells(tris, g, [len(outer), len(inner)])
    assert wetted_area_mm2(tris, g, solid) == pytest.approx(6 * 120 ** 2, rel=1e-6)


def _write_stl(path, tris):
    import struct
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0, 0, 0) + t.astype("<f4").tobytes() + b"\0\0")


def test_assembly_tris_places_each_instance(tmp_path):
    _write_stl(tmp_path / "a.stl", box((0, 0, 0), (10, 10, 10)))
    T = np.eye(4)
    T[:3, 3] = (100, 0, 0)
    tris, counts = assembly_tris([
        {"id": "i1", "step": str(tmp_path / "a.step"), "transform": np.eye(4).tolist()},
        {"id": "i2", "step": str(tmp_path / "a.step"), "transform": T.tolist()}])
    assert counts == [12, 12]
    assert tris[:12].reshape(-1, 3).max(0)[0] == 10
    assert tris[12:].reshape(-1, 3).min(0)[0] == 100
    with pytest.raises(ValueError, match="no STL"):
        assembly_tris([{"id": "x", "step": str(tmp_path / "none.step"),
                        "transform": np.eye(4).tolist()}])


def _flow(tmp_path, rot=None):
    """Uniform 10 m/s along grid +x, a block in the way, velocity zero inside it."""
    vel = np.zeros((3, 40, 20, 20), np.float32)
    vel[0] = 10.0
    solid = np.zeros((40, 20, 20), bool)
    solid[18:22, 8:12, 8:12] = True
    vel[:, solid] = 0.0
    path = tmp_path / "f.npz"
    save_grid(path, np.array([0.0, 0.0, 0.0]), 5.0, {"velocity": vel}, {"velocity": "m/s"},
              solid=solid, rot=rot)
    return Fields(path), solid


def test_streamlines_follow_the_solver_field_and_never_enter_the_body(tmp_path):
    f, solid = _flow(tmp_path)
    r = f.streamlines(36)
    assert r["lines"] and r["units"] == "m/s" and r["inflow"] == pytest.approx(10.0)
    # samples are 0.75 cell apart at the inflow speed: dt = 0.75 * 5 mm / 10 m/s
    assert r["dt_s"] == pytest.approx(0.75 * 5e-3 / 10)
    for ln in r["lines"]:
        g = np.round(np.array(ln["points"]) / 5.0).astype(int)
        assert not solid[g[:, 0], g[:, 1], g[:, 2]].any()
        assert len(ln["speed"]) == len(ln["points"])
        assert max(ln["speed"]) <= 10.0 + 1e-3
    assert max(len(ln["points"]) for ln in r["lines"]) > 30     # free lines cross the tunnel


def test_streamlines_come_back_in_part_coordinates(tmp_path):
    # the wind frame's +x is the part's -z: rows of rot are the grid axes in part coordinates
    rot = np.array([[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
    f, _ = _flow(tmp_path, rot=rot)
    pts = np.array(max(f.streamlines(36)["lines"], key=lambda ln: len(ln["points"]))["points"])
    assert pts[-1, 2] < pts[0, 2]                               # travels down the part's z
    assert np.ptp(pts[:, 0]) < np.ptp(pts[:, 2])


def test_streamlines_refuse_a_study_without_velocity(tmp_path):
    path = tmp_path / "g.npz"
    save_grid(path, np.zeros(3), 1.0, {"speed": np.zeros((4, 4, 4), np.float32)}, {"speed": "m/s"})
    with pytest.raises(ValueError, match="no velocity"):
        Fields(path).streamlines()


def test_openfoam_samples_fill_a_grid_that_traces_streamlines(tmp_path):
    from mises.sim.cfd.foam_parse import volume_samples
    from mises.sim.cfd.openfoam import volume_arrays
    # a 10 mm grid with a hole the sampler left out (the body), uniform 10 m/s along +x
    shape = (40, 20, 20)
    rows = [[i * 0.01, j * 0.01, k * 0.01, 10.0, 0.0, 0.0]
            for i in range(shape[0]) for j in range(shape[1]) for k in range(shape[2])
            if not (18 <= i < 22 and 8 <= j < 12 and 8 <= k < 12)]
    f = tmp_path / "volume_U.xy"
    f.write_text("# x y z U_x U_y U_z\n" + "\n".join(" ".join(map(str, r)) for r in rows))
    vel, solid = volume_arrays(volume_samples(f), np.zeros(3), 0.01, shape)
    assert solid[20, 10, 10] and not solid[0, 0, 0]
    assert vel[0, 0, 0, 0] == 10.0 and vel[0, 20, 10, 10] == 0.0
    save_grid(tmp_path / "f.npz", np.zeros(3), 10.0, {"velocity": vel}, {"velocity": "m/s"},
              solid=solid)
    assert Fields(tmp_path / "f.npz").streamlines(16)["lines"]


def test_the_volume_grid_is_capped_and_stays_in_the_domain():
    from types import SimpleNamespace

    from mises.sim.cfd.foam_case import VOLUME_MAX_POINTS, volume_grid
    plan = SimpleNamespace(lo=np.zeros(3), hi=np.array([0.8, 0.2, 0.2]), d_ref_m=0.2,
                           dom_lo=np.array([-0.1, -1.0, -1.0]), dom_hi=np.array([3.0, 1.0, 1.0]))
    o, h, shape = volume_grid(plan)
    assert o[0] == -0.1                                   # clipped to the domain
    assert np.prod(shape) <= 1.1 * VOLUME_MAX_POINTS
    assert o[1] + (shape[1] - 1) * h <= 1.0
