"""The router: picks the agent for a prompt, so the user never has to choose one (Plan 7 R8).

A cheap classifier: keyword rules first, and only when they are unsure (confidence below
ROUTER_ASK_BELOW) an optional small-model call that may override them. Defaults:
- an attached drawing (pdf, png/jpg, dxf)        -> drawing-import
- a system build (drone, robot arm, mechanism)   -> engineer
- a single part (bracket, plate with holes)      -> designer
- "test / analyze" (stress, modal, wind, drop)   -> sim
- "check / review / verify"                      -> verifier
- nothing recognisable                           -> the profile's default agent, low confidence

The prompt is the user's own text; it is only ever matched against word lists here, never run.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

DRAWING_AGENT = "drawing-import"
# drawing files the router recognises by extension or media type
DRAWING_EXTS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".dxf"})
DRAWING_MIMES = frozenset({"application/pdf", "image/png", "image/jpeg", "image/vnd.dxf",
                           "application/dxf", "image/x-dxf"})
# below this the rules are unsure and an optional classifier is asked
ROUTER_ASK_BELOW = 0.5


class Routed(BaseModel):
    """What the router chose: the agent, a short human reason, and how sure it is (0..1)."""
    agent: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class Attachment(BaseModel):
    """A file sent with the prompt: its name and, when known, its media type."""
    name: str = ""
    mime: str | None = None


# ---- word lists (lowercase stems; matched on word starts, so "drones" hits "drone")
BUILD_VERBS = ("make", "build", "design", "create", "model", "generate", "draw", "add",
               "need", "want", "give me", "produce", "fabricate", "print", "redesign")
SYSTEM_NOUNS = ("drone", "quadcopter", "multirotor", "robot", "robotic", "arm", "gripper",
                "mechanism", "assembly", "gearbox", "transmission", "linkage", "rover",
                "vehicle", "chassis", "gimbal", "machine", "system", "exoskeleton", "crane",
                "turret", "walker", "hexapod", "conveyor", "printer", "cnc", "frame",
                "suspension", "landing gear", "actuator", "multi-part", "several parts")
PART_NOUNS = ("bracket", "plate", "part", "hole", "mount", "spacer", "washer", "knob", "gear",
              "box", "cube", "cylinder", "flange", "clip", "shaft", "bushing", "standoff",
              "enclosure", "case", "lid", "cover", "hook", "handle", "block", "tube", "ring",
              "pulley", "bearing holder", "adapter", "coupler", "nut", "bolt", "hinge", "panel",
              "beam", "rod", "tab", "slot", "fillet", "chamfer", "boss", "rib", "pocket")
# analysis nouns: strong evidence for the sim agent even next to "check"
SIM_NOUNS = ("stress", "fea", "fem", "finite element", "deflection", "displacement", "modal",
             "natural frequenc", "resonan", "vibration", "buckl", "thermal", "temperature",
             "heat", "drop test", "impact", "wind", "drag", "lift", "aerodynamic", "cfd",
             "safety factor", "von mises", "strain", "fatigue", "load case", "simulat")
SIM_VERBS = ("test", "analy", "simulate", "stress-test", "how strong", "will it break",
             "can it hold", "can it take", "how much load", "how stiff", "survive")
CHECK_VERBS = ("check", "review", "verify", "validate", "audit", "inspect", "confirm",
               "double-check", "proofread", "sanity", "does it meet", "is it correct",
               "is this right", "meets the spec", "against the spec", "requirements met")
LOAD_WORDS = ("load", "newton", "safety factor", "lightweight", "as light as", "strong enough",
              "stiff", "weight target", "under 50 g", "payload")
DIMENSION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m|in|inch|inches|°|deg)\b|\b\d+\s*[x×]\s*\d+",
                          re.IGNORECASE)
_WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _pat(stem: str) -> re.Pattern[str]:
    p = _WORD_RE_CACHE.get(stem)
    if p is None:
        p = _WORD_RE_CACHE[stem] = re.compile(r"(?<![a-z0-9])" + re.escape(stem), re.IGNORECASE)
    return p


def _hits(text: str, words: Iterable[str]) -> list[str]:
    return [w for w in words if _pat(w).search(text)]


def is_drawing(att: Attachment) -> bool:
    ext = PurePosixPath(att.name.lower()).suffix
    return ext in DRAWING_EXTS or (att.mime or "").lower() in DRAWING_MIMES


def _known(agent: str, known: set[str] | None, fallback: str) -> str:
    return agent if known is None or agent in known else fallback


def route_rules(prompt: str, *, attachments: Iterable[Attachment | dict] = (),
                known: set[str] | None = None, default: str = "engineer") -> Routed:
    """The keyword rules alone (no model call). `known` is the set of defined agents: a choice
    that is not defined falls back (drawing-import -> designer; anything else -> `default`)."""
    atts = [a if isinstance(a, Attachment) else Attachment.model_validate(a)
            for a in attachments]
    default = _known(default, known, "engineer")
    if any(is_drawing(a) for a in atts):
        agent = _known(DRAWING_AGENT, known, _known("designer", known, default))
        why = ("technical drawing attached" if agent == DRAWING_AGENT
               else "technical drawing attached (no drawing-import agent; the designer builds it)")
        return Routed(agent=agent, reason=why, confidence=0.95)

    text = " ".join((prompt or "").split())
    build = _hits(text, BUILD_VERBS)
    system = _hits(text, SYSTEM_NOUNS)
    part = _hits(text, PART_NOUNS)
    sim_n = _hits(text, SIM_NOUNS)
    sim_v = _hits(text, SIM_VERBS)
    check = _hits(text, CHECK_VERBS)
    dims = bool(DIMENSION_RE.search(text))

    def pick(agent: str, reason: str, conf: float) -> Routed:
        return Routed(agent=_known(agent, known, default), reason=reason, confidence=conf)

    if build or (dims and (part or system)):
        if system:
            return pick("engineer", "system build", 0.85 if build else 0.7)
        if part:
            if _hits(text, LOAD_WORDS) or sim_n:
                return pick("engineer", "part with load requirements", 0.6)
            return pick("designer", "single part", 0.85 if dims else 0.75)
    if sim_n:
        return pick("sim", "test / analyze", 0.85 if (sim_v or check) else 0.75)
    if check:
        return pick("verifier", "check / review", 0.8)
    if sim_v:
        return pick("sim", "test / analyze", 0.7)
    if system:
        return pick("engineer", "system build", 0.55)
    if part or dims:
        return pick("designer", "single part", 0.5)
    if build:
        return pick(default, "a build request with no clear subject", 0.4)
    return Routed(agent=default, reason="no clear signal: the default agent", confidence=0.3)


Classifier = Callable[[str, list[str]], Awaitable["Routed | None"]]


async def route(prompt: str, *, attachments: Iterable[Attachment | dict] = (),
                known: set[str] | None = None, default: str = "engineer",
                classifier: Classifier | None = None) -> Routed:
    """route_rules, plus the optional small-model `classifier(prompt, agents)` when the rules
    are unsure. The classifier's answer is used only if it names a known agent; any failure
    keeps the rules' answer (routing never fails a run)."""
    atts = list(attachments)
    rules = route_rules(prompt, attachments=atts, known=known, default=default)
    if classifier is None or rules.confidence >= ROUTER_ASK_BELOW:
        return rules
    try:
        alt = await classifier(prompt, sorted(known) if known else [])
    except Exception:  # noqa: BLE001 - a classifier failure keeps the rules' answer
        log.warning("router classifier failed; keeping the rules' choice", exc_info=True)
        return rules
    if alt is None or (known is not None and alt.agent not in known):
        return rules
    return alt
