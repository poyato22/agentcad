"""Fix round 1: face validation, estimate/guide flags, size caps, stale sims, interview_plan,
project scoping, batched ui_ask_user, objectives and evaluation errors."""
import asyncio
import hashlib
import json
import time

import pytest
from pydantic import ValidationError

from mises.engineering.spec import LoadCase, Requirement, Spec, evaluate_spec
from mises.engineering.study import (Budget, Objective, Variable, Weight, run_study,
                                     weights_from_priorities)
from mises.engineering.tools import (InterviewArgs, ProjectArg, SpecCheckArgs, SpecSetArgs,
                                     interview_plan, spec_check, spec_get, spec_set)
from mises.types import ErrorCode, MisesError
from mises.ui_tools import AskArgs, ui_ask_user

from .fakes import make_ctx, make_part
from .test_spec import drone_spec
from .test_tools import DRONE


# ---- spec

def test_load_case_faces_must_be_presets_or_ids():
    with pytest.raises(ValidationError):
        LoadCase(id="hover_thrust", name="hover", fix="botom", load="top", newtons=10,
                 direction="-z")
    LoadCase(id="hover_thrust", name="hover", fix="f3", load="top", newtons=10, direction="-z")


def test_impact_case_must_be_marked_estimate():
    with pytest.raises(ValidationError):
        LoadCase(id="crash_drop", name="crash drop", fix="bottom", load="top", newtons=100,
                 direction="-z")


def test_rows_carry_guide_and_estimate_flags():
    rep = evaluate_spec(drone_spec(), {"mass_g": 100.0, "bbox": {"size": [1, 1, 1]}},
                        {"crash_drop": {"safety_factor": 3.0}})
    by = {r["id"]: r for r in rep["requirements"]}
    assert by["arm_safety"]["guide"] and by["arm_safety"]["estimate"]
    assert not by["mass_budget"]["guide"] and not by["mass_budget"]["estimate"]


def _name(i: int) -> str:
    return "req_" + "".join(chr(97 + int(d)) for d in str(i))  # never ends in a digit


def test_size_caps():
    with pytest.raises(ValidationError):
        Spec(requirements=[Requirement(id=_name(i), kind="max_mass_g", target=10)
                           for i in range(65)])
    with pytest.raises(ValidationError):
        Spec(answers={"use": "x" * 5000})
    with pytest.raises(ValidationError):
        Spec(answers={_name(i): "a" for i in range(40)})


# ---- spec_check and stale sims

def _sim_record(part_dir, sim_id, geometry_hash, sf):
    d = part_dir / "sims" / sim_id
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({
        "sim_id": sim_id, "part_id": part_dir.name, "status": "done", "created_at": time.time(),
        "geometry_hash": geometry_hash,
        "inputs": {"fix": "bottom", "load": {"face": "top", "newtons": 120, "direction": "-z"}},
        "results": {"safety_factor": sf, "max_displacement_mm": 0.2}}))


async def test_spec_check_ignores_stale_sims(tmp_path):
    ctx = make_ctx(tmp_path)
    await spec_set.handler(ctx, SpecSetArgs(**DRONE))
    part = make_part(tmp_path, "frame_x")
    _sim_record(part, "sim_" + "b" * 12, "0" * 64, 3.0)  # the geometry changed since: stale
    out = await spec_check.handler(ctx, SpecCheckArgs(part_id="frame_x"))
    row = {r["id"]: r for r in out.data["requirements"]}["arm_safety"]
    assert row["status"] == "not_checked" and "stale" in row["detail"]

    fresh = hashlib.sha256((part / "model.step").read_bytes()).hexdigest()
    _sim_record(part, "sim_" + "c" * 12, fresh, 3.0)
    out = await spec_check.handler(ctx, SpecCheckArgs(part_id="frame_x"))
    row = {r["id"]: r for r in out.data["requirements"]}["arm_safety"]
    assert row["status"] == "pass" and row["estimate"] and row["guide"]


# ---- interview_plan and project scope

async def test_interview_plan_tool(tmp_path):
    out = await interview_plan.handler(make_ctx(tmp_path),
                                       InterviewArgs(goal="build me a 5 inch drone"))
    batches = out.data["batches"]
    assert sum(len(b) for b in batches) <= 6 and all(1 <= len(b) <= 3 for b in batches)
    assert {"id", "prompt", "options", "multi", "rank", "allow_text"} <= set(batches[0][0])
    assert any(q["rank"] for b in batches for q in b)
    AskArgs(question="About the drone", questions=batches[0])  # a batch is passed to ui_ask_user as is


async def test_explicit_project_must_be_the_runs(tmp_path, monkeypatch):
    import mises.engineering.tools as eng
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(eng, "_run_project", lambda _ctx: "proj_mine")
    with pytest.raises(MisesError) as e:
        await spec_get.handler(ctx, ProjectArg(project_id="proj_other"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE
    assert (await spec_get.handler(ctx, ProjectArg())).data["project_id"] == "proj_mine"


# ---- batched ui_ask_user

class FakeRT:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def ask(self, run_id, question, options, *, questions=None):
        self.calls.append({"question": question, "options": options, "questions": questions})
        return self.reply


BATCH = [{"id": "use", "prompt": "What will it do?", "options": ["racing", "freestyle"]},
         {"id": "priorities", "prompt": "Rank these", "options": ["weight", "stiffness"],
          "rank": True}]


async def test_batched_question_answers_by_id(tmp_path):
    rt = FakeRT(json.dumps({"use": "racing", "priorities": ["weight", "stiffness"],
                            "stray": "dropped"}))
    out = await ui_ask_user.handler(make_ctx(tmp_path, services={"runtime": rt}),
                                    AskArgs(question="About the drone", questions=BATCH))
    assert out.data["answers"] == {"use": "racing", "priorities": ["weight", "stiffness"]}
    sent = rt.calls[0]
    assert len(sent["questions"]) == 2
    assert sent["question"].startswith("About the drone") and "What will it do?" in sent["question"]


async def test_batched_question_plain_text_fallback(tmp_path):
    rt = FakeRT("a 5 inch racer")
    ctx = make_ctx(tmp_path, services={"runtime": rt})
    one = await ui_ask_user.handler(ctx, AskArgs(question="About the drone", questions=BATCH[:1]))
    assert one.data["answers"] == {"use": "a 5 inch racer"}
    two = await ui_ask_user.handler(ctx, AskArgs(question="About the drone", questions=BATCH))
    assert two.data["answers"] == {"text": "a 5 inch racer"}


async def test_single_question_shape_unchanged(tmp_path):
    rt = FakeRT("PETG")
    out = await ui_ask_user.handler(make_ctx(tmp_path, services={"runtime": rt}),
                                    AskArgs(question="Material?", options=["PLA", "PETG"]))
    assert out.data == {"question": "Material?", "answer": "PETG"}
    assert rt.calls[0]["questions"] is None


def test_batch_limits():
    with pytest.raises(ValidationError):
        AskArgs(question="q", questions=BATCH * 2)  # 4 > 3
    with pytest.raises(ValidationError):
        AskArgs(questions=BATCH)  # the schema still requires a `question` (the intro)
    with pytest.raises(ValidationError):
        AskArgs(question="q", questions=[BATCH[0], BATCH[0]])  # duplicate ids


# ---- objectives and evaluation errors

async def _ev(v):
    x = v["wall"]
    return {"mass_g": 10 * x, "sf_by_case": {"c": x}, "max_disp_mm": 1 / x}


async def test_strength_and_stiffness_first_objectives():
    vs = [Variable("wall", 1.0, 9.0)]
    for objective in ("max:min_sf", "min:max_disp_mm"):
        res = await run_study(vs, _ev, objective=objective, budget=Budget(evals=12),
                              max_parallel=1)
        best = next(it for it in res["iterations"] if it["n"] == res["best_id"])
        assert best["vars"]["wall"] == max(it["vars"]["wall"] for it in res["iterations"])


def test_weights_from_priorities():
    ws = weights_from_priorities(["impact strength", "weight", "unknown"])
    assert Weight("min_sf", "max", 2.0) in ws and Weight("mass_g", "min", 1.0) in ws
    with pytest.raises(ValueError):
        Objective("weighted")


async def test_weighted_objective_trades_off():
    vs = [Variable("wall", 1.0, 9.0)]
    heavy = Objective("weighted", (Weight("mass_g", "min", 10.0), Weight("min_sf", "max", 1.0)))
    strong = Objective("weighted", (Weight("mass_g", "min", 1.0), Weight("min_sf", "max", 10.0)))
    a = await run_study(vs, _ev, objective=heavy, budget=Budget(evals=12), max_parallel=1)
    b = await run_study(vs, _ev, objective=strong, budget=Budget(evals=12), max_parallel=1)
    wall = lambda r: next(it for it in r["iterations"] if it["n"] == r["best_id"])["vars"]["wall"]
    assert wall(a) < wall(b)


async def test_evaluation_errors_are_infeasible():
    async def ev(v):
        if v["wall"] < 5:
            raise RuntimeError("boom")
        return {"mass_g": v["wall"], "sf_by_case": {"c": 3.0}}

    res = await run_study([Variable("wall", 1.0, 9.0)], ev, budget=Budget(evals=10),
                          max_parallel=1)
    for it in res["iterations"]:
        if it["vars"]["wall"] < 5:
            assert not it["feasible"] and "boom" in it["metrics"]["eval_error"]
    best = next(it for it in res["iterations"] if it["n"] == res["best_id"])
    assert best["vars"]["wall"] >= 5


async def test_slow_evaluation_is_cut_at_the_budget():
    async def ev(v):
        await asyncio.sleep(5)
        return {"mass_g": 1.0}

    t0 = time.monotonic()
    res = await run_study([Variable("wall", 1.0, 9.0)], ev,
                          budget=Budget(evals=4, minutes=0.005), max_parallel=4)
    assert time.monotonic() - t0 < 2
    assert res["status"] == "budget_exhausted"
    assert all(not it["feasible"] for it in res["iterations"])
