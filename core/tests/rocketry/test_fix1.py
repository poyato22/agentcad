"""Plan 11 F6 fix round 1: the browser guard on motor fetches (I2), absurd flights (I1, M4),
cached curves shadowing bundled ones (M3) and the rocket prompt only on rocket builds (I3)."""
import json
import math
from types import SimpleNamespace

import pytest

from mises.rocketry import motors
from mises.rocketry import tools as rt
from mises.types import MisesError

from . import reference as ref
from .test_tools import F39, _resolve, _Web, ctx  # noqa: F401 - ctx is a fixture


# ---- I2: motor fetches obey the agent browser's rules

async def test_fetch_never_follows_a_redirect_off_the_motor_hosts(ctx):
    web = _Web({"https://www.thrustcurve.org/api/v1/search.json": (
        "redirect", "https://evil.example/collect?d=1")})
    ctx.services["browser_http"] = {"transport": web.transport, "resolve": _resolve}
    with pytest.raises(MisesError):
        await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name="F39T"))
    assert not any("evil.example" in u for u in web.urls)


async def test_mirror_download_url_must_be_the_mirrors_own(ctx):
    listing = [{"name": "F39_abc123.eng", "download_url": "https://evil.example/F39.eng"}]
    web = _Web({"https://api.github.com/repos/openrocket/motor-database/contents/data/"
                "thrustcurve.org/AeroTech": ("application/json", json.dumps(listing)),
                "https://evil.example/": ("text/plain", F39)})
    ctx.services["browser_http"] = {"transport": web.transport, "resolve": _resolve}
    with pytest.raises(MisesError):
        await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name="F39T",
                                                        manufacturer="AeroTech"))
    assert not any("evil.example" in u for u in web.urls)


@pytest.mark.parametrize("name", ["F39T?x=1", "aGVsbG8gd29ybGQgc2VjcmV0", "../../etc"])
async def test_fetch_refuses_names_that_are_not_motor_names(ctx, name):
    with pytest.raises(MisesError) as e:
        await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name=name))
    assert "plain motor name" in e.value.message


async def test_fetch_is_charged_to_the_web_budget(ctx):
    class Snaps:
        used = 0

        def charge(self, run_id, n, cap):
            if self.used + n > 1:
                return None
            self.used += n
            return self.used
    snaps = Snaps()
    ctx.store = SimpleNamespace(run_family=lambda rid: ("root", []))
    ctx.services["browser"] = snaps
    web = _Web({"https://www.thrustcurve.org/api/v1/search.json": (
        "application/json", json.dumps({"results": [{"motorId": "m1", "designation": "F39T"}]}))})
    ctx.services["browser_http"] = {"transport": web.transport, "resolve": _resolve}
    with pytest.raises(MisesError) as e:
        await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="fetch", name="F39T"))
    assert e.value.code.value == "budget_exhausted" and snaps.used == 1
    assert len(web.urls) == 1                     # nothing fetched past the budget


def test_remote_metadata_is_clamped():
    assert rt._meta("PD<script>alert(1)</script>" * 10, 20) == "PDscriptalert(1)/scr"
    assert rt._meta(None) is None


# ---- M3: a saved curve that replaces a bundled one is named on the card

async def test_a_saved_curve_shadowing_a_bundled_one_is_named(ctx):
    eng = (motors.BUNDLED_DIR / "AeroTech_F52C.eng").read_text()
    await rt.rocket_motor.handler(ctx, rt.MotorArgs(action="save", eng=eng))
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        project_id="rkt", geometry=ref.geometry("A"), motors=["F52C"]))
    assert any("replaces the bundled one" in w for w in out.warnings)
    assert out.data["flights"][0]["motor_source"] != "bundled"


# ---- I1 / M4: absurd flights never become numbers on a card

async def test_big_chute_at_burnout_flies_finite_numbers(ctx):
    """The reviewer's case: 0.3 kg, a 2.5 m chute opening at burnout (delay 0) on a G40W
    diverged the fixed-step RK4 (descent -168371 m/s, landing 1.6e56 m/s)."""
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        geometry=ref.geometry("A"), dry_mass_g=300, cg_mm=450, motors=["G40W"],
        chute_diameter_mm=2500, delay_s=0))
    f = out.data["flights"][0]
    assert 0 < f["descent_mps"] < 3 and 0 < f["landing_v_mps"] < 3
    assert 0 < f["deploy_shock_g"] < 2000 and f["apogee_m"] > 20
    assert all(math.isfinite(v) for v in f.values() if isinstance(v, float))


def test_tiny_rocket_huge_chute_is_finite():
    from mises.rocketry.flight import FlightSetup, simulate
    r = simulate(FlightSetup(ref.body(), motors.find("G40W", None), 0.2, chute_d_m=5.0,
                             delay_s=0.0, chute_cd=2.5))
    assert math.isfinite(r.landing_v_mps) and r.landing_v_mps < 3
    assert math.isfinite(r.deploy_shock_g)


async def test_a_diverged_flight_is_a_failed_call_not_numbers(ctx, monkeypatch):
    from mises.rocketry import flight
    monkeypatch.setattr(flight, "V_ABSURD", 5.0)
    with pytest.raises(MisesError) as e:
        await rt.rocket_flight.handler(ctx, rt.FlightArgs(
            geometry=ref.geometry("A"), dry_mass_g=540, cg_mm=493, motors=["F52C"]))
    assert e.value.code.value == "tool_failed" and "diverged" in e.value.message


async def test_a_rocket_that_cannot_lift_off_fails(ctx):
    out = await rt.rocket_flight.handler(ctx, rt.FlightArgs(
        geometry=ref.geometry("A"), dry_mass_g=9000, cg_mm=493, motors=["F44W"]))
    assert out.data["flights"][0]["status"] == "fail"
    assert any("does not lift off" in w for w in out.warnings)
