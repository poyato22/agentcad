"""Plan 11 F8a: reasoning effort per turn and per sub-agent.

Mises' levels are low · medium · high · max. Each turn gets one:
- the user's pick in the composer's Think control, per message: auto · normal · hard · max
  (normal = medium, hard = high);
- else the build's default (meta `effort_default:<build>`, set from the Think control);
- else `auto_pick`: open-ended challenges (an egg drop, a competition, "make it interesting")
  and design-class builds think hard, repeated failures or a critique issue that survives a
  fix think harder, routine edits and measurements think little.
A sub-agent gets its spawner's explicit `effort`, else its own auto pick on its brief. A system
note (a study finished) keeps the run's current level.

`control(provider, model, level)` is what each runtime really receives, checked against the
installed Claude Code (2.1.316 `--help`, and its stream-json control protocol):
- Claude Code: `--effort <low|medium|high|max>` when the session starts, and per turn a
  stream-json `control_request` {subtype: apply_flag_settings, settings: {effortLevel}} (the
  Agent SDK's setter), confirmed with `get_settings` (`applied.effort`). The environment never
  carries CLAUDE_CODE_EFFORT_LEVEL (it would pin the session's effort).
- Anthropic API: `output_config: {effort}` on models that support effort (Opus 4.5 up to
  `high`; Opus 4.6+, Sonnet 4.6+, the 5.x families and Haiku 5.5 all four levels).
- OpenAI reasoning models (o-series, gpt-5): `reasoning_effort` low / medium / high (max ->
  high). Any other runtime: no control, recorded as such.
"""
from __future__ import annotations

import contextvars
import re
import time
from typing import Any, Literal

LEVELS = ("low", "medium", "high", "max")
CHOICES = ("auto", "normal", "hard", "max")
Think = Literal["auto", "normal", "hard", "max"]          # the composer's Think control
CHOICE_LEVEL = {"normal": "medium", "hard": "high", "max": "max"}
LEVEL_CHOICE = {"low": "auto", "medium": "normal", "high": "hard", "max": "max"}
LABEL = {"low": "Light", "medium": "Normal", "high": "Hard", "max": "Max"}
DEFAULT_META = "effort_default:{}"

#: the Think choice of the HTTP request being handled (POST /projects/{id}/chat): set by the
#: route, read by orchestrator.lead.submit, so the message keeps it through the lead's queue
REQUESTED: contextvars.ContextVar[str | None] = contextvars.ContextVar("mises_effort",
                                                                         default=None)


def normalise(choice: Any) -> str | None:
    """A Think choice (auto/normal/hard/max) or a level name -> its choice; None when unset or
    unknown (treated as auto)."""
    if not isinstance(choice, str):
        return None
    c = choice.strip().lower()
    if c in CHOICES:
        return c
    if c in LEVELS:
        return LEVEL_CHOICE[c] if c != "low" else "low"
    return None


def _rank(level: str) -> int:
    return LEVELS.index(level)


# ---- the auto pick

_ROUTINE = re.compile(
    r"^\s*(what('?s| is| are)|how (big|long|wide|tall|heavy|thick|much|many)|measure|show|"
    r"hide|rename|delete|remove|undo|move|rotate|color|colour|make it (red|blue|green|black|"
    r"white|grey|gray)|set|change the (colou?r|name|material)|thanks|thank you|ok\b|okay)\b",
    re.I)
_SMALL_EDIT = re.compile(r"\b\d+(\.\d+)?\s?(mm|cm|m|in|deg|°|%)\b", re.I)
_HARD_ASK = re.compile(r"\b(think (hard|carefully|deeply)|really (think|careful)|"
                       r"take your time|best possible|optimi[sz]e|from scratch|"
                       r"redesign|rethink)\b", re.I)


def auto_pick(text: str, *, design_class: str | None = None, challenge: dict | None = None,
              failures: int = 0, critique_repeats: int = 0) -> tuple[str, list[str]]:
    """(level, reasons) for one turn from the request and the run's recent history."""
    from .ideation import challenge_signal
    level, reasons = "medium", []

    def up(lvl: str, why: str) -> None:
        nonlocal level
        reasons.append(why)
        if _rank(lvl) > _rank(level):
            level = lvl

    words = len((text or "").split())
    routine = bool(_ROUTINE.search(text or "")
                   or (words <= 14 and _SMALL_EDIT.search(text or "")))
    sig = challenge_signal(text or "")
    if sig is not None and sig.get("off"):
        sig = None
    if sig:
        what = {"egg_drop": "an egg-drop challenge", "competition": "a design challenge",
                "creative": "an open-ended creative request"}.get(sig.get("kind"), "a challenge")
        up("max" if sig.get("creative") else "high", f"open-ended: {what}")
    elif challenge and not routine:
        up("high", "a follow-up in an open-ended challenge build")
    if failures >= 2:
        up("max", f"the last {failures} turns failed")
    elif failures == 1:
        up("high", "the last turn failed")
    if critique_repeats:
        up("high", "a critique issue survived the last fix")
    if design_class == "design":
        up("high", "a design-heavy build")
    if _HARD_ASK.search(text or ""):
        up("high", "the user asked for careful thought")
    if not reasons:
        if routine:
            return "low", ["a routine edit or measurement"]
        return "medium", ["a regular request"]
    return level, reasons


def run_signals(store, run_id: str) -> dict:
    """failures: consecutive recent turns that ended in an error step; critique_repeats:
    critique rounds since the previous user message that asked for a strategy switch."""
    from .types import StepKind
    try:
        steps = store.steps(run_id, [StepKind.MESSAGE, StepKind.ERROR, StepKind.VERIFY])
    except Exception:  # noqa: BLE001
        return {"failures": 0, "critique_repeats": 0}
    turns: list[list] = []
    for s in steps:
        if s.kind == StepKind.MESSAGE and s.payload.get("role") == "user" \
                and "system_note" not in s.payload:
            turns.append([])
        elif turns:
            turns[-1].append(s)
    failures = 0
    for t in reversed(turns):
        if any(s.kind == StepKind.ERROR for s in t):
            failures += 1
        else:
            break
    repeats = 0
    for s in (turns[-1] if turns else []):
        data = ((s.payload.get("output") or {}).get("data") or {}) \
            if s.kind == StepKind.VERIFY else {}
        crit = data.get("untrusted_critique") if isinstance(data.get("untrusted_critique"),
                                                            dict) else data
        if data.get("strategy_switch") or (crit or {}).get("repeated"):
            repeats += 1
    return {"failures": failures, "critique_repeats": repeats}


def build_default(store, project_id: str | None) -> str | None:
    if not project_id:
        return None
    try:
        return normalise(store.meta_get(DEFAULT_META.format(project_id)))
    except Exception:  # noqa: BLE001
        return None


def set_build_default(store, project_id: str, choice: str | None) -> str | None:
    c = normalise(choice)
    if c in (None, "auto", "low"):
        store.meta_delete(DEFAULT_META.format(project_id))
        return "auto"
    store.meta_set(DEFAULT_META.format(project_id), c)
    return c


def decide(store, run_id: str, text: str, choice: Any = None) -> dict:
    """The effort decision for one turn of `run_id`: {level, choice, source, reasons}."""
    c = normalise(choice)
    if c == "low":
        return {"level": "low", "choice": "auto", "source": "user", "reasons": ["chosen"]}
    if c in CHOICE_LEVEL:
        return {"level": CHOICE_LEVEL[c], "choice": c, "source": "user",
                "reasons": [f"the user picked {LABEL[CHOICE_LEVEL[c]]}"]}
    try:
        run = store.get_run(run_id)
    except KeyError:
        run = None
    project = getattr(run, "project_id", None)
    d = build_default(store, project)
    if d in CHOICE_LEVEL:
        return {"level": CHOICE_LEVEL[d], "choice": "auto", "source": "build",
                "reasons": [f"the build's default ({LABEL[CHOICE_LEVEL[d]]})"]}
    from .ideation import build_challenge
    dc = getattr(run, "design_class", None) or {}
    sig = run_signals(store, run_id)
    level, reasons = auto_pick(text, design_class=dc.get("cls") if isinstance(dc, dict) else None,
                               challenge=build_challenge(store, project), **sig)
    return {"level": level, "choice": "auto", "source": "auto", "reasons": reasons}


# ---- what each runtime receives

_ANTHROPIC_ALL = re.compile(r"claude-(opus|sonnet)-4-[6-9]|claude-(opus|sonnet|haiku|fable|"
                            r"mythos)-[5-9]")
_ANTHROPIC_HIGH = re.compile(r"claude-opus-4-5")
_OPENAI_REASONING = re.compile(r"^(o[1-9]|gpt-5)")


def claude_code_effort(level: str) -> str:
    """Claude Code's --effort / effortLevel value (it takes low, medium, high, xhigh, max)."""
    return level if level in LEVELS else "medium"


def anthropic_effort(model: str, level: str) -> str | None:
    if _ANTHROPIC_ALL.search(model or ""):
        return level
    if _ANTHROPIC_HIGH.search(model or ""):
        return "high" if level == "max" else level
    return None


def openai_effort(model: str, level: str) -> str | None:
    if _OPENAI_REASONING.search(model or ""):
        return "high" if level == "max" else level
    return None


def control(provider: str, model: str, level: str) -> dict:
    """What the runtime is sent for `level`: {runtime, param, value} or {runtime, supported:
    False, note}."""
    if provider == "claude-code":
        return {"runtime": "claude-code", "param": "--effort / apply_flag_settings.effortLevel",
                "value": claude_code_effort(level)}
    if provider == "anthropic":
        v = anthropic_effort(model, level)
        return ({"runtime": "anthropic", "param": "output_config.effort", "value": v}
                if v else {"runtime": "anthropic", "supported": False,
                           "note": f"{model} takes no effort setting"})
    if provider == "openai":
        v = openai_effort(model, level)
        return ({"runtime": "openai", "param": "reasoning_effort", "value": v}
                if v else {"runtime": "openai", "supported": False,
                           "note": f"{model} is not a reasoning model"})
    return {"runtime": provider, "supported": False,
            "note": "this runtime has no reasoning-effort control"}


def record(rt, run_id: str, decision: dict, ctl: dict) -> dict:
    """The decision as a DECISION step just before the turn's user message: the thread shows
    it on that message (the app fetches it with the turn's steps; no event of its own)."""
    from .types import StepKind
    body = {**decision, "label": LABEL[decision["level"]], "control": ctl, "at": time.time()}
    rt.store.append_step(run_id, StepKind.DECISION, {"effort": body})
    return body
