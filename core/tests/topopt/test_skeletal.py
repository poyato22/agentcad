"""Skeletal, truly 3-D generative design: a tall design space inside a large grid, crisp struts,
one printed body, loads on boxes (a mount inside a design space has no face of its own)."""
import numpy as np
import pytest
import scipy.sparse.linalg as spla
from pydantic import ValidationError

from mises.topopt import fe, filters, geometry as geo, simp
from mises.topopt.jobs import _dir
from mises.topopt.spec import Fixture, TopoptLoad, TopoptRunArgs
from mises.topopt.verify import select_in_box
from mises.sim.mesh import Mesh, Surface


def _cantilever(n=(16, 8, 8)):
    nx, ny, nz = n
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[nodes[:, 0] == 0] = True
    f = np.zeros(3 * len(nodes))
    tip = np.flatnonzero((nodes[:, 0] == nx) & (nodes[:, 1] == ny // 2) & (nodes[:, 2] == nz // 2))
    f[3 * tip + 2] = -1.0
    return nodes, fixed.ravel(), f


def _l_elements(n):
    """An L-shaped design space: a third of the grid has no elements at all."""
    i, j, k = fe.element_grid(n)
    return (k < n[2] // 2) | (i < n[0] // 4)


def test_only_the_design_space_is_assembled():
    n = (16, 8, 8)
    els = _l_elements(n)
    _, fixed, f = _cantilever(n)
    lev = fe.Level(n, 1.0, fe.hex8_ke(0.3), fixed, els)
    assert len(lev.el) == els.sum() and lev.pos.shape == (els.sum(), 64)
    # a node touching no element is an identity row
    assert lev.fixed[np.repeat(~lev.active_nodes, 3)].all()


def test_block_assembly_matches_the_element_sum():
    n = (4, 4, 4)
    fixed = np.zeros(3 * 125, bool)
    ke1 = fe.hex8_ke(0.3)
    lev = fe.Level(n, 1.0, ke1, fixed)
    E = np.random.default_rng(1).uniform(0.1, 1.0, 64)
    K = lev.assemble(E).toarray()
    ref = np.zeros_like(K)
    for e in range(64):
        d = lev.edof[e]
        ref[np.ix_(d, d)] += E[e] * ke1
    assert np.allclose(K, ref)


@pytest.mark.parametrize("levels", [2, 3])
def test_coarse_operators_are_the_galerkin_product(levels):
    """The per-coarse-element sum is exactly P^T A P, fixture included."""
    n = (8, 8, 8)
    nn = 9 ** 3
    fixed = np.zeros(3 * nn, bool)
    fixed[3 * 81:3 * 162] = True       # the whole z = 1 layer: no coarse node on it
    E = np.random.default_rng(2).uniform(0.1, 1.0, 512)
    mg = fe.MGCG(n, 1.0, 0.3, fixed, levels=levels)
    mg.set_density(E)
    for lv in range(1, levels):
        ref = (mg.P[lv - 1].T @ mg.A[lv - 1].tocsr() @ mg.P[lv - 1]).toarray()
        got = mg.A[lv].toarray()
        free = mg.levels[lv].free
        assert np.allclose(got[np.ix_(free, free)], ref[np.ix_(free, free)], atol=1e-9)


def test_solve_on_a_masked_design_space_matches_direct():
    n = (16, 8, 8)
    els = _l_elements(n)
    _, fixed, f = _cantilever(n)
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    f = np.zeros_like(f)
    tip = np.flatnonzero((nodes[:, 0] == 16) & (nodes[:, 2] == 2))
    f[3 * tip + 2] = -1.0
    mg = fe.MGCG(n, 1.0, 0.3, fixed, levels=3, elements=els)
    E = np.where(els, np.random.default_rng(0).uniform(1e-3, 1.0, els.size), 0.0)
    mg.set_density(E)
    u, its = mg.solve(f * mg.fine.free, tol=1e-9, maxit=300)
    ref = spla.spsolve(mg.A[0].tocsc(), f * mg.fine.free)
    assert its < 300 and np.linalg.norm(u - ref) / np.linalg.norm(ref) < 1e-6


def test_a_fixture_on_an_odd_layer_still_grounds_the_coarse_grid():
    n = (8, 8, 8)
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[(nodes[:, 2] == 1) & (abs(nodes[:, 0] - 4) <= 1) & (abs(nodes[:, 1] - 4) <= 1)] = True
    f = np.zeros(3 * len(nodes))
    f[3 * np.flatnonzero((nodes[:, 2] == 8))] = 1.0
    mg = fe.MGCG(n, 1.0, 0.3, fixed.ravel(), levels=3)
    mg.set_density(np.ones(512))
    u, its = mg.solve(f * mg.fine.free, tol=1e-6, maxit=300)
    assert np.isfinite(u).all() and its < 300


def test_projection_gradient_matches_finite_differences():
    n = (6, 4, 4)
    H, Hs = filters.density_filter(n, 1.5)
    chain = filters.Chain(n, H, Hs, beta=4.0)
    x = np.random.default_rng(3).uniform(0.2, 0.8, 96)
    g = np.random.default_rng(4).normal(size=96)
    chain.forward(x)
    an = chain.backward(g)
    d = np.random.default_rng(5).normal(size=96)
    eps = 1e-6
    num = (g @ chain.forward(x + eps * d) - g @ chain.forward(x - eps * d)) / (2 * eps)
    assert an @ d == pytest.approx(num, rel=1e-5)


def _problem(n=(24, 8, 8)):
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[nodes[:, 0] == 0] = True
    f = np.zeros(3 * len(nodes))
    tip = np.flatnonzero((nodes[:, 0] == n[0]) & (nodes[:, 2] == 4))
    f[3 * tip + 2] = -1.0 / len(tip)
    ne = int(np.prod(n))
    return simp.Problem(n=n, h=1.0, space=np.ones(ne, bool), keep_in=np.zeros(ne, bool),
                        keep_out=np.zeros(ne, bool), fixed_dofs=fixed.ravel(),
                        loads=[(f, 1.0)], E=1000.0, nu=0.3)


def test_crisp_leaves_struts_not_a_gray_cloud():
    p = _problem()
    gray = simp.optimize(p, simp.Options(volume_fraction=0.15, filter_radius=1.5, max_iter=40))
    crisp = simp.optimize(p, simp.Options(volume_fraction=0.15, filter_radius=1.5, max_iter=40,
                                          crisp=True))

    def grayness(x):
        return float((4 * x * (1 - x)).mean())
    assert grayness(crisp.x) < 0.6 * grayness(gray.x)
    # the volume target still holds, on the projected densities
    assert crisp.x.sum() / crisp.x.size == pytest.approx(0.15, abs=0.01)


def test_a_floating_mount_is_bridged_to_the_body():
    n = (20, 8, 8)
    ne = int(np.prod(n))
    i, j, k = fe.element_grid(n)
    x = np.zeros(ne)
    x[(i < 6) & (j == 4) & (k == 4)] = 1.0      # the body
    mount = (i >= 15) & (j == 4) & (k == 4)
    x[mount] = 1.0
    x[(i >= 6) & (i < 15) & (j == 4) & (k == 4)] = 0.4   # a strut that nearly formed
    xs, bridges = geo.bridge_islands(x, n, np.ones(ne, bool), mount)
    assert bridges == 1
    from scipy.ndimage import generate_binary_structure, label
    _, count = label(geo.element_grid3(xs > 0.5, n), structure=generate_binary_structure(3, 3))
    assert count == 1
    # along the strut, not a new path
    assert (xs[(j == 4) & (k == 4) & (i >= 6) & (i < 15)] == 1.0).all()


def test_a_speck_without_a_mount_is_not_bridged():
    n = (20, 8, 8)
    ne = int(np.prod(n))
    i, j, k = fe.element_grid(n)
    x = np.zeros(ne)
    x[(i < 6) & (j == 4) & (k == 4)] = 1.0
    x[(i == 18) & (j == 4) & (k == 4)] = 1.0
    xs, bridges = geo.bridge_islands(x, n, np.ones(ne, bool), np.zeros(ne, bool), min_share=0.5)
    assert bridges == 0 and (xs == x).all()


def test_a_one_voxel_link_grows_so_the_smoothing_keeps_it():
    from scipy.ndimage import gaussian_filter, label
    n = (16, 16, 16)
    ne = int(np.prod(n))
    i, j, k = fe.element_grid(n)
    x = np.zeros(ne)
    strut = (abs(i - 8) <= 2) & (abs(j - 8) <= 2) & (k < 5)
    top = (abs(i - 8) <= 2) & (abs(j - 8) <= 2) & (k > 10)
    link = (i == 8) & (j == 8) & (k >= 5) & (k <= 10)
    x[strut | top | link] = 1.0
    out = geo.thicken_thin(x, n, np.ones(ne, bool))
    assert out[strut | top | link].min() == 1.0
    assert (out == 1.0).sum() > (x == 1.0).sum()
    assert label(gaussian_filter(geo.element_grid3(out, n), 0.7, mode="constant") > 0.5)[1] == 1


def test_fixtures_and_loads_take_a_box_and_a_vector():
    Fixture(box=[0, 0, 0, 1, 1, 1])
    ld = TopoptLoad(box=[0, 0, 0, 1, 1, 1], newtons=5, direction=[1, 1, 0])
    assert _dir(ld.direction) == pytest.approx([2 ** -0.5, 2 ** -0.5, 0])
    assert _dir("-z") == [0, 0, -1]
    with pytest.raises(ValidationError):
        Fixture()
    with pytest.raises(ValidationError):
        Fixture(face="+x", box=[0, 0, 0, 1, 1, 1])
    with pytest.raises(ValidationError):
        Fixture(box=[1, 0, 0, 0, 1, 1])
    with pytest.raises(ValidationError):
        TopoptLoad(box=[0, 0, 0, 1, 1, 1], newtons=5, direction=[0, 0, 0])
    args = TopoptRunArgs(part_id="p", fixtures=[{"box": [0, 0, 0, 1, 1, 1]}],
                         load_cases=[{"loads": [{"box": [0, 0, 0, 1, 1, 1], "newtons": 1,
                                                 "direction": "+z"}]}])
    assert args.crisp is None and args.surface is None


def test_verify_finds_the_surface_inside_a_box():
    nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (5, 5, 5), 5: (6, 5, 5), 6: (5, 6, 5)}
    s = Surface(tag=1, center=[0, 0, 0], bbox=[0] * 6, area=0.0, tris=[[1, 2, 3], [4, 5, 6]])
    m = Mesh(nodes=nodes, tets=[], surfaces=[s], size_mm=1.0, gmsh_version="x", order=2)
    assert select_in_box(m, [-1, -1, -1, 2, 2, 1]) == [[1, 2, 3]]
    assert select_in_box(m, [20, 20, 20, 21, 21, 21]) == []


def test_only_a_mount_crosses_the_design_space_wall():
    """A fine surface: a strut at the wall closes round inside it; a mount crosses, so the
    exact boolean gives it a flat face."""
    n = (12, 12, 12)
    i, j, k = fe.element_grid(n)
    outside = k >= 8
    x = ((abs(i - 3) <= 1) & (abs(j - 6) <= 1) & (k < 8)).astype(float)
    mount = (abs(i - 9) <= 1) & (abs(j - 6) <= 1) & (k < 8)
    x[mount] = 1.0
    F = geo.nodal_field(x, n, outside, grow_from=mount)
    # F[m] averages elements m - 2 and m - 1 along each axis: index 9 straddles the wall (k 7, 8)
    assert F[10, 7, 9] == pytest.approx(1.0)      # the mount crossed
    assert F[4, 7, 9] <= 0.5                      # the strut stops at the wall


def test_a_one_voxel_link_does_not_count_when_the_surface_is_smoothed():
    """Connected as voxels, but the Gaussian erases the link: the two halves must be bridged."""
    from scipy.ndimage import gaussian_filter, label
    n = (24, 12, 12)
    ne = int(np.prod(n))
    i, j, k = fe.element_grid(n)
    block = lambda lo, hi: (i >= lo) & (i < hi) & (abs(j - 6) <= 2) & (abs(k - 6) <= 2)
    x = np.zeros(ne)
    x[block(2, 9) | block(15, 22)] = 1.0
    x[(i >= 9) & (i < 15) & (j == 6) & (k == 6)] = 1.0
    none = np.zeros(ne, bool)
    assert geo.bridge_islands(x, n, np.ones(ne, bool), none)[1] == 0       # voxels: one body
    xs, bridges = geo.bridge_islands(x, n, np.ones(ne, bool), none, sigma=0.7)
    assert bridges == 1
    smooth = gaussian_filter(geo.element_grid3(xs, n), 0.7, mode="constant") > 0.5
    assert label(smooth)[1] == 1


def test_the_cut_level_keeps_the_target_volume():
    x = np.concatenate([np.ones(10), np.full(10, 0.3), np.zeros(80)])
    space = np.ones(100, bool)
    out, t = geo.volume_preserving(x, space, 0.2)
    assert t == pytest.approx(0.3) or t == pytest.approx(0.05)
    assert out[:10].min() == 1.0
    assert (out[10:20] >= 0.5).all() and (out[20:] < 0.5).all()
    # a crisp field is left alone: its level stays at most 0.5
    out2, t2 = geo.volume_preserving((x > 0.5).astype(float), space, 0.1)
    assert ((out2 > 0.5) == (x > 0.5)).all() and t2 <= 0.5


def test_a_run_resumes_from_its_checkpoint():
    p = _problem()
    o = simp.Options(volume_fraction=0.2, filter_radius=1.5, max_iter=12, crisp=True, xtol=0.0,
                     ctol=0.0)
    saved = []
    whole = simp.optimize(p, o, checkpoint=lambda s: saved.append({k: np.copy(v) for k, v in s.items()}))
    assert [int(s["it"]) for s in saved] == [5, 10]
    rest = simp.optimize(p, o, resume=saved[0])
    assert rest.iters == 7       # iterations 5..11
    assert abs(rest.x - whole.x).max() < 0.05


def test_the_core_carries_on_an_interrupted_job(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from mises.topopt import jobs
    d = tmp_path / "topopt" / "to_0123456789ab"
    d.mkdir(parents=True)
    (tmp_path / "parts" / "space").mkdir(parents=True)
    (tmp_path / "parts" / "space" / "model.step").write_text("x")
    args = {"part_id": "space", "fixtures": [{"box": [0, 0, 0, 1, 1, 1]}],
            "load_cases": [{"loads": [{"box": [0, 0, 0, 1, 1, 1], "newtons": 1, "direction": "+z"}]}]}
    rec = {"topopt_id": d.name, "tool": "topopt_generate", "part_id": "space", "status": "running",
           "material": {"name": "PA12"}, "args": args, "run_id": None, "project_id": None,
           "variants": [{"id": "v0", "status": "done"}, {"id": "v1", "status": "running",
                                                         "phase": "optimize"}]}
    (d / "record.json").write_text(json.dumps(rec))
    seen = {}

    async def fake_run_job(events, r, dd, a, plan_, save):
        seen["status"] = [v["status"] for v in r["variants"]]
        seen["args"] = a

    monkeypatch.setattr(jobs, "_run_job", fake_run_job)
    rt = SimpleNamespace(home=tmp_path, events=None)

    async def go():
        out = jobs.resume_interrupted(rt)
        await jobs._active[d.name]
        return out
    assert asyncio.run(go()) == [d.name]
    assert seen["status"] == ["done", "queued"] and seen["args"].part_id == "space"
    # a finished job is left alone
    rec["status"] = "done"
    (d / "record.json").write_text(json.dumps(rec))
    assert jobs.resume_interrupted(rt) == []


def test_a_reused_pid_does_not_own_a_run():
    """After a reboot a run's owner pid can belong to a new process: it started after the run's
    last activity, so the run is orphaned and gets continued."""
    import os
    import time

    from mises.runtime import Runtime
    me = os.getpid()
    assert Runtime._still_owner(me, time.time())           # started before: still the owner
    assert not Runtime._still_owner(me, 1000.0)            # started long after that activity
    assert not Runtime._still_owner(2 ** 22 - 3, time.time())   # no such process


def test_a_shutdown_is_not_a_cancel(tmp_path, monkeypatch):
    """Stopping the core leaves a job unfinished on disk for the next start; only a real cancel
    records it as cancelled."""
    import asyncio
    from types import SimpleNamespace

    from mises.sim import ccx as _ccx
    from mises.topopt import jobs
    monkeypatch.setattr(_ccx, "find_ccx", lambda: None)

    async def hang(*a, **k):
        await asyncio.sleep(60)

    monkeypatch.setattr(jobs, "_run_variant", hang)
    monkeypatch.setattr(jobs, "note_lead", lambda *a: False)

    async def go(tid):
        rec = {"topopt_id": tid, "status": "queued", "part_id": "p",
               "variants": [{"id": "v0", "status": "running", "phase": "optimize"}]}
        args = SimpleNamespace(verify=False, min_safety_factor=1.5, max_deflection_mm=None)
        t = asyncio.ensure_future(jobs._run_job(None, rec, tmp_path, args, {}, lambda: None))
        await asyncio.sleep(0.01)
        jobs._active[tid] = t
        return rec, t

    async def shutdown():
        rec, t = await go("to_000000000001")
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        jobs._active.pop(rec["topopt_id"], None)
        assert rec["status"] == "running"
        assert rec["variants"][0]["status"] == "running"

    async def real_cancel():
        rec, t = await go("to_000000000002")
        jobs.cancel(rec["topopt_id"])
        with pytest.raises(asyncio.CancelledError):
            await t
        jobs._active.pop(rec["topopt_id"], None)
        jobs._cancel_requested.discard(rec["topopt_id"])
        assert rec["status"] == "cancelled"

    asyncio.run(shutdown())
    asyncio.run(real_cancel())
