"""Assembly studies (sim_run with project_id): the build's assembly is solved (C2), every
instance is meshed at its solved position as its own solid, and instances interact through
contact or bonded pairs. Fast, coarse runs with the real CalculiX."""
import json

import pytest

from mises.assembly.model import AssemblyStore, Frame, Instance, Joint, JointRef
from mises.sim import studies
from mises.sim.assembly_study import assemblies_root
from mises.sim.ccx import find_ccx
from mises.sim.tools import ResultArgs, SimRunArgs, sim_result_get, sim_run

from .test_nonlinear import PLA, _ctx, _part

BLOCK = ('from build123d import *\nVARS={"s":20.0}\ndef build(v):\n'
         '    return Box(v["s"],v["s"],10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
BAR = ('from build123d import *\nVARS={"l":50.0}\ndef build(v):\n'
       '    return Box(v["l"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')


def _move(dx=0.0, dy=0.0, dz=0.0):
    return [[1.0, 0, 0, dx], [0, 1.0, 0, dy], [0, 0, 1.0, dz], [0, 0, 0, 1.0]]


def test_sim_run_targets_exactly_one_of_part_or_build():
    with pytest.raises(Exception, match="exactly one"):
        SimRunArgs(type="static", part_id="a", project_id="p")
    with pytest.raises(Exception, match="exactly one"):
        SimRunArgs(type="static")
    from mises.sim.tools import _run_scope
    assert _run_scope(SimRunArgs(type="static", project_id="p1")) == ["p1", "project:p1"]


async def test_assembly_refusals_before_a_record_exists(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    ctx = _ctx(tmp_path, {"assembly": store})
    with pytest.raises(Exception, match="no assembly instances"):
        await sim_run.handler(ctx, SimRunArgs(type="static", project_id="p1", settings={
            "load_cases": [{"supports": [{"face": "bottom"}],
                            "loads": [{"kind": "force", "face": "top", "newtons": 1,
                                       "direction": "-z"}]}]}))
    store.add_instance("p1", Instance(id="base", part_id="blk", name="base", fixed=True))
    with pytest.raises(Exception, match="not built"):
        await sim_run.handler(ctx, SimRunArgs(type="static", project_id="p1", settings={
            "load_cases": [{"supports": [{"face": "bottom"}],
                            "loads": [{"kind": "force", "face": "top", "newtons": 1,
                                       "direction": "-z"}]}]}))
    await _part(ctx, "blk", BLOCK)
    for faces, msg in ((("f0", "top"), "ambiguous"), (("f99@base", "top"), "no face f99@base"),
                       (("bottom", "f1@nope"), "no face f1@nope")):
        with pytest.raises(Exception, match=msg):
            await sim_run.handler(ctx, SimRunArgs(type="static", project_id="p1", settings={
                "load_cases": [{"supports": [{"face": faces[0]}],
                                "loads": [{"kind": "force", "face": faces[1], "newtons": 1,
                                           "direction": "-z"}]}]}))
    with pytest.raises(Exception, match="static or wind only"):
        await sim_run.handler(ctx, SimRunArgs(type="modal", project_id="p1", settings={}))
    assert not assemblies_root(tmp_path / "parts").exists()


needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")


@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_fast_assembly_contact_carries_the_load_between_instances(tmp_path):
    """A lid placed on a base by a fixed joint (C2 solve), pressed down: the load crosses
    the (frictionless) instance contact as a pressure of about F / A, and the lid's top moves
    by the two blocks' compression. Symmetry on x = 0 and y = 0 (a quarter of a 40 x 40
    stack) holds the lid sideways."""
    store = AssemblyStore(tmp_path / "assembly.db")
    ctx = _ctx(tmp_path, {"assembly": store})
    await _part(ctx, "blk", BLOCK)
    store.add_instance("p1", Instance(id="base", part_id="blk", name="base", fixed=True))
    store.add_instance("p1", Instance(id="lid", part_id="blk", name="lid"))
    store.add_joint("p1", Joint(kind="fixed",
                                a=JointRef(instance_id="base",
                                           frame=Frame(origin=[10, 10, 10], axis=[0, 0, 1])),
                                b=JointRef(instance_id="lid",
                                           frame=Frame(origin=[10, 10, 0], axis=[0, 0, 1]))))
    F, A = 400.0, 400.0
    out = await sim_run.handler(ctx, SimRunArgs(type="static", project_id="p1", settings={
        "load_cases": [{"name": "press", "supports": [
            {"kind": "fixed", "face": "bottom"}, {"kind": "symmetry", "face": "x-min"},
            {"kind": "symmetry", "face": "y-min"}],
                        "loads": [{"kind": "force", "face": "top", "newtons": F,
                                   "direction": "-z"}]}],
        "material": {"name": "PLA"}, "mesh": {"size_mm": 5},
        "nonlinear": {"contacts": [{"a": {"instance": "lid"}, "b": {"instance": "base"}}],
                      "increments": 2}}))
    assert out.ok, out.summary
    d = out.data
    r = d["results"]
    assert d["part_id"] is None and d["fidelity_class"] == "fem_nonlinear"
    assert r["converged"] and r["pairs"][0]["kind"] == "contact"
    assert r["pairs"][0]["a_surfaces"] == 1 and r["pairs"][0]["b_surfaces"] == 1
    sim_dir = assemblies_root(tmp_path / "parts") / "p1" / "sims" / d["sim_id"]
    assert (sim_dir / "result.glb").is_file() and (sim_dir / "fields.npz").is_file()
    from mises.sim.fields import Fields
    f = Fields(sim_dir / "fields.npz")
    mid = f.probe([[10.0, 10.0, 10.0 + 1e-3]])[0]["values"]
    print(f"\nstack contact pressure at the centre {mid['contact_pressure']:.4g} MPa vs F/A "
          f"{F / A:.4g}; max {r['max_contact_pressure_mpa']:.4g}")
    assert mid["contact_pressure"] == pytest.approx(F / A, rel=0.15), mid
    top = f.probe([[10.0, 10.0, 20.0]])[0]["values"]["displacement"]
    uniaxial = F * 20.0 / (PLA.e_mpa * A)            # two 10 mm blocks in series
    assert -top[2] == pytest.approx(uniaxial, rel=0.25), top
    rec = json.loads((sim_dir / "result.json").read_text())
    assert rec["project_id"] == "p1" and {i["id"] for i in rec["instances"]} == {"base", "lid"}
    lid = next(i for i in rec["instances"] if i["id"] == "lid")
    assert lid["transform"][2][3] == pytest.approx(10.0)   # placed by the joint
    # found, listed and read back like a part's study; stale once a part changes
    got = await sim_result_get.handler(ctx, ResultArgs(sim_id=d["sim_id"]))
    assert got.ok and "assembly p1" in got.summary and got.data["stale"] is False
    listed = studies.list_sims(tmp_path / "parts", project_id="p1")
    assert [x["sim_id"] for x in listed] == [d["sim_id"]] and listed[0]["project_id"] == "p1"
    assert d["sim_id"] in [x["sim_id"] for x in studies.list_sims(tmp_path / "parts")]
    assert studies.list_sims(tmp_path / "parts", part_id="blk") == []
    (tmp_path / "parts" / "blk" / "model.step").write_text("changed")
    got = await sim_result_get.handler(ctx, ResultArgs(sim_id=d["sim_id"]))
    assert got.data["stale"] is True


@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_fast_bonded_bars_bend_like_one_beam(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    ctx = _ctx(tmp_path, {"assembly": store})
    await _part(ctx, "bar", BAR)
    store.add_instance("p2", Instance(id="root", part_id="bar", name="root", fixed=True))
    store.add_instance("p2", Instance(id="tip", part_id="bar", name="tip", fixed=True,
                                      transform=_move(dx=50.0)))
    P = 10.0
    out = await sim_run.handler(ctx, SimRunArgs(type="static", project_id="p2", settings={
        "load_cases": [{"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
                        "loads": [{"kind": "force", "face": "x-max", "newtons": P,
                                   "direction": "-z"}]}],
        "material": {"name": "PLA"}, "mesh": {"size_mm": 5},
        "nonlinear": {"contacts": [{"kind": "bonded", "a": {"instance": "tip"},
                                    "b": {"instance": "root"}}]}}))
    assert out.ok, out.summary
    r = out.data["results"]
    beam = P * 100.0 ** 3 / (3 * PLA.e_mpa * 10.0 ** 4 / 12)    # 1.74 mm
    print(f"\nbonded bars tip {r['max_displacement_mm']:.4g} mm vs PL^3/3EI {beam:.4g} mm")
    assert r["max_displacement_mm"] == pytest.approx(beam, rel=0.10), r["max_displacement_mm"]
    assert r["pairs"][0]["kind"] == "bonded"


@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_floating_instance_is_refused(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    ctx = _ctx(tmp_path, {"assembly": store})
    await _part(ctx, "bar", BAR)
    store.add_instance("p3", Instance(id="root", part_id="bar", name="root", fixed=True))
    store.add_instance("p3", Instance(id="loose", part_id="bar", name="loose", fixed=True,
                                      transform=_move(dx=10.0, dy=30.0)))
    out = await sim_run.handler(ctx, SimRunArgs(type="static", project_id="p3", settings={
        "load_cases": [{"supports": [{"kind": "fixed", "face": "x-min"}],
                        "loads": [{"kind": "force", "face": "x-max", "newtons": 1,
                                   "direction": "-z"}]}],
        "mesh": {"size_mm": 5}}))
    assert not out.ok and "loose" in out.summary and "neither supported" in out.summary
