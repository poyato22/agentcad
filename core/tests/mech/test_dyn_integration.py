"""Plan 10 contract end to end (C2 -> C3): a spur pair inserted by mech_insert (real parts built in
the sandbox) exports to MuJoCo through dyn_build, and driving the input turns the output by the
gear ratio (-z1/z2), with the coupling read in C2's degree units."""
import math
from types import SimpleNamespace

import pytest

from mises.assembly.model import AssemblyStore
from mises.events import EventBus
from mises.mech import tools as mt


@pytest.mark.slow
@pytest.mark.timeout(400)
async def test_gear_pair_from_the_library_runs_coupled_in_mujoco(tmp_path):
    mujoco = pytest.importorskip("mujoco")
    from mises.dyn import export, mjmodel
    from mises.dyn.tools import DynArgs, dyn_build

    store = AssemblyStore(tmp_path / "assembly.db")
    ctx = SimpleNamespace(workdir=tmp_path, events=EventBus(), services={"assembly": store},
                          run_id=None, store=None)
    out = await mt.mech_insert.handler(ctx, mt.InsertArgs(project_id="p", mech_id="spur_pair",
                                                          params={"z1": 20, "z2": 40}))
    card = out.data["card"]
    jin, jout = card["joint_ids"]["in_rev"], card["joint_ids"]["out_rev"]

    built = await dyn_build.handler(ctx, DynArgs(project_id="p"))
    assert built.data["constraints"] >= 1                     # the gear equality
    assert jin in {a["joint_id"] for a in built.data["actuators"]}

    ex = export.export(tmp_path, None, store.get("p"))
    eq = next(e for e in ex.model["equalities"] if e["type"] == "joint")
    assert eq["polycoef"][1] == pytest.approx(-0.5)
    ex.model["gravity"] = [0, 0, 0]
    m, d = mjmodel.compile_model(ex.model)
    act = next(k for k, v in ex.map["actuators"].items() if v["joint_id"] == jin)
    a = m.actuator(act).id
    for _ in range(4000):
        d.ctrl[a] = math.radians(30.0)
        mujoco.mj_step(m, d)
    names = {v["joint_id"]: k for k, v in ex.map["joints"].items()}
    q_in = float(d.qpos[m.jnt_qposadr[m.joint(names[jin]).id]])
    q_out = float(d.qpos[m.jnt_qposadr[m.joint(names[jout]).id]])
    assert math.degrees(q_in) == pytest.approx(30.0, abs=0.5)
    assert q_out == pytest.approx(-0.5 * q_in, abs=math.radians(0.3))
