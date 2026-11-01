"""The dyn_* and engineering-check tools end to end on the quadruped fixture (Plan 10 C3): the
squat integration test (< 30 s), the checks record and the done gate, and dyn_to_fem's load
cases (the CalculiX run itself is faked here; the proof runs it for real)."""
from types import SimpleNamespace

import pytest

from mises.assembly.model import AssemblyStore
from mises.dyn import tools as dt
from mises.engineering import eng_tools as et
from mises.engineering import pipeline
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass, ToolOutput

from . import quad

pytestmark = pytest.mark.slow


class _Events:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


@pytest.fixture
def ctx(tmp_path):
    quad.write_parts(tmp_path)
    store = AssemblyStore(tmp_path / "assembly.db")
    quad.assembly("quad", store)
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id=None, events=_Events(),
                           services={"assembly": store}, policy=pol, call_id="c1",
                           store=None, agent_call=False)


def _faces(tmp_path):
    """Give the fixture parts bore faces at their joints (what a real build reports)."""
    import json
    for pid, (lo, hi) in quad.part_boxes().items():
        p = tmp_path / "parts" / pid / "metrics.json"
        m = json.loads(p.read_text())
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        faces = [{"id": "f0", "type": "plane", "center": [cx, cy, lo[2]], "normal": [0, 0, -1],
                  "area": 100.0},
                 {"id": "f1", "type": "plane", "center": [cx, cy, hi[2]], "normal": [0, 0, 1],
                  "area": 100.0}]
        if "thigh" in pid or "shank" in pid:
            faces.append({"id": "f2", "type": "cylinder", "center": [cx, cy, hi[2]],
                          "normal": None, "area": 50.0, "radius": 4.0})
        if "thigh" in pid:
            faces.append({"id": "f3", "type": "cylinder", "center": [cx, cy, lo[2]],
                          "normal": None, "area": 50.0, "radius": 4.0})
        m["face_list"] = faces
        p.write_text(json.dumps(m))


@pytest.mark.timeout(60)
async def test_quadruped_squat_pipeline(ctx):
    """The squat integration test: build, torque, stability, ROM, static sizing, electronics,
    power and the done gate on a 2.6 kg quadruped with MG996R knees (< 30 s)."""
    import time
    t0 = time.perf_counter()
    acts = {f"{leg}_{j}": "mg996r" for leg in quad.LEGS for j in ("hip", "knee")}
    b = await dt.dyn_build.handler(ctx, dt.DynArgs(project_id="quad", actuators=acts))
    assert b.data["bodies"] == 9 and b.data["base"] == "free"
    tq = await dt.dyn_torque.handler(ctx, dt.TorqueArgs(
        project_id="quad", actuators=acts,
        trajectory=dt.Trajectory(kind="squat", depth_mm=50, period_s=1.5)))
    rows = {r["joint_id"]: r for r in tq.data["joints"]}
    assert set(rows) == set(acts)
    knee = rows["fl_knee"]
    assert knee["actuator"] == "servo_mg996r_standard"
    # ~0.8 N m at a 1.08 N m stall: over the 0.6 margin (research §3.1's MG996R verdict)
    assert 0.5 < knee["peak_nm"] < 1.08
    assert knee["status"] in ("warn", "fail") and knee["candidates"]
    assert tq.data["card"]["accuracy"] and tq.data["stream"] == "sim"
    assert any(e.type == "dyn_result" for e in ctx.events.events)
    st = await dt.dyn_stability.handler(ctx, dt.StabilityArgs(project_id="quad",
                                                              tilt_test=False))
    static = next(r for r in st.data["rows"] if r["check"] == "static")
    assert static["status"] == "pass" and static["margin_mm"] > 50
    rom = await dt.dyn_rom_sweep.handler(ctx, dt.RomArgs(project_id="quad", n=300))
    assert rom.data["status"] in ("pass", "fail")
    size = await et.actuator_size.handler(ctx, et.SizeArgs(project_id="quad", actuators=acts))
    srow = {r["joint_id"]: r for r in size.data["joints"]}
    assert srow["fl_knee"]["standing_nm"] > srow["fl_hip"]["standing_nm"]
    assert size.data["dynamic_factor"] == 2.0
    el = await et.electronics_estimate.handler(ctx, et.ElecArgs(project_id="quad",
                                                                actuators=acts))
    roles = [c["role"] for c in el.data["components"]]
    assert "servo_driver" in roles and "battery" in roles and "estop" in roles
    assert el.data["needs_source"]["actuators"] == "dyn_torque"     # fresh RMS torques used
    pw = await et.power_budget.handler(ctx, et.PowerArgs(project_id="quad", actuators=acts))
    assert pw.data["battery"] and pw.data["e_needed_wh"] > 0
    rep = await et.engineering_checks.handler(ctx, et.ChecksArgs(project_id="quad"))
    done = {r["check"]: r["status"] for r in rep.data["rows"]}
    assert done["dyn_torque"] in ("warn", "fail") and done["dyn_to_fem"] == "missing"
    assert done["assembly_health"] == "skipped"            # no C1 tool in this registry
    assert rep.data["done_ok"] is False
    assert time.perf_counter() - t0 < 30


async def test_checks_go_stale_when_the_assembly_moves(ctx):
    await dt.dyn_stability.handler(ctx, dt.StabilityArgs(project_id="quad", tilt_test=False))
    store = ctx.services["assembly"]
    store.update_joint("quad", "fl_hip", value=10.0)
    asm = store.get("quad")
    rep = pipeline.report(ctx.workdir, "quad", asm)
    assert "dyn_stability" in rep["stale"]


async def test_dyn_to_fem_builds_bore_load_cases(ctx, monkeypatch):
    _faces(ctx.workdir)
    calls = []

    async def fake_sim_run(c, payload, k):
        calls.append(payload)
        return ToolOutput(summary="ok", data={"sim_id": f"sim_{k:012d}", "results": {
            "safety_factor": 3.1, "max_von_mises_mpa": 5.0, "max_displacement_mm": 0.1}})
    monkeypatch.setattr(dt, "_sim_run", fake_sim_run)
    out = await dt.dyn_to_fem.handler(ctx, dt.FemArgs(
        project_id="quad", n_links=2, wait=True,
        trajectory=dt.Trajectory(kind="squat", depth_mm=40, period_s=1.5)))
    assert len(calls) == 2 and out.data["status"] == "pass"
    case = calls[0]["settings"]["load_cases"][0]
    assert case["supports"][0]["kind"] == "fixed"
    sup = case["supports"][0]["face"]
    # held at the parent joint's bore (the top bore f2), loaded elsewhere
    assert sup == "f2"
    assert all(ld["face"] != sup for ld in case["loads"])
    assert calls[0]["requirements"][0]["kind"] == "min_safety_factor"
    assert calls[0]["part_id"].endswith(("thigh", "shank"))
    rec = pipeline.load(ctx.workdir, "quad")["dyn_to_fem"]
    assert rec["status"] == "pass" and all(v == 3.1 for v in rec["margins"].values())


async def test_cable_route_and_thermal(ctx):
    out = await et.cable_route.handler(ctx, et.CableArgs(project_id="quad", nets=[
        et.Net(name="fl servo", **{"from": "torso"}, to="fl_shank", amps=2.5)]))
    net = out.data["nets"][0]
    assert net["path"] and net["length_mm"] > 100 and net["awg"] == 24
    assert {x["joint_id"] for x in net["service_loops"]} == {"fl_hip", "fl_knee"}
    th = await et.thermal_estimate.handler(ctx, et.ThermalArgs(
        project_id="quad", power_w=5.0, size_mm=[100, 70, 40], material="PLA"))
    assert th.data["status"] in ("warn", "fail")
