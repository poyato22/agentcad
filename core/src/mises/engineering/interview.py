"""The requirements interview.

"Build me a <system>" is a spec waiting to be written, not a shape to guess. The engineer asks a
short, structured interview first: at most 6 questions, batched 2-3 at a time, each with options
and a free-text fallback. The questions are domain-aware (a legged robot asks about gait and
actuators; a drone about prop size and the flight-controller mount) with a sensible generic
fallback for anything else.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

MAX_QUESTIONS = 6
MAX_PER_BATCH = 3

QuestionKind = Literal["single", "multi", "rank"]


class Question(BaseModel):
    id: str
    text: str
    options: list[str] = Field(default_factory=list)
    kind: QuestionKind = "single"
    allow_free: bool = True  # the user may always answer in their own words


class Interview(BaseModel):
    domain: str
    goal: str
    questions: list[Question]

    @property
    def batches(self) -> list[list[Question]]:
        return [self.questions[i:i + MAX_PER_BATCH]
                for i in range(0, len(self.questions), MAX_PER_BATCH)]


_PRIORITY_OPTIONS = ["weight", "impact strength", "stiffness", "print time"]


def _drone_questions() -> list[Question]:
    return [
        Question(id="use", text="What will this drone do?",
                 options=["freestyle", "racing", "cinematic", "long-range"]),
        Question(id="size_class", text="Prop / size class?",
                 options=['3"', '5"', '7"']),
        Question(id="motor_prop",
                 text="Which motors and props? (pick a class; the catalog fills in thrust/mass)",
                 options=["1404", "2207", "2306", "2806.5"]),
        Question(id="battery", text="Battery pack?",
                 options=["4S LiPo", "6S LiPo"]),
        Question(id="fc_mount", text="Flight-controller / ESC stack mount pattern?",
                 options=["20×20 M3", "25.5×25.5 M3", "30.5×30.5 M3"]),
        Question(id="priorities",
                 text="Rank what matters most, first = most important.",
                 options=_PRIORITY_OPTIONS, kind="rank"),
    ]


def _legged_robot_questions() -> list[Question]:
    return [
        Question(id="use", text="What will the robot carry or do, and roughly what payload (kg)?",
                 options=["inspection / cameras", "light cargo", "manipulation (arm on top)",
                          "research platform"]),
        Question(id="gait_speed", text="Gait and rough top speed?",
                 options=["walk (slow, stable)", "trot (medium)", "run / dynamic (fast)"]),
        Question(id="terrain", text="Where does it operate?",
                 options=["flat indoors", "rough / outdoors", "stairs and obstacles"]),
        Question(id="runtime", text="Battery runtime target per charge?",
                 options=["~15 min", "~30 min", "~60 min+"]),
        Question(id="legs_dof",
                 text="How many legs, degrees of freedom per leg, and actuator class?",
                 options=["quadruped, 3 DOF/leg, hobby servo", "quadruped, 3 DOF/leg, BLDC+gearbox",
                          "biped / humanoid, 6 DOF/leg, BLDC+gearbox"]),
        Question(id="aesthetic",
                 text="Aesthetic intent for the look?",
                 options=["sleek covered (Optimus / NEO-class skin over skeleton)",
                          "exposed mechanical", "functional / no preference"]),
    ]


def _generic_questions(noun: str) -> list[Question]:
    return [
        Question(id="use", text=f"What is the {noun} for, and where does it mount or sit?",
                 options=[]),
        Question(id="envelope", text="Rough size envelope it must fit inside (mm)?",
                 options=["fits in 50 mm", "fits in 100 mm", "fits in 200 mm"]),
        Question(id="loads", text="What loads or forces does it carry, and roughly how large?",
                 options=["light / handling only", "moderate", "heavy / structural"]),
        Question(id="interfaces",
                 text="What does it bolt to or interface with (hole pattern, mating part)?",
                 options=[]),
        Question(id="material", text="Printer and material?",
                 options=["PLA", "PETG", "ABS", "ASA", "PA-CF (nylon-carbon)"]),
        Question(id="priorities", text="Rank what matters most, first = most important.",
                 options=_PRIORITY_OPTIONS, kind="rank"),
    ]


# A legged robot (quadruped / humanoid / walker) is not a drone, even when the word "quad"
# appears: "quadruped" and "robot dog" must never be read as a quadcopter (D26). Legged words
# are checked first so they win over an ambiguous "quad".
_LEGGED_RE = re.compile(
    r"\b(?:quadrupeds?|biped(?:al)?s?|hexapods?|legged|walkers?|humanoids?|dogs?)\b", re.I)
# Each drone stem carries its own \b, so "quadruped" / "squad" no longer match "quad"
# (the previous pattern's single trailing \b left "quad" unanchored).
_DRONE_RE = re.compile(r"\b(?:drones?|quad(?:copter)?s?|fpv|multirotors?)\b", re.I)


def detect_domain(goal: str) -> str:
    text = goal or ""
    if _LEGGED_RE.search(text):
        return "legged_robot"
    if _DRONE_RE.search(text):
        return "drone"
    return "generic"


def _noun(goal: str) -> str:
    # the last noun-ish word of a "build me a <thing>" request, for friendlier generic prompts
    m = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", goal or "")
    skip = {"build", "design", "make", "me", "a", "an", "the", "please", "can", "you"}
    words = [w for w in m if w.lower() not in skip]
    return words[-1].lower() if words else "part"


def build_interview(goal: str) -> Interview:
    """At most MAX_QUESTIONS questions for this request. Never guesses silently: every question
    carries options and a free-text fallback."""
    domain = detect_domain(goal)
    if domain == "drone":
        qs = _drone_questions()
    elif domain == "legged_robot":
        qs = _legged_robot_questions()
    else:
        qs = _generic_questions(_noun(goal))
    qs = qs[:MAX_QUESTIONS]
    return Interview(domain=domain, goal=goal or "", questions=qs)
