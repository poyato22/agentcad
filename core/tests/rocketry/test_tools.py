"""rocket_analyze / rocket_flight / rocket_motor on a build (Plan 11 F6): the OpenRocket
reference rocket "A" laid out as parts in an assembly, so the CG comes from the parts' B-rep
mass properties exactly as the tools read them on a real build."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from mises.assembly.model import AssemblyStore, Instance
from mises.cad import skeleton as sk
from mises.events import tool_stream
from mises.gate import MAX_OUTPUT_BYTES
from mises.rocketry import motors
from mises.rocketry import tools as rt
from mises.store import AgentPolicy
from mises.tools.registry import ToolRegistry
from mises.types import Caps, MisesError, SafetyClass

from . import reference as ref


@pytest.fixture
def ctx(tmp_path):
    ref.write_parts(tmp_path)
    store = AssemblyStore(tmp_path / "assembly.db")
    for i, pid in enumerate(ref.PARTS_A):
        store.add_instance("rkt", Instance(id=f"i{i}", part_id=pid, name=pid, fixed=i == 0))
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)
    return SimpleNamespace(workdir=tmp_path, run_id=None, events=None,
                           services={"assembly": store}, policy=pol, call_id="c1",
                           store=None, agent_call=False)


def _analyze(ctx, **kw):
    return rt.rocket_analyze.handler(ctx, rt.AnalyzeArgs(project_id="rkt", **kw))


async def test_analyze_cg_and_margin_from_parts_match_openrocket(ctx):
    out = await _analyze(ctx, geometry=ref.geometry("A"), motor="F52C")
    o = ref.OR_FLIGHTS[("A", "F52C")]
    d = out.data
    assert d["dry_mass_g"] == pytest.approx(540.0)                     # motor part replaced
    assert d["dry_cg_mm"] == pytest.approx(ref.OR["A"]["dry_cg"] * 1000, abs=0.5)
    assert d["motor_parts"] == ["motor"]
    assert d["cg_launch_mm"] == pytest.approx(o["wet_cg"] * 1000, abs=1.0)
    assert d["cp_mm"] == pytest.approx(ref.OR["A"]["cp"] * 1000, rel=ref.TOL_CP)
    assert d["margin_launch_cal"] == pytest.approx(o["stability"], abs=0.1)
    assert d["margin_burnout_cal"] > d["margin_launch_cal"]          # CG moves forward
    assert d["status"] == "pass"
    card = d["card"]
    assert card["kind"] == "rocket_analyze" and d["stream"] == "sim"
    assert card["charts"][0]["bands"][0]["from"] == 1.0
    assert {r["term"] for r in d["terms"]} == {"nose (ogive)", "fins (3)"}
    assert len(out.model_dump_json().encode()) < MAX_OUTPUT_BYTES
    fl = d["flutter"][0]
    assert fl["status"] == "pass" and fl["ratio"] > 1.5 and fl["max_v_mps"] == pytest.approx(90, rel=0.03)
    assert "margin 1.8" in out.summary or "margin 1.9" in out.summary


async def test_boattail_rocket_is_flagged_marginal(ctx):
    """OpenRocket says rocket B on a G40W launches at 0.70 cal: a fail here."""
    out = await rt.rocket_analyze.handler(ctx, rt.AnalyzeArgs(
        geometry=ref.geometry("B"), motor="G40W", dry_mass_g=560, cg_mm=508.39,
        motor_aft_mm=934))
    o = ref.OR_FLIGHTS[("B", "G40W")]
    assert out.data["margin_launch_cal"] == pytest.approx(o["stability"], abs=0.1)
    assert out.data["status"] == "fail"
    assert out.data["card"]["metrics"][6]["status"] == "fail"


async def test_mass_overrides_and_extra_masses_move_the_cg(ctx):
    base = await _analyze(ctx, geometry=ref.geometry("A"))
    heavy_nose = await _analyze(ctx, geometry=ref.geometry("A"),
                                mass_overrides_g={"payload": 200},
                                extra_masses=[{"name": "ballast", "mass_g": 30, "at_mm": 150}])
    assert heavy_nose.data["dry_mass_g"] == pytest.approx(540 + 80 + 30)
    assert heavy_nose.data["dry_cg_mm"] < base.data["dry_cg_mm"]
    bad = await _analyze(ctx, geometry=ref.geometry("A"), mass_overrides_g={"nope": 1})
    assert any("nope" in w for w in bad.warnings)


async def test_geometry_from_the_skeleton(ctx, tmp_path):
    params = {"body_od": 66, "nose_len": 200, "nose_base_z": 700, "fin_count": 3,
              "fin_root": 120, "fin_tip": 50, "fin_span": 65, "fin_sweep": 60, "fin_t": 3.2,
              "fin_root_start": 0}
    sk.save(tmp_path / "parts", "rkt", sk.SkeletonData(params=params), 1)
    out = await _analyze(ctx, motor="F52C")
    assert out.data["cp_mm"] == pytest.approx(ref.OR["A"]["cp"] * 1000, rel=ref.TOL_CP)
    assert "geometry from the skeleton" in out.data["card"]["notes"]


async def test_no_geometry_names_what_is_missing(ctx, tmp_path):
    sk.save(tmp_path / "parts", "rkt", sk.SkeletonData(params={"body_od": 66}), 1)
    with pytest.raises(MisesError) as e:
        await _analyze(ctx)
    assert "nose_len" in e.value.hint


async def test_flutter_fails_thin_soft_fins(ctx):
    g = ref.geometry("A")
    g["fins"][0]["thickness_mm"] = 0.8
    out = await _analyze(ctx, geometry=g, motor="G40W", fin_material="PETG")
    assert out.data["flutter"][0]["status"] == "fail"
    assert out.data["status"] == "fail"


async def test_geometry_that_disagrees_with_the_parts_is_warned(ctx):
    g = ref.geometry("A")
    g["sections"][0]["length_mm"] = 1000
    out = await _analyze(ctx, geometry=g)
    assert any("parts span" in w for w in out.warnings)


async def test_flight_compares_motors_targets_and_tunes_mass(ctx):
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motors=["F52C", "G40W"],
        target_apogee_ft=750, target_time_s=[36, 39], tune_mass=True, descent_mps=5.0))
    f52, g40 = out.data["flights"]
    o = ref.OR_FLIGHTS[("A", "F52C")]
    assert f52["apogee_m"] == pytest.approx(o["apogee"], rel=ref.TOL_APOGEE)
    assert g40["apogee_m"] > f52["apogee_m"]
    assert f52["descent_mps"] == pytest.approx(5.0, rel=0.06)
    # 750 ft = 229 m: below the F52C's 312 m -> ballast; the tuned flight hits it
    t = f52["tune"]
    assert t["reachable"] and t["ballast_g"] > 0 and t["margin_launch_cal"] > f52["margin_cal"]
    tuned = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motors=["F52C"],
        extra_masses=[{"name": "ballast", "mass_g": t["ballast_g"], "at_mm": t["at_mm"]}]))
    assert tuned.data["flights"][0]["apogee_ft"] == pytest.approx(750, abs=8)
    # 312 m at 5 m/s is ~70 s, outside 36-39 s: a smaller chute (faster descent) lands it inside
    assert f52["status"] == "warn" and f52["flight_time_s"] > 39
    assert f52["chute_for_time_mm"] < f52["chute_mm"] and f52["descent_for_time_mps"] > 5
    card = out.data["card"]
    assert card["kind"] == "rocket_flight" and len(card["charts"]) == 3
    assert card["charts"][0]["series"][0]["name"] == "F52C"
    assert len(card["charts"][0]["series"][0]["points"]) <= rt.CHART_POINTS + 1
    assert "F52C apogee" in out.summary and "target 750 ft" in out.summary


async def test_flight_on_a_given_eng(ctx):
    eng = (motors.BUNDLED_DIR / "AeroTech_F52C.eng").read_text().replace("F52C", "F52Z")
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motor_eng=eng))
    assert out.data["flights"][0]["motor"] == "F52Z"


async def test_unreachable_target_says_change_the_motor(ctx):
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motors=["F44W"], target_apogee_m=2000,
        tune_mass=True))
    t = out.data["flights"][0]["tune"]
    assert not t["reachable"] and "change the motor" in t["note"]


def test_every_lead_is_told_to_fly_rocket_builds():
    from mises.agent.definitions import AgentDefinition
    from mises.orchestrator.lead import lead_definition
    lead = lead_definition(AgentDefinition(name="designer", description="d", body="# Role",
                                           tools=["mirror_part_create"]), rocket=True)
    assert {"rocket_analyze", "rocket_flight", "rocket_motor"} <= set(lead.tools)
    body = lead.body
    assert "CURRENT rules" in body and "rocketrychallenge.org" in body
    assert "static margin (cal), apogee" in body and body.count("## Rocket builds") == 1


def test_a_non_rocket_lead_gets_one_line_not_the_rules():
    from mises.agent.definitions import AgentDefinition
    from mises.orchestrator.lead import is_rocket_text, lead_definition
    lead = lead_definition(AgentDefinition(name="designer", description="d", body="# Role",
                                           tools=["mirror_part_create"]))
    assert "rocketrychallenge" not in lead.body and "ARC" not in lead.body
    assert "egg" not in lead.body and lead.body.count("rocket_analyze") == 1
    assert "rocket_flight" in lead.tools           # the tools stay offered (READ, cheap)
    for t in ("ARC model rocket carrying 2 eggs", "a TARC entry", "egg lofter on an F39",
              "a 3D-printed nose cone and fins"):
        assert is_rocket_text(t), t
    for t in ("a 6-axis robot arm", "sketch an arc and extrude it", "a CNC spindle mount",
              "a camera gimbal"):
        assert not is_rocket_text(t), t


def test_rocket_build_is_read_from_the_build_name_and_spec():
    from mises.orchestrator.lead import is_rocket_build
    from mises.store import Store
    store = Store(":memory:")
    p = store.create_project("Rocket").id
    q = store.create_project("Robot arm").id
    assert is_rocket_build(store, p, "make the fins") and not is_rocket_build(store, q, "make it")
    store.put_spec(q, {"goal": "ARC model rocket, 2 eggs, 750 ft"})
    assert is_rocket_build(store, q, "make it")


async def test_four_motor_flight_fits_the_gates_output_cap(ctx):
    """Over 16 KB the gate spills the result to a file and the card would be lost."""
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motors=["F44W", "F52C", "F67C", "G40W"],
        target_apogee_ft=750, target_time_s=[36, 39], tune_mass=True, wind_mps=4))
    assert len(out.data["flights"]) == 4
    assert len(out.model_dump_json().encode()) < MAX_OUTPUT_BYTES


async def test_tuning_down_says_how_much_to_remove(ctx):
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motors=["F52C"], target_apogee_m=330,
        tune_mass=True))
    t = out.data["flights"][0]["tune"]
    assert t["reachable"] and t["ballast_g"] < 0 and "remove" in t["note"]
    assert "margin_launch_cal" not in t


def test_flight_args_need_a_motor():
    with pytest.raises(ValueError):
        rt.FlightArgs(project_id="rkt")


def test_rocket_tools_ride_the_sim_stream_and_register():
    reg = ToolRegistry()
    rt.register_rocket_tools(reg)
    for name in ("rocket_analyze", "rocket_flight", "rocket_motor"):
        assert reg.get(name).safety == SafetyClass.READ
    assert tool_stream("mcp__mises__rocket_flight") == "sim"
    assert tool_stream("rocket_analyze") == "sim"
    assert tool_stream("rocket_motor") == "text"


# ---- rocket_motor

async def test_motor_list_get_save(ctx, tmp_path):
    out = await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="list"))
    assert {"F52C", "G40W"} <= {m["designation"] for m in out.data["motors"]}
    got = await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="get", name="G40"))
    assert got.data["designation"] == "G40W" and got.data["curve"][0] == [0.0, 0.0]
    eng = "X12T 29 100 6 0.02 0.06 Test\n 0.1 12\n 1.0 12\n 1.1 0\n"
    await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="save", eng=eng))
    assert motors.find("X12T", tmp_path) is not None


class _Web:
    def __init__(self, pages):
        self.pages, self.urls = pages, []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req):
        url = str(req.url.copy_with(host=req.headers["host"]))
        self.urls.append(url)
        for prefix, (ctype, body) in self.pages.items():
            if url.startswith(prefix):
                if ctype == "redirect":
                    return httpx.Response(302, headers={"Location": body})
                return httpx.Response(200, content=body.encode(), headers={"Content-Type": ctype})
        return httpx.Response(403, content=b"challenge", headers={"Content-Type": "text/html"})


async def _resolve(host):
    return ["93.184.216.34"]


F39 = ("; Aerotech F39 test copy\nF39T 24 70 3-4-5-6-7-9 0.0227 0.059 AT\n 0.05 40\n 0.5 45\n"
       " 1.0 40\n 1.2 0\n")


async def test_fetch_from_thrustcurve_api(ctx, tmp_path):
    web = _Web({
        "https://www.thrustcurve.org/api/v1/search.json": (
            "application/json", json.dumps({"results": [{"motorId": "m1", "designation": "F39T",
                                                         "availability": "regular"}]})),
        "https://www.thrustcurve.org/api/v1/download.json": (
            "application/json", json.dumps({"results": [{
                "simfileId": "5f4294d20002e90000000287", "format": "RASP", "source": "cert", "license": "PD",
                # the simfile's header names it "F39": the catalog's "F39T" is kept
                "data": base64.b64encode(F39.replace("F39T 24", "F39 24").encode()).decode()}]})),
    })
    ctx.services["browser_http"] = {"transport": web.transport, "resolve": _resolve}
    out = await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name="F39"))
    assert out.data["designation"] == "F39T" and out.data["license"] == "PD"
    assert "commonName=F39" in web.urls[0]
    m = motors.find("F39", tmp_path)
    assert m is not None and m.url == "https://www.thrustcurve.org/simfiles/5f4294d20002e90000000287/"


async def test_fetch_falls_back_to_the_openrocket_mirror(ctx, tmp_path):
    listing = [{"name": "F39_abc123.eng", "download_url":
                "https://raw.githubusercontent.com/openrocket/motor-database/main/data/"
                "thrustcurve.org/AeroTech/F39_abc123.eng"},
               {"name": "F390_x.eng", "download_url": "https://example.invalid/x"}]
    web = _Web({
        "https://api.github.com/repos/openrocket/motor-database/contents/data/thrustcurve.org/"
        "AeroTech": ("application/json", json.dumps(listing)),
        "https://raw.githubusercontent.com/openrocket/": ("text/plain", F39),
    })
    ctx.services["browser_http"] = {"transport": web.transport, "resolve": _resolve}
    out = await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name="F39T",
                                                          manufacturer="AeroTech"))
    assert out.data["designation"] == "F39T"
    assert "mirror" in out.summary or "OpenRocket" in out.summary
    assert motors.find("F39T", tmp_path).url.endswith("/simfiles/abc123/")


async def test_fetch_failure_says_how_to_paste_it(ctx):
    web = _Web({})
    ctx.services["browser_http"] = {"transport": web.transport, "resolve": _resolve}
    with pytest.raises(MisesError) as e:
        await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name="G64"))
    assert "web_open" in e.value.hint
