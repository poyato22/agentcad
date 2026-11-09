"""Agent-built simulators, phase 1 (plan 2026-11-06 S1 S2 S3 S6 S9): a request is refused when
an existing study covers it; its cost estimate comes from recorded usage (comparable builds,
else turns x a stated turn count, else a stated prior) with the basis said; the user decides
once (409 after), agents never decide; a build pauses at 1.5x the high estimate."""
import asyncio

import pytest
from starlette.testclient import TestClient

from mises.sim import requests as sr
from mises.sim.requests import SimRequest, SimRequests
from mises.store import Store
from mises.types import AlreadyDecided, MisesError

EXISTING = [{"id": "static", "name": "Static stress", "source": "sim_types_list"},
            {"id": "drop", "name": "Drop test", "source": "sim_types_list"},
            {"id": "walk", "name": "Walking gait", "source": "assembly_study_kinds"}]


def _req(**kw) -> SimRequest:
    base = dict(
        name="flapping_wing_aero",
        computes="mean lift and thrust of a flapping wing over one stroke cycle",
        inputs=[{"name": "flap frequency", "unit": "Hz"}, {"name": "span", "unit": "m"}],
        outputs=[{"name": "mean lift", "unit": "N"}],
        why="the ornithopter spec check 'lift >= weight' is unverified: no aero study exists",
        method="blade-element flapping model (quasi-steady, Sane & Dickinson 2002)",
        dependencies=[{"name": "numpy", "manager": "pip", "licence": "BSD-3-Clause",
                       "size_mb": 18.0}],
        validation_cases=[{"name": "fruit fly hover", "inputs": {"f": 200},
                           "expected": {"mean lift": 1.0e-5}, "tolerance_pct": 20,
                           "source": "Sane & Dickinson 2002, Robofly"}],
        fidelity_class="reduced_order", complexity="small")
    return SimRequest.model_validate({**base, **kw})


def _store(tmp_path) -> Store:
    return Store(tmp_path / "mises.db")


def _turn(s: Store, usd: float, tokens: int, project="p1", ts=1000.0):
    rid = s.create_run("lead", "anthropic", "claude-opus-4-5", "t", project_id=project).id
    s.start_turn(rid, ts, 0.0)
    s.add_usage(rid, input_tokens=tokens // 2, output_tokens=tokens - tokens // 2, usd=usd)
    return rid


# ---- S1

def test_request_for_an_existing_study_is_refused(tmp_path):
    reqs = SimRequests(_store(tmp_path))
    with pytest.raises(MisesError, match="already exists"):
        reqs.file(_req(name="drop_study"), run_id=None, project_id="p1", existing=EXISTING)
    assert reqs.list() == []


def test_similar_existing_study_must_be_checked_with_a_reason(tmp_path):
    reqs = SimRequests(_store(tmp_path))
    impact = dict(name="impact_dynamics", computes="peak egg deceleration in a drop onto "
                  "a crumple structure", method="explicit dynamic drop impact, 1-DOF")
    with pytest.raises(MisesError, match="drop"):
        reqs.file(_req(**impact), run_id=None, project_id="p1", existing=EXISTING)
    rec, created = reqs.file(
        _req(**impact, existing_checked={"drop": "quasi-static peak force; no crush history"}),
        run_id=None, project_id="p1", existing=EXISTING)
    assert created and rec["status"] == "pending"


def test_a_second_request_for_an_open_name_returns_the_first(tmp_path):
    reqs = SimRequests(_store(tmp_path))
    a, c1 = reqs.file(_req(), run_id=None, project_id="p1", existing=EXISTING)
    b, c2 = reqs.file(_req(), run_id=None, project_id="p1", existing=EXISTING)
    assert c1 and not c2 and a["id"] == b["id"] and len(reqs.list()) == 1


def test_request_validation_needs_cases_and_snake_case():
    with pytest.raises(ValueError):
        _req(validation_cases=[])
    with pytest.raises(ValueError):
        _req(name="Flapping Wing")


def test_existing_studies_lists_part_and_assembly_kinds(tmp_path):
    ids = {e["id"] for e in sr.existing_studies(_store(tmp_path))}
    assert {"static", "drop"} <= ids and "walk" in ids


# ---- S2

def test_estimate_with_no_history_is_a_stated_prior(tmp_path):
    e = sr.estimate(_store(tmp_path), _req())
    assert e["method"] == "prior" and e["confidence"] == "low"
    assert "not data" in e["basis"]
    per = sr.price("anthropic", sr.PRIOR_MODEL, *sr.PRIOR_TURN_TOKENS)
    lo, mid, hi = sr.ASSUMED_TURNS["small"]
    assert e["usd"] == {"low": round(per * lo, 2), "likely": round(per * mid, 2),
                        "high": round(per * hi, 2)}
    assert e["tokens"]["likely"] == sum(sr.PRIOR_TURN_TOKENS) * mid
    assert e["download_mb"] == 18.0 and e["budget_pause_usd"] == round(per * hi * 1.5, 2)


def test_estimate_scales_recorded_turns_when_no_comparable_builds(tmp_path):
    s = _store(tmp_path)
    for i in range(5):                      # $1..$5 per turn, 1000..5000 tokens
        _turn(s, usd=float(i + 1), tokens=1000 * (i + 1), project=f"p{i}")
    e = sr.estimate(s, _req(complexity="medium"))
    lo, mid, hi = sr.ASSUMED_TURNS["medium"]
    assert e["method"] == "turn_scaled"
    # quartiles of 1..5 are 2, 3, 4
    assert e["usd"] == {"low": 2.0 * lo, "likely": 3.0 * mid, "high": 4.0 * hi}
    assert e["tokens"] == {"low": 2000 * lo, "likely": 3000 * mid, "high": 4000 * hi}
    assert "assumption" in e["basis"] and "5 recorded turns" in e["basis"]


def test_estimate_uses_comparable_builds_of_the_same_complexity(tmp_path):
    s = _store(tmp_path)
    reqs = SimRequests(s)
    for i, usd in enumerate((2.0, 4.0, 6.0)):
        rec, _ = reqs.file(_req(name=f"sim_{i}_aero"), run_id=None, project_id="p",
                           existing=[])
        reqs.decide(rec["id"], "accept", decided_by="user")
        root = _turn(s, usd=usd, tokens=int(usd * 1000))
        reqs.start_build(rec["id"], root)
        r = reqs.get(rec["id"])
        reqs._cas(rec["id"], "building", "built",
                  {**reqs._data(r), "build": {**r["build"], "finished": True}})
    e = sr.estimate(s, _req(name="new_aero"))
    assert e["method"] == "comparable_builds" and "3 recorded simulator builds" in e["basis"]
    assert e["usd"] == {"low": 2.4, "likely": 4.0, "high": 5.6}       # p10 / p50 / p90
    # a different complexity class is not comparable
    assert sr.estimate(s, _req(name="other", complexity="large"))["method"] != "comparable_builds"


def test_weekly_share_only_with_a_baseline(tmp_path):
    s = _store(tmp_path)
    assert sr.estimate(s, _req())["weekly_share_pct"] is None
    e = sr.estimate(s, _req(), weekly_baseline_usd=100.0)
    assert e["weekly_share_pct"]["high"] == round(e["usd"]["high"], 1)


# ---- S3

def test_decision_is_one_time_and_only_the_user_decides(tmp_path):
    reqs = SimRequests(_store(tmp_path))
    rec, _ = reqs.file(_req(), run_id=None, project_id="p1", existing=EXISTING)
    with pytest.raises(MisesError, match="only the user"):
        reqs.decide(rec["id"], "accept", decided_by="agent:lead")
    with pytest.raises(MisesError, match="say why"):
        reqs.decide(rec["id"], "reject", decided_by="user", reason="")
    out = reqs.decide(rec["id"], "reject", decided_by="user", reason="too costly this week")
    assert out["status"] == "rejected"
    with pytest.raises(AlreadyDecided) as e:
        reqs.decide(rec["id"], "accept", decided_by="user")
    assert e.value.decision["status"] == "rejected"
    assert reqs.get(rec["id"])["status"] == "rejected"
    g = sr.agent_guidance(reqs.get(rec["id"]))
    assert "state the gap" in g and "too costly this week" in g


def test_filing_never_auto_approves(tmp_path):
    reqs = SimRequests(_store(tmp_path))
    rec, _ = reqs.file(_req(), run_id=None, project_id="p1", existing=EXISTING)
    assert rec["status"] == "pending" and rec["decision"] is None
    with pytest.raises(MisesError, match="not accepted"):
        reqs.start_build(rec["id"], "root")


# ---- S6

def test_build_pauses_at_one_and_a_half_times_the_high_estimate(tmp_path):
    s = _store(tmp_path)
    reqs = SimRequests(s)
    rec, _ = reqs.file(_req(), run_id=None, project_id="p1", existing=EXISTING)
    reqs.decide(rec["id"], "accept", decided_by="user")
    high = rec["estimate"]["usd"]["high"]
    root = s.create_run("builder", "anthropic", "claude-opus-4-5", "b", project_id="p1").id
    reqs.start_build(rec["id"], root)
    s.add_usage(root, input_tokens=10, output_tokens=10, usd=high * 1.4)
    assert reqs.budget_check(rec["id"])["paused"] is False
    s.add_usage(root, input_tokens=10, output_tokens=10, usd=high * 0.11)
    chk = reqs.budget_check(rec["id"])
    assert chk["paused"] and chk["spend"]["usd"] >= high * 1.5 - 0.01
    assert chk["budget"]["paused_spend"]["usd"] == chk["spend"]["usd"]
    with pytest.raises(MisesError, match="only the user"):
        reqs.budget_decide(rec["id"], "continue", decided_by="agent")
    out = reqs.budget_decide(rec["id"], "continue", decided_by="user")
    assert out["status"] == "building"
    assert out["budget"]["limit_usd"] == pytest.approx(round(high * 1.5, 2) + high, abs=0.02)
    with pytest.raises(AlreadyDecided):
        reqs.budget_decide(rec["id"], "stop", decided_by="user")
    assert reqs.budget_check(rec["id"])["paused"] is False


# ---- tool and routes

def test_tool_files_and_publishes_and_route_decides_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    from mises.runtime import Runtime
    from mises.server.app import create_app
    from mises.tools.registry import ToolContext
    from mises.sim.request_tools import simulator_request
    rt = Runtime(tmp_path / "home")
    assert rt.registry.list(["simulator_request", "simulator_request_status"])
    seen = []
    rt.events.taps.append(lambda ev: seen.append(ev.type))
    ctx = ToolContext(run_id="r0", user="u", store=rt.store, events=rt.events,
                      workdir=tmp_path, policy=None, call_id="c")
    monkeypatch.setattr(sr, "existing_studies", lambda store=None: EXISTING)
    out = asyncio.run(simulator_request.handler(ctx, _req()))
    rid = out.data["id"]
    assert out.data["status"] == "pending" and "sim_request_filed" in seen
    assert "unverified" in out.data["next"]
    with TestClient(create_app(rt, "tok")) as client:
        h = {"Authorization": "Bearer tok"}
        lst = client.get("/sim-requests", headers=h, params={"status": "pending"}).json()
        assert [r["id"] for r in lst["requests"]] == [rid]
        ok = client.post(f"/sim-requests/{rid}/decide", headers=h,
                         json={"action": "changes", "reason": "add a hover validation case"})
        assert ok.status_code == 200 and ok.json()["request"]["status"] == "changes_requested"
        again = client.post(f"/sim-requests/{rid}/decide", headers=h, json={"action": "accept"})
        assert again.status_code == 409 and again.json()["error"] == "already_decided"
        assert again.json()["decision"]["status"] == "changes_requested"
