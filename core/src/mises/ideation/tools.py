"""Agent tools of the ideation stage (Plan 11 F8b/c): ideate_concepts (local_mutate),
concepts_tried and egg_drop_estimate (read). See ideation/__init__.py for the stage."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..concept.tools import concepts as concept_store, run_project, user_block
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, ToolOutput
from . import (FAMILIES, FAMILY_HELP, MIN_CONCEPTS, MIN_FAMILIES, MIN_PRINCIPLES,
               IdeationStore, build_challenge, conventional, norm, score, shortlist, tried_keys)

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Family = Literal[FAMILIES]  # type: ignore[valid-type]


def ideations(ctx: ToolContext) -> IdeationStore:
    c = concept_store(ctx)
    st = getattr(c, "_ideation", None)
    if st is None:
        st = c._ideation = IdeationStore(c)
    return st


# ---- ideate_concepts

class Concept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    principle: Annotated[str, StringConstraints(min_length=3, max_length=100)] = Field(
        description="the physical principle it relies on, e.g. 'autorotation slows the "
                    "descent' or 'progressive plastic crushing of a printed lattice'")
    family: Family = Field(description="; ".join(f"{k}: {v}" for k, v in FAMILY_HELP.items()))
    mechanism: Annotated[str, StringConstraints(min_length=10, max_length=500)] = Field(
        description="how it works and roughly what it looks like (one or two sentences)")
    feasibility: Unit = Field(description="0..1: can it be built and work with the means "
                              "and the rules given")
    novelty: Unit = Field(description="0..1: how far from the obvious answer it is")
    rule_compliance: Unit = Field(description="0..1: how surely it meets the rules / limits")
    conventional: bool = Field(False, description="true for the obvious, standard answer "
                               "(a padded box or cage): listed, but never shortlisted unless "
                               "the user asked for it")
    risks: Annotated[str, StringConstraints(max_length=240)] = ""


class IdeateArgs(BaseModel):
    challenge: Annotated[str, StringConstraints(min_length=3, max_length=300)] = Field(
        description="the problem in one line, e.g. 'protect a raw egg dropped from 10 m'")
    constraints: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list, max_length=12,
        description="the rules and limits that apply (from the user, or the fetched rules)")
    rules_source: Annotated[str, StringConstraints(max_length=300)] = Field(
        "", description="where the rules came from: a URL you fetched, or 'the user's "
        "message'; for a competition fetch the current rules first")
    concepts: list[Concept] = Field(..., min_length=MIN_CONCEPTS, max_length=16)
    include_conventional: bool = Field(
        False, description="only when the user asked for the standard design (e.g. a cage)")

    @model_validator(mode="after")
    def _distinct(self):
        names = [norm(c.name) for c in self.concepts]
        if len(set(names)) != len(names):
            raise ValueError("concept names must differ")
        principles = {norm(c.principle) for c in self.concepts}
        if len(principles) < MIN_PRINCIPLES:
            raise ValueError(
                f"only {len(principles)} distinct physical principles: list at least "
                f"{MIN_PRINCIPLES} genuinely different ones (not variants of one idea)")
        families = {c.family for c in self.concepts}
        if len(families) < MIN_FAMILIES:
            raise ValueError(
                f"the concepts span only {len(families)} principle families "
                f"({', '.join(sorted(families))}): spread them over at least {MIN_FAMILIES}, "
                f"e.g. {', '.join(f for f in FAMILIES if f not in families)[:200]}")
        return self


@tool("ideate_concepts", safety=SafetyClass.LOCAL_MUTATE, timeout_s=30,
      description="Divergent ideation for an open-ended design challenge (an egg drop, a "
                  "competition, 'make it interesting'), BEFORE pitch_options and before any "
                  "part. List at least 8 genuinely different concepts on at least 8 distinct "
                  "physical principles across at least 4 families (crush, elastic "
                  "suspension, aerodynamic drag, autorotation, pneumatic, fluid/granular "
                  "damping, origami/kirigami structures, rolling/spinning, staged/sacrificial, "
                  "shells), each scored 0..1 on feasibility, novelty and rule compliance; mark "
                  "the obvious answer `conventional`. AgentCAD scores them, flags concepts this "
                  "build already tried, and shortlists the 3 most different strong ones (one "
                  "per family): pitch those with pitch_options (set each option's `concept`).")
async def ideate_concepts(ctx: ToolContext, args: IdeateArgs) -> ToolOutput:
    store = ideations(ctx)
    project = run_project(ctx)
    tried = store.tried(project)
    seen = tried_keys(tried)
    concepts = []
    for c in args.concepts:
        d = c.model_dump()
        d["score"] = score(d)
        d["conventional"] = conventional(d)
        d["already_tried"] = bool({norm(c.name), norm(c.principle)} & seen)
        concepts.append(d)
    fresh = [c for c in concepts if not c["already_tried"]]
    if len(fresh) < MIN_CONCEPTS:
        names = ", ".join(c["name"] for c in concepts if c["already_tried"])
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"{len(concepts) - len(fresh)} concept(s) repeat what this build "
                         f"already tried ({names}): list at least {MIN_CONCEPTS} new ones",
                         hint="concepts_tried lists them; keep the new ones different")
    picks = shortlist(concepts, include_conventional=args.include_conventional)
    challenge = build_challenge(ctx.store, project) or {}
    rec = store.add(project, {
        "run_id": ctx.run_id, "challenge": args.challenge, "kind": challenge.get("kind"),
        "constraints": args.constraints, "rules_source": args.rules_source,
        "concepts": concepts, "shortlist": picks,
        "principles": len({norm(c["principle"]) for c in concepts}),
        "families": sorted({c["family"] for c in concepts})})
    table = [{"name": c["name"], "family": c["family"], "score": c["score"],
              "conventional": c["conventional"], "already_tried": c["already_tried"]}
             for c in sorted(concepts, key=lambda c: -c["score"])]
    others = store.others(project, challenge.get("kind"))
    data = {"ideation_id": rec["ideation_id"], "shortlist": picks, "ranked": table,
            "note": "names and mechanisms are your own text, returned as data"}
    if others:
        data["tried_in_other_builds"] = user_block(
            {"concepts": [{"name": o["name"], "family": o["family"], "status": o["status"]}
                          for o in others]})
    short = len(picks) < 3
    return ToolOutput(
        summary=(f"{len(concepts)} concepts on {rec['principles']} principles across "
                 f"{len(rec['families'])} families; shortlist: {', '.join(picks) or 'none'}. "
                 + ("Fewer than 3 strong unconventional concepts: add better ones before "
                    "pitching. " if short else
                    "Next: pitch_options with these 3 (one massing each, `concept` = its "
                    "name), then wait for the user's pick; do not default to a cage. ")),
        data=data)


# ---- concepts_tried

class TriedArgs(BaseModel):
    pass


@tool("concepts_tried", safety=SafetyClass.READ,
      description="The concepts this build already pitched or built (and the user's other "
                  "builds of the same challenge kind), so a new ideation round does not "
                  "repeat them; plus the latest ideation's shortlist.")
async def concepts_tried(ctx: ToolContext, args: TriedArgs) -> ToolOutput:
    store = ideations(ctx)
    project = run_project(ctx)
    tried = store.tried(project)
    latest = store.latest(project)
    challenge = build_challenge(ctx.store, project)
    others = store.others(project, (challenge or {}).get("kind"))
    data = {"challenge": challenge, "untrusted_tried": user_block(
        {"this_build": tried, "other_builds": [{k: o.get(k) for k in
                                                ("name", "family", "status", "build_id")}
                                               for o in others]}),
            "latest_ideation": None if latest is None else {
                "ideation_id": latest["ideation_id"], "shortlist": latest["shortlist"]}}
    return ToolOutput(summary=f"{len(tried)} concept(s) tried in this build, {len(others)} in "
                              "other builds of this kind", data=data)


# ---- egg_drop_estimate (F8c)

G = 9.81
RHO_AIR = 1.225                 # kg/m3, sea level, 15 C
EGG_MASS_KG = 0.06              # a large chicken egg
# the egg-break reference: quasi-static shell failure load of a chicken egg. Reported shell
# breaking strengths cluster around 30-40 N (range ~25-45 N) for an egg loaded between flat
# plates; a conforming cradle spreads the load and an egg survives more, a point contact less.
EGG_BREAK_N = 35.0
EGG_BREAK_RANGE_N = (25.0, 45.0)
EGG_BREAK_SOURCE = ("chicken-egg shell quasi-static compression failure load, ~25-45 N "
                    "(nominal 35 N), as reported in eggshell breaking-strength studies "
                    "(citation not re-verified here); loaded between flat plates, "
                    "so a conforming padded cradle tolerates more and a point contact less")


class EggDropArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drop_height_m: float = Field(gt=0, le=1000)
    total_mass_kg: float = Field(gt=0, le=50, description="the whole device with the egg")
    egg_mass_kg: float = Field(EGG_MASS_KG, gt=0, le=0.2)
    drag_area_m2: float = Field(0.0, ge=0, le=50, description="Cd x frontal area of the "
                                "device without a parachute (e.g. 0.5 x 0.01 m2)")
    parachute_diameter_m: float = Field(0.0, ge=0, le=10)
    parachute_cd: float = Field(1.5, gt=0, le=3)
    rotor_cd_area_m2: float = Field(0.0, ge=0, le=50, description="an autorotating rotor or "
                                    "samara wing: its effective Cd x disc area")
    stroke_mm: float = Field(gt=0, le=2000, description="the travel the egg has to stop in "
                             "(crush depth + suspension travel), from the drawn geometry")
    stroke_efficiency: float = Field(
        0.5, gt=0.05, le=1.0, description="mean / peak decelerating force over the stroke: "
        "~0.5 for a linear spring or band suspension, 0.7-0.9 for a crushable foam / "
        "honeycomb / lattice with a flat plateau, 0.3 for a stiffening (bottoming) cushion")


def egg_drop(args: EggDropArgs) -> dict:
    cda = args.drag_area_m2 + args.parachute_cd * math.pi * (args.parachute_diameter_m / 2) ** 2 \
        + args.rotor_cd_area_m2
    h, m = args.drop_height_m, args.total_mass_kg
    if cda > 0:
        vt = math.sqrt(2 * m * G / (RHO_AIR * cda))
        v = vt * math.sqrt(1 - math.exp(-2 * G * h / vt ** 2))
    else:
        vt, v = None, math.sqrt(2 * G * h)
    s = args.stroke_mm / 1000
    a_mean = v ** 2 / (2 * s)
    a_peak = a_mean / args.stroke_efficiency
    force = args.egg_mass_kg * (a_peak + G)
    limit_g = EGG_BREAK_N / (args.egg_mass_kg * G) - 1
    margin = EGG_BREAK_N / force
    low, high = EGG_BREAK_RANGE_N
    # the warning starts at the range's LOWER bound: a weak egg breaks at ~25 N
    verdict = ("likely survives" if force <= low else "marginal" if force <= high
               else "likely breaks")
    return {
        "impact_velocity_m_s": round(v, 3),
        "terminal_velocity_m_s": None if vt is None else round(vt, 3),
        "free_fall_velocity_m_s": round(math.sqrt(2 * G * h), 3),
        "drag_cd_area_m2": round(cda, 5),
        "mean_deceleration_g": round(a_mean / G, 1),
        "peak_deceleration_g": round(a_peak / G, 1),
        "peak_egg_force_n": round(force, 2),
        "egg_break_threshold": {"force_n": EGG_BREAK_N, "range_n": list(EGG_BREAK_RANGE_N),
                                "deceleration_g": round(limit_g, 1),
                                "source": EGG_BREAK_SOURCE},
        "margin": round(margin, 2), "margin_vs_lower_bound": round(low / force, 2),
        "verdict": verdict,
        "estimate": True,
        "assumptions": [
            "quadratic drag at sea-level air density (1.225 kg/m3), no wind",
            "the egg stops over the given stroke; peak = mean / stroke_efficiency",
            "the device lands on the stroke side; a tumble onto a hard face bypasses it",
            "rigid egg in its cradle: a cradle that conforms spreads the load (more margin)",
            "the threshold is a quasi-static load; a short impact pulse is not the same load "
            "case, so read the margin as indicative"],
    }


@tool("egg_drop_estimate", safety=SafetyClass.READ,
      description="Predict an egg-drop device's landing: impact speed with drag (parachute, "
                  "drag plate or autorotating rotor), mean and peak deceleration over the "
                  "stopping stroke, and the peak force on the egg against the egg-break "
                  "threshold (~35 N quasi-static shell failure, source returned). An "
                  "ESTIMATE: state it as one, with the margin and the source, and check the "
                  "structure with the drop study / dynamics where it matters.")
async def egg_drop_estimate(ctx: ToolContext, args: EggDropArgs) -> ToolOutput:
    r = egg_drop(args)
    return ToolOutput(
        summary=(f"impact {r['impact_velocity_m_s']} m/s, peak {r['peak_deceleration_g']} g "
                 f"on the egg ({r['peak_egg_force_n']} N vs ~{EGG_BREAK_N:g} N shell failure, "
                 f"warning from 25 N): {r['verdict']} (estimate)"),
        data=r)


def register_ideation_tools(registry: ToolRegistry) -> None:
    for t in (ideate_concepts, concepts_tried, egg_drop_estimate):
        registry.register(t)
