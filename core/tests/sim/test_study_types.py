"""Every CalculiX study type end to end against analytic results. Needs a real `ccx`."""
import asyncio
import json
import math
import os
from types import SimpleNamespace

import numpy as np
import pytest

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.sim import studies
from mises.sim import tools as sim_tools
from mises.sim.ccx import find_ccx
from mises.sim.fields import Fields
from mises.sim.results import read_glb
from mises.sim.tools import ResultArgs, SimRunArgs, sim_cancel, sim_run
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")]

BEAM = ('from build123d import *\nVARS={"length":100.0}\ndef build(v):\n'
        '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
COLUMN = ('from build123d import *\nVARS={"h":100.0}\ndef build(v):\n'
          '    return Box(10,10,v["h"],align=(Align.MIN,Align.MIN,Align.MIN))\n')
PLA_E, PLA_RHO = 2300.0, 1.24e-9   # MPa, t/mm^3


class _Events:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


def _ctx(tmp_path):
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id="r1", events=_Events(), services={},
                           policy=pol, call_id="c1")


async def _part(ctx, pid, script):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id=pid, script=script))
    assert r.ok, r.summary


async def _run(ctx, type_, pid, settings, **kw):
    return await sim_run.handler(ctx, SimRunArgs(type=type_, part_id=pid, settings=settings,
                                                 **kw))


@pytest.mark.timeout(240)
async def test_modal_cantilever_first_frequency_matches_beam_theory(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "modal", "beam", {
        "supports": [{"kind": "fixed", "face": "x-min"}], "n_modes": 4,
        "excitation": {"rpm": 13200, "blades": 1, "margin_pct": 10}, "material": {"name": "PLA"}})
    assert out.ok, out.summary
    r = out.data["results"]
    L, b = 100.0, 10.0
    I, A = b ** 4 / 12, b * b
    f1 = 1.8751 ** 2 / (2 * math.pi) * math.sqrt(PLA_E * I / (PLA_RHO * A * L ** 4))
    assert abs(r["first_freq_hz"] - f1) / f1 < 0.10, (r["first_freq_hz"], f1)
    # the square section gives two equal bending modes
    assert r["frequencies_hz"][1] == pytest.approx(r["frequencies_hz"][0], rel=0.03)
    assert r["band_hz"] == pytest.approx([198.0, 242.0])  # 13200 rpm / 60 x 1 +- 10%
    assert r["modes_in_band"] >= 1 and any("excitation band" in w for w in out.data["warnings"])
    assert out.data["fidelity_class"] == "fem" and out.data["type"] == "modal"
    assert out.data["guide"] is True and r["error_band"] is not None
    sim_dir = tmp_path / "parts" / "beam" / "sims" / out.data["sim_id"]
    gltf, _ = read_glb(sim_dir / "result.glb")
    chans = gltf["asset"]["extras"]["mises_sim"]["channels"]
    assert chans[0]["attribute"] == "_MODE_1" and chans[0]["kind"] == "vector"
    assert "_MODE_1" in gltf["meshes"][0]["primitives"][0]["attributes"]
    assert set(Fields(sim_dir / "fields.npz").channels()) >= {"mode_1", "mode_2"}
    assert out.data["card"]["metrics"][0]["key"] == "first_freq_hz"


@pytest.mark.timeout(240)
async def test_buckling_euler_column(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "col", COLUMN)
    F = 100.0
    out = await _run(ctx, "buckling", "col", {
        "load_case": {"name": "axial", "supports": [{"kind": "fixed", "face": "bottom"}],
                      "loads": [{"kind": "force", "face": "top", "newtons": F,
                                 "direction": "-z"}]},
        "n_modes": 2, "material": {"name": "PLA"}})
    assert out.ok, out.summary
    r = out.data["results"]
    I, L = 10.0 ** 4 / 12, 100.0
    p_cr = math.pi ** 2 * PLA_E * I / (4 * L ** 2)   # fixed-free Euler column
    assert abs(r["first_factor"] - p_cr / F) / (p_cr / F) < 0.15, (r["first_factor"], p_cr / F)
    assert r["critical_load_n"] == pytest.approx(r["first_factor"] * F)


@pytest.mark.timeout(240)
async def test_thermal_one_dimensional_conduction(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "bar", BEAM)
    q = 100.0  # W/m2 into the hot end, the other end held at 20 C, the sides insulated
    out = await _run(ctx, "thermal", "bar", {
        "temperatures": [{"face": "x-min", "temp_c": 20}],
        "heat": [{"face": "x-max", "w_m2": q}], "material": {"name": "PLA"}})
    assert out.ok, out.summary
    r = out.data["results"]
    k, L = 0.13, 0.1  # W/m K, m
    analytic = 20 + q * L / k
    assert abs(r["max_temp_c"] - analytic) / (analytic - 20) < 0.05, (r["max_temp_c"], analytic)
    assert r["min_temp_c"] == pytest.approx(20, abs=0.5)
    assert any("heat deflection" in w for w in out.data["warnings"])  # 97 C > PLA's 55 C HDT
    f = Fields(tmp_path / "parts" / "bar" / "sims" / out.data["sim_id"] / "fields.npz")
    mid = f.probe([[50.0, 5.0, 5.0]])[0]
    assert mid["inside"] and mid["values"]["temperature"] == pytest.approx(
        20 + q * 0.05 / k, rel=0.03)
    # total watts on the face gives the same answer: 100 W/m2 x 1e-4 m2 = 0.01 W
    out2 = await _run(ctx, "thermal", "bar", {
        "temperatures": [{"face": "x-min", "temp_c": 20}],
        "heat": [{"face": "x-max", "watts": 0.01}], "material": {"name": "PLA"}})
    assert out2.data["results"]["max_temp_c"] == pytest.approx(r["max_temp_c"], rel=0.01)


@pytest.mark.timeout(300)
async def test_static_two_cases_supports_and_load_kinds(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "static", "beam", {"load_cases": [
        {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
         "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]},
        {"name": "moment", "supports": [{"kind": "fixed", "face": "x-min"}],
         "loads": [{"kind": "moment", "face": "x-max", "n_mm": 1000, "axis": "+y"}]},
        {"name": "gravity", "supports": [{"kind": "fixed", "face": "x-min"},
                                         {"kind": "symmetry", "face": "y-min"}],
         "loads": [{"kind": "gravity", "g": 100, "direction": "-z"},
                   {"kind": "pressure", "face": "top", "mpa": 0.001}]}],
        "material": {"name": "PLA", "fdm": {"infill_pct": 40, "walls": 2,
                                            "layer_orientation": "z"}}})
    assert out.ok, out.summary
    r = out.data["results"]
    assert [c["name"] for c in r["cases"]] == ["tip", "moment", "gravity"]
    by = {c["name"]: c for c in r["cases"]}
    # pure moment M on a 10x10 section: sigma = 6 M / (b h^2) = 6 MPa everywhere along the beam
    assert by["moment"]["max_von_mises_mpa"] == pytest.approx(6.0, rel=0.15)
    assert by["tip"]["max_von_mises_mpa"] == pytest.approx(6.0, rel=0.15)
    assert r["governing_case"] in by and set(r["sf_by_case"]) == set(by)
    m = out.data["material"]
    assert m["stiffness_factor"] < 1 and m["knockdown"] < 0.7 and "approximate" in m["source"]
    # a softer (infill) model deflects more than the solid beam's F L^3 / 3 E I
    solid = 10 * 100 ** 3 / (3 * PLA_E * 10 ** 4 / 12)
    assert by["tip"]["max_displacement_mm"] > 1.2 * solid
    sim_dir = tmp_path / "parts" / "beam" / "sims" / out.data["sim_id"]
    gltf, _ = read_glb(sim_dir / "result.glb")
    names = {c["name"] for c in gltf["asset"]["extras"]["mises_sim"]["channels"]}
    assert {"von_mises", "displacement", "s1", "s3", "safety_factor"} <= names
    f = Fields(sim_dir / "fields.npz")
    sec = f.section(2, 5.0, "von_mises", n=12)
    assert sec["axis"] == "z" and len(sec["values"]) == 12


@pytest.mark.timeout(400)
async def test_convergence_loop_stops_and_records_history(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "static", "beam", {
        "load_cases": [{"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
                        "loads": [{"kind": "force", "face": "x-max", "newtons": 10,
                                   "direction": "-z"}]}],
        "convergence": {"enabled": True, "tol_pct": 50, "max_levels": 4}})
    assert out.ok, out.summary
    # the tool result is compact; the history is in the record (/sims/{id}, /charts)
    assert out.data["results"]["convergence"]["converged"] is True
    rec = json.loads((tmp_path / "parts" / "beam" / "sims" / out.data["sim_id"]
                      / "result.json").read_text())
    conv = rec["results"]["convergence"]
    assert conv["converged"] is True and 2 <= len(conv["history"]) < 4
    assert out.data["results"]["convergence"]["levels"] == len(conv["history"])
    assert conv["history"][-1]["change"] < 0.5


@pytest.mark.timeout(240)
async def test_drop_is_an_estimate_from_impact_equivalent(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "drop", "beam", {"impact_face": "bottom", "drop_m": 1.0, "k": 20})
    assert out.ok, out.summary
    imp = out.data["results"]["impact"]
    mass_kg = 100 * 10 * 10 * 1.24 / 1000 / 1000
    assert imp["newtons"] == pytest.approx(mass_kg * 9.81 * 20, rel=1e-3)
    assert imp["estimate"] is True and "m g k" in imp["formula"].replace("·", " ")
    assert out.data["fidelity_class"] == "fem_estimate"
    assert out.summary.startswith("ESTIMATE") and "GUIDE ONLY" in out.summary
    assert out.data["warnings"][0].startswith("ESTIMATE")


@pytest.mark.timeout(120)
async def test_cancel_kills_the_solver_process_group(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "ccx"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n[ \"$1\" = -v ] && echo Version 2.23 && exit 0\n"
                    "sleep 60 &\necho $! > child.pid\necho $$ > job.pid\nwait\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: str(fake))
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    task = asyncio.create_task(_run(ctx, "modal", "beam", {"supports": [
        {"kind": "fixed", "face": "x-min"}]}))
    pid = child = None
    for _ in range(600):
        await asyncio.sleep(0.05)
        for p in (tmp_path / "sim_tmp").glob("*/coarse/child.pid"):
            if p.read_text().strip() and (p.parent / "job.pid").exists():
                child, pid = int(p.read_text()), int((p.parent / "job.pid").read_text())
        if pid:
            break
    assert pid, "solver never started"
    sim_id = studies.active_ids()[0]
    c = await sim_cancel.handler(ctx, ResultArgs(sim_id=sim_id))
    assert c.ok
    out = await task
    assert out.ok is False and out.data["error"] == "cancelled"
    await asyncio.sleep(0.2)
    for p in (pid, child):  # the whole process group is gone, not just the shell
        with pytest.raises(ProcessLookupError):
            os.kill(p, 0)
    rec = json.loads((tmp_path / "parts" / "beam" / "sims" / sim_id / "result.json").read_text())
    assert rec["status"] == "cancelled"
    again = await sim_cancel.handler(ctx, ResultArgs(sim_id=sim_id))
    assert again.ok is False


@pytest.mark.timeout(240)
async def test_progress_events_carry_phase_and_pct(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "modal", "beam", {"supports": [{"kind": "fixed", "face": "x-min"}],
                                            "n_modes": 2}, reason="check vibration")
    assert out.ok
    evs = [e.data for e in ctx.events.events if e.type == "sim_progress"]
    phases = [e["phase"] for e in evs]
    assert {"mesh", "solve", "post"} <= set(phases)
    pcts = [e["pct"] for e in evs]
    assert pcts == sorted(pcts) and all(e["sim_id"] == out.data["sim_id"] for e in evs)
    assert out.data["reason"] == "check vibration" and out.data["card"]["reason"]
    assert out.data["card"]["urls"]["charts"] == f"/sims/{out.data['sim_id']}/charts"
    assert np.isfinite(out.data["results"]["first_freq_hz"])
