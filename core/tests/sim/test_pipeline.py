"""End to end: build123d mirror part -> gmsh -> CalculiX -> results. Needs a real `ccx`."""
import json
from types import SimpleNamespace

import pytest

from mises.cad.tools import CreateArgs, PartArgs, mirror_measure, mirror_part_create
from mises.sim import ccx as ccx_mod
from mises.sim import studies
from mises.sim import tools as sim_tools
from mises.sim.ccx import INSTALL_HINT, find_ccx
from mises.sim.results import read_glb
from mises.sim.tools import (EnvListArgs, ResultArgs, StaticArgs, sim_env_list, sim_result_get,
                             sim_static_run)
from mises.store import AgentPolicy
from mises.types import Caps, ErrorCode, MisesError, SafetyClass, StepKind

pytestmark = pytest.mark.slow
needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")

L, B, H, F = 100.0, 10.0, 10.0, 10.0
BEAM = ('from build123d import *\nVARS={"length":100.0}\ndef build(v):\n'
        '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')


class _Events:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


def _ctx(tmp_path, scope=("*",)):
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=list(scope), read_scope=list(scope),
                      caps=Caps(), allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id="r1", events=_Events(), services={},
                           policy=pol, call_id="c1")


async def _beam(ctx):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="beam", script=BEAM))
    assert r.ok, r.summary


async def test_mirror_measure_lists_face_ids_centers_normals(tmp_path):
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    m = await mirror_measure.handler(ctx, PartArgs(part_id="beam"))
    faces = m.data["face_list"]
    assert len(faces) == 6 and [f["id"] for f in faces] == [f"f{i}" for i in range(6)]
    end = [f for f in faces if f["center"][0] == pytest.approx(100)]
    assert len(end) == 1 and end[0]["normal"] == pytest.approx([1, 0, 0])
    assert end[0]["area"] == pytest.approx(100) and end[0]["type"] == "plane"
    assert m.data["faces"] == 6  # the old count stays


@pytest.mark.timeout(180)
@needs_ccx
async def test_cantilever_matches_beam_theory(tmp_path):
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    out = await sim_static_run.handler(ctx, StaticArgs(
        part_id="beam", fix="x-min", load={"face": "x-max", "newtons": F, "direction": "-z"},
        material="PLA"))
    assert out.ok, out.summary
    assert out.step_kind == StepKind.SIM_RUN
    d = out.data
    analytic = 6 * F * L / (B * H ** 2)  # 6 MPa at the root
    peak = d["results"]["max_von_mises_mpa"]
    assert abs(peak - analytic) / analytic < 0.15, (peak, analytic)
    e = d["material"]["e_mpa"]
    defl = F * L ** 3 / (3 * e * B * H ** 3 / 12)
    assert abs(d["results"]["max_displacement_mm"] - defl) / defl < 0.15
    # honest labelling
    assert d["fidelity_class"] == "fem" and d["guide"] is True
    assert d["solver"]["name"] == "CalculiX" and d["solver"]["version"]
    assert d["mesh"]["fine"]["size_mm"] < d["mesh"]["coarse"]["size_mm"]
    assert d["mesh"]["fine"]["nodes"] > d["mesh"]["coarse"]["nodes"]
    assert d["material"]["knockdown"] == 0.7
    fx = d["results"]["max_von_mises_at_fixture_mpa"]
    assert d["results"]["peak_excludes"] == "nodes on the fixed face"
    assert d["results"]["safety_factor_incl_fixture"] == pytest.approx(
        d["material"]["allowable_mpa"] / max(peak, fx))
    _assert_labelled(out.summary, fx)
    # error band = |fine - coarse| / fine on the peak stress
    coarse = d["results"]["coarse_max_von_mises_mpa"]
    assert d["results"]["error_band"] == pytest.approx(abs(peak - coarse) / peak)
    allow = d["material"]["yield_mpa"] * 0.7
    assert d["results"]["safety_factor"] == pytest.approx(allow / peak)
    # stored record + coloured GLB
    sim_dir = tmp_path / "parts" / "beam" / "sims" / d["sim_id"]
    rec = json.loads((sim_dir / "result.json").read_text())
    assert rec["status"] == "done" and rec["sim_id"] == d["sim_id"]
    gltf, colors = read_glb(sim_dir / "result.glb")
    assert "COLOR_0" in gltf["meshes"][0]["primitives"][0]["attributes"]
    assert len({tuple(round(x, 3) for x in c) for c in colors}) > 5
    # the temp solve dir under the run's workdir is cleaned up
    assert not any((tmp_path / "sim_tmp").glob("*"))
    got = await sim_result_get.handler(ctx, ResultArgs(sim_id=d["sim_id"]))
    assert got.data["results"]["max_von_mises_mpa"] == peak and got.data["stale"] is False
    _assert_labelled(got.summary, fx)
    assert not any("shared with the fixed face" in w for w in d["warnings"])


def _assert_labelled(summary: str, fixture_mpa: float) -> None:
    for s in ("GUIDE ONLY", "CalculiX 2.", "fine mesh 2.5 mm", "coarse 5 mm",
              "PLA isotropic linear elastic", "x0.7 FDM knock-down", "error band",
              f"peak excludes the fixed face; at fixture: {fixture_mpa:.3g} MPa",
              "safety factor incl. fixture"):
        assert s in summary, (s, summary)


@pytest.mark.timeout(180)
@needs_ccx
async def test_load_on_nodes_shared_with_fixture_warns(tmp_path):
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    # the x-max end shares its bottom edge with the clamped bottom face
    out = await sim_static_run.handler(ctx, StaticArgs(
        part_id="beam", fix="bottom", load={"face": "x-max", "newtons": F, "direction": "-z"}))
    assert out.ok, out.summary
    assert out.data["mesh"]["fine"]["dropped_load_n"] > 0.01 * F
    assert any("shared with the fixed face" in w for w in out.data["warnings"])


async def test_queue_wait_is_bounded(tmp_path, monkeypatch):
    # a study that gets the slot after the longest allowed wait still has its full time
    assert sim_tools.QUEUE_TIMEOUT_S + sim_tools.STUDY_MAX_S < sim_tools.TOOL_TIMEOUT_S
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: "/usr/bin/false")
    monkeypatch.setattr(sim_tools, "QUEUE_TIMEOUT_S", 0.05)
    monkeypatch.setenv("MISES_CONCURRENT_STUDIES", "1")
    studies._sems.clear()
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    sem = sim_tools._semaphore()
    await sem.acquire()  # another study holds the solver
    try:
        out = await sim_static_run.handler(ctx, StaticArgs(
            part_id="beam", fix="x-min", load={"face": "x-max", "newtons": F, "direction": "-z"}))
    finally:
        sem.release()
    assert out.ok is False and out.data["error"] == "busy"
    assert sim_static_run.timeout_s == sim_tools.TOOL_TIMEOUT_S


@pytest.mark.timeout(180)
@needs_ccx
async def test_face_id_selection_runs(tmp_path):
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    faces = (await mirror_measure.handler(ctx, PartArgs(part_id="beam"))).data["face_list"]
    fix = next(f["id"] for f in faces if f["center"][0] == pytest.approx(0))
    load = next(f["id"] for f in faces if f["center"][0] == pytest.approx(100))
    out = await sim_static_run.handler(ctx, StaticArgs(
        part_id="beam", fix=fix, load={"face": load, "newtons": F, "direction": "-z"}))
    assert out.ok, out.summary
    assert out.data["material"]["name"] == "PLA"  # the default material
    assert abs(out.data["results"]["max_von_mises_mpa"] - 6.0) / 6.0 < 0.15


async def test_missing_ccx_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: None)
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    out = await sim_static_run.handler(ctx, StaticArgs(
        part_id="beam", fix="x-min", load={"face": "x-max", "newtons": F, "direction": "-z"}))
    assert out.ok is False
    assert INSTALL_HINT in out.summary
    assert out.data["error"] == "solver_missing" and out.data["install"] == INSTALL_HINT
    recs = list((tmp_path / "parts" / "beam" / "sims").glob("*/result.json"))
    assert len(recs) == 1 and json.loads(recs[0].read_text())["status"] == "failed"


async def test_same_face_for_fix_and_load_is_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    with pytest.raises(MisesError) as e:
        await sim_static_run.handler(ctx, StaticArgs(
            part_id="beam", fix="x-max", load={"face": "x-max", "newtons": F, "direction": "-z"}))
    assert e.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.timeout(120)
@needs_ccx
async def test_solver_timeout_is_reported_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(sim_tools, "SOLVE_TIMEOUT_S", 0.01)
    ctx = _ctx(tmp_path)
    await _beam(ctx)
    out = await sim_static_run.handler(ctx, StaticArgs(
        part_id="beam", fix="x-min", load={"face": "x-max", "newtons": F, "direction": "-z"}))
    assert out.ok is False and "timed out" in out.summary
    rec = json.loads(next((tmp_path / "parts" / "beam" / "sims").glob("*/result.json")).read_text())
    assert rec["status"] == "failed" and "timed out" in rec["error"]


async def test_env_list_and_result_get_errors(tmp_path):
    ctx = _ctx(tmp_path)
    out = await sim_env_list.handler(ctx, EnvListArgs())
    assert {"environments", "face_presets", "directions", "materials", "solvers"} <= set(out.data)
    assert out.data["solvers"]["ccx"]["install"] == INSTALL_HINT
    assert out.data["knockdown"] == 0.7
    # S2: the optional OpenFOAM solver is reported (conftest: the image reads as not pulled)
    of = out.data["solvers"]["openfoam"]
    assert of["available"] is False and of["why_not"]
    assert "OpenFOAM wind solver unavailable" in out.summary
    with pytest.raises(MisesError) as e:
        await sim_result_get.handler(ctx, ResultArgs(sim_id="sim_000000000000"))
    assert e.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(Exception):
        ResultArgs(sim_id="../../etc")
