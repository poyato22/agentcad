"""Plan 11 F8b/c: challenge detection, the ideation stage, its gate, the tried-concepts memory
and the egg-drop estimate."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises import ideation
from mises.ideation import gate as igate
from mises.ideation.tools import EggDropArgs, IdeateArgs, egg_drop
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C, StepKind

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# eight concepts on eight principles in seven families, plus the cage everyone builds
EGG_CONCEPTS = [
    dict(name="Samara pod", family="aero_rotation",
         principle="autorotation of a single maple-seed wing slows the descent",
         mechanism="one printed wing spins the pod down at a few m/s", feasibility=0.7,
         novelty=0.9, rule_compliance=0.9),
    dict(name="Gyroid crush cone", family="crush",
         principle="progressive plastic crushing of a printed gyroid lattice",
         mechanism="a 60 mm gyroid cone under the egg crushes layer by layer",
         feasibility=0.85, novelty=0.6, rule_compliance=1.0),
    dict(name="Tensegrity cradle", family="elastic_suspension",
         principle="tensioned flexures hold the egg free of every strut",
         mechanism="printed TPU flexures suspend the egg in a tensegrity frame",
         feasibility=0.6, novelty=0.85, rule_compliance=0.9),
    dict(name="Kirigami bellows", family="geometric_structure",
         principle="buckling of a kirigami cylinder absorbs energy",
         mechanism="a cut-pattern cylinder folds flat at a near-constant force",
         feasibility=0.65, novelty=0.8, rule_compliance=0.9),
    dict(name="Drag petals", family="aero_drag",
         principle="aerodynamic drag of deployed petals lowers terminal velocity",
         mechanism="hinged petals open on release into a drag flower",
         feasibility=0.6, novelty=0.7, rule_compliance=0.8),
    dict(name="Bead damper", family="fluid_granular",
         principle="granular friction of printed beads dissipates the impact",
         mechanism="a chamber of loose printed beads around the egg", feasibility=0.7,
         novelty=0.6, rule_compliance=0.9),
    dict(name="Rolling wheel", family="rotational_rolling",
         principle="converting the vertical hit into rolling sheds energy sideways",
         mechanism="a wheel with spiral spokes rolls away on landing", feasibility=0.5,
         novelty=0.75, rule_compliance=0.9),
    dict(name="Snap legs", family="staged_sacrificial",
         principle="sacrificial legs fracture in sequence at set loads",
         mechanism="four printed legs with notches break one after another",
         feasibility=0.75, novelty=0.5, rule_compliance=1.0),
    dict(name="Padded cage", family="containment_shell",
         principle="a stiff cage spreads the impact through padding",
         mechanism="a spherical cage with foam around the egg", feasibility=0.95,
         novelty=0.1, rule_compliance=1.0, conventional=True),
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return tmp_path / "home"


class Capture:
    name = "fake"

    def __init__(self):
        self.reqs = []

    async def stream(self, req):
        self.reqs.append(req)
        async for ev in FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]]
                                     ).stream(req):
            yield ev


def _rt(home, task="Design a egg drop build no limits"):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    project = rt.store.ensure_default_project().id
    run = rt.store.create_run("engineer", "fake", "m", task)
    rt.store.update_run(run.id, project_id=project, role="lead")
    rt.store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
    rt.store.append_step(run.id, StepKind.MESSAGE, {"role": "user", "text": task})
    return rt, project, run


def _ctx(rt, run):
    return SimpleNamespace(run_id=run.id, store=rt.store, services=rt.gate.services,
                           workdir=rt.home)


# ---- detection

@pytest.mark.parametrize("text,kind,creative", [
    ("Make a design for the egg drop challenge. the only constrain is ht must be less than 3 "
     "hours of priniting", "egg_drop", False),
    ("Design a egg drop build no limits think of the best possible design", "egg_drop", True),
    ("for the egg drop challenge it keeps making the same boring cage but I want something "
     "really interesting", "egg_drop", True),
    ("design our entry for the Team America Rocketry Challenge", "competition", False),
    ("make me a desk lamp, something really creative", "creative", True),
])
def test_challenge_signal(text, kind, creative):
    sig = ideation.challenge_signal(text)
    assert sig["kind"] == kind and sig["creative"] is creative


@pytest.mark.parametrize("text", ["make the wall 2 mm thicker", "a bracket for a NEMA 17",
                                  "what is the mass of the lid?"])
def test_ordinary_requests_are_not_challenges(text):
    assert ideation.challenge_signal(text) is None


def test_the_flag_rises_and_turns_off(home):
    rt, project, _ = _rt(home)
    assert ideation.note_challenge(rt.store, project, "Design an egg drop build")["kind"] \
        == "egg_drop"
    assert ideation.note_challenge(rt.store, project, "make the lid thicker")["kind"] \
        == "egg_drop"                                      # a plain follow-up keeps it
    rec = ideation.note_challenge(rt.store, project, "something really interesting please")
    assert rec["kind"] == "egg_drop" and rec["creative"]   # an egg drop stays an egg drop
    assert ideation.note_challenge(rt.store, project, "just build it") is None
    assert ideation.build_challenge(rt.store, project) is None


# ---- the ideation tool

def test_ideation_needs_eight_distinct_principles_across_families():
    with pytest.raises(ValueError, match="at least 8"):
        IdeateArgs(challenge="protect an egg", concepts=EGG_CONCEPTS[:7])
    same = [{**c, "principle": "crushing foam absorbs the impact"} for c in EGG_CONCEPTS[:9]]
    with pytest.raises(ValueError, match="distinct physical principles"):
        IdeateArgs(challenge="protect an egg", concepts=same)
    narrow = [{**c, "family": "crush" if i % 2 else "aero_drag"}
              for i, c in enumerate(EGG_CONCEPTS)]
    with pytest.raises(ValueError, match="principle families"):
        IdeateArgs(challenge="protect an egg", concepts=narrow)
    IdeateArgs(challenge="protect an egg", concepts=EGG_CONCEPTS)


def test_shortlist_is_three_different_families_never_the_cage():
    cs = [{**c, "score": ideation.score(c), "conventional": ideation.conventional(c)}
          for c in EGG_CONCEPTS]
    picks = ideation.shortlist(cs)
    fam = {c["name"]: c["family"] for c in cs}
    assert len(picks) == 3 and len({fam[p] for p in picks}) == 3
    assert "Padded cage" not in picks
    assert "Padded cage" not in ideation.shortlist(cs, n=9)
    assert "Padded cage" in ideation.shortlist(cs, include_conventional=True, n=9)
    # a cage the model forgot to mark conventional is still recognised
    assert ideation.conventional({"name": "Egg cage", "principle": "x"})


async def _ideate(rt, run, concepts=EGG_CONCEPTS, call_id="i1"):
    return await rt.gate.call(run.id, "ideate_concepts", {
        "challenge": "protect a raw egg dropped from a height", "constraints": ["fully printed"],
        "rules_source": "the user's message", "concepts": concepts}, call_id=call_id,
        workdir=rt.home)


async def test_ideate_scores_shortlists_and_stores(home):
    rt, project, run = _rt(home)
    ideation.note_challenge(rt.store, project, "egg drop")
    out = await _ideate(rt, run)
    assert out.ok and len(out.data["shortlist"]) == 3
    assert "Padded cage" not in out.data["shortlist"]
    assert "pitch_options" in out.summary and "do not default to a cage" in out.summary
    rec = rt.concepts._ideation.latest(project)
    assert rec["principles"] == 9 and rec["kind"] == "egg_drop"
    assert {c["name"] for c in rec["concepts"]} >= {"Samara pod", "Padded cage"}


async def test_tried_concepts_are_remembered_and_not_repeated(home):
    rt, project, run = _rt(home)
    ideation.note_challenge(rt.store, project, "egg drop")
    out = await _ideate(rt, run)
    picks = out.data["shortlist"]
    store = rt.concepts._ideation
    store.mark_tried(project, picks[:2], "pitched")
    store.mark_tried(project, picks[:1], "chosen")
    tried = {t["name"]: t["status"] for t in store.tried(project)}
    assert tried == {picks[0]: "chosen", picks[1]: "pitched"}
    # the same list again: two repeats leave 7 new concepts, under the minimum
    with pytest.raises(MisesError, match="already tried"):
        await _ideate(rt, run, call_id="i2")
    extra = [dict(name="Air bladder", family="pneumatic",
                  principle="trapped air in a sealed printed bladder compresses",
                  mechanism="a sealed thin-wall bellows vents through a small orifice",
                  feasibility=0.5, novelty=0.7, rule_compliance=0.8),
             dict(name="Spinning top", family="rotational_rolling",
                  principle="gyroscopic spin keeps the pod upright onto its crush nose",
                  mechanism="fins spin the pod so it lands nose first",
                  feasibility=0.5, novelty=0.8, rule_compliance=0.9)]
    out2 = await _ideate(rt, run, EGG_CONCEPTS + extra, call_id="i3")
    ranked = {r["name"]: r for r in out2.data["ranked"]}
    assert ranked[picks[0]]["already_tried"] and not set(picks[:2]) & set(out2.data["shortlist"])
    status = await rt.gate.call(run.id, "concepts_tried", {}, call_id="t1", workdir=rt.home)
    assert picks[0] in status.data["untrusted_tried"]


async def test_other_builds_of_the_same_challenge_show_their_tried_concepts(home):
    rt, project, run = _rt(home)
    other = rt.store.create_project("Egg Drop2").id
    ideation.note_challenge(rt.store, other, "egg drop challenge")
    ideation.note_challenge(rt.store, project, "egg drop challenge")
    from mises.ideation import IdeationStore
    IdeationStore(rt.concepts).mark_tried(other, ["Spherical cage"], "chosen")
    out = await _ideate(rt, run)
    assert "Spherical cage" in out.data["tried_in_other_builds"]


def test_pitch_and_choose_mark_the_tried_list(home):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead = client.post("/runs", headers=AUTH, json={"prompt": "an egg drop"}).json()["run_id"]
        for _ in range(200):
            if rt.store.get_run(lead).status in ("done", "failed"):
                break
            time.sleep(0.02)
        project = rt.store.get_run(lead).project_id
        p = rt.concepts.add_pitch(project, {"pitch_id": "ckp_" + "c" * 12, "options": [
            {"name": "A", "concept": "Samara pod"}, {"name": "B", "baseline": True}],
            "chosen": None})
        r = client.post(f"/concept/pitches/{p['pitch_id']}/choose", headers=AUTH,
                        json={"option": "A"})
        assert r.status_code == 200, r.text
    tried = rt.concepts.get(project)["tried"]
    assert tried[0]["name"] == "Samara pod" and tried[0]["status"] == "chosen"


# ---- the gate

async def test_gate_holds_pitch_and_new_parts_until_ideation_and_the_users_pick(home):
    rt, project, run = _rt(home)
    ctx = _ctx(rt, run)
    new_part = SimpleNamespace(part_id="egg_pod")
    # not a challenge build: nothing is held
    igate.check_call(ctx, "pitch_options", None)
    igate.check_call(ctx, "mirror_part_create", new_part)
    ideation.note_challenge(rt.store, project, "Design a egg drop build no limits")
    for name, args in (("pitch_options", None), ("mirror_part_create", new_part)):
        with pytest.raises(MisesError) as e:
            igate.check_call(ctx, name, args)
        assert e.value.code == ErrorCode.NEEDS_APPROVAL and "ideate_concepts" in e.value.hint
    rt.store.register_part("old_part", project)                  # a rebuild always passes
    igate.check_call(ctx, "mirror_part_create", SimpleNamespace(part_id="old_part"))
    await _ideate(rt, run)
    igate.check_call(ctx, "pitch_options", None)                 # ideation done: may pitch
    with pytest.raises(MisesError, match="wait for the pitch"):
        igate.check_call(ctx, "mirror_part_create", new_part)
    rt.concepts.add_pitch(project, {"pitch_id": "ckp_" + "d" * 12, "options": [],
                                    "chosen": None})
    with pytest.raises(MisesError, match="user's pick"):
        igate.check_call(ctx, "mirror_part_create", new_part)
    time.sleep(0.01)
    rt.store.append_step(run.id, StepKind.MESSAGE, {"role": "user", "text": "the samara!"})
    igate.check_call(ctx, "mirror_part_create", new_part)        # the user answered


async def test_gate_runs_in_the_pipeline_and_skip_opens_it(home):
    rt, project, run = _rt(home)
    ideation.note_challenge(rt.store, project, "egg drop challenge")
    with pytest.raises(MisesError) as e:
        await rt.gate.call(run.id, "mirror_part_create",
                           {"part_id": "egg_pod", "script": "VARS={}\ndef build(v): pass"},
                           call_id="c1", workdir=rt.home)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL
    rt.concepts.update(project, status="skipped")
    igate.check_call(_ctx(rt, run), "mirror_part_create", SimpleNamespace(part_id="egg_pod"))


# ---- the prompt and the lead's tools

def test_lead_gets_the_ideation_rules_and_tools():
    from mises.agent.definitions import load_definitions
    from mises.orchestrator.lead import LEAD_TOOLS, lead_definition
    for t in ("ideate_concepts", "concepts_tried", "egg_drop_estimate"):
        assert t in LEAD_TOOLS
    body = lead_definition(load_definitions()["engineer"]).body
    assert "ideate_concepts" in body and "at least 8 genuinely different concepts" in body
    assert "Never default to a cage" in body and "samara" in body
    assert "fetch its current rules" in body and "egg_drop_estimate" in body


def test_the_lead_run_marks_a_challenge_build(home):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        p = rt.store.ensure_default_project().id
        r = client.post(f"/projects/{p}/chat", headers=AUTH, json={
            "text": "for the egg drop challenge it keeps making the same boring cage but I "
                    "want something really interesting"})
        assert r.status_code == 200, r.text
    assert ideation.build_challenge(rt.store, p)["kind"] == "egg_drop"


# ---- the egg-drop estimate (F8c)

def test_egg_drop_free_fall_and_parachute():
    free = egg_drop(EggDropArgs(drop_height_m=10, total_mass_kg=0.2, stroke_mm=50))
    assert free["impact_velocity_m_s"] == pytest.approx((2 * 9.81 * 10) ** 0.5, rel=1e-3)
    # v^2/(2s) = 196.2/0.1 = 1962 m/s2 = 200 g mean, 400 g peak at efficiency 0.5
    assert free["mean_deceleration_g"] == pytest.approx(200, rel=0.01)
    assert free["peak_deceleration_g"] == pytest.approx(400, rel=0.01)
    assert free["verdict"] == "likely breaks"
    th = free["egg_break_threshold"]
    assert th["force_n"] == 35 and "Voisey" in th["source"]
    chute = egg_drop(EggDropArgs(drop_height_m=10, total_mass_kg=0.2, stroke_mm=50,
                                 parachute_diameter_m=0.6, stroke_efficiency=0.8))
    vt = (2 * 0.2 * 9.81 / (1.225 * 1.5 * 3.14159 * 0.09)) ** 0.5
    assert chute["terminal_velocity_m_s"] == pytest.approx(vt, rel=1e-3)
    assert chute["impact_velocity_m_s"] < vt
    assert chute["peak_egg_force_n"] < 35 and chute["verdict"] == "likely survives"


async def test_egg_drop_estimate_tool(home):
    rt, project, run = _rt(home)
    out = await rt.gate.call(run.id, "egg_drop_estimate", {
        "drop_height_m": 8, "total_mass_kg": 0.15, "rotor_cd_area_m2": 0.05,
        "stroke_mm": 40, "stroke_efficiency": 0.8}, call_id="e1", workdir=rt.home)
    assert out.ok and "shell failure" in out.summary and "(estimate)" in out.summary
    assert out.data["estimate"] is True and out.data["egg_break_threshold"]["source"]
