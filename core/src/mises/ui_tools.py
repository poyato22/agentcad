"""ui_* tools: talk to the human through whatever UI is attached to the runtime."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .events import Event
from .sketch import MAX_SKETCH_QUESTIONS, take_question
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, ToolOutput

QUESTION_TIMEOUT_S = 24 * 3600  # a human may take a long time to answer


class AskArgs(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def _prompt_alias(cls, v):
        # small models often name the field `prompt` (or `text`); accept those as `question`
        if isinstance(v, dict) and "question" not in v:
            for alias in ("prompt", "text"):
                if isinstance(v.get(alias), str):
                    v = {**{k: x for k, x in v.items() if k != alias}, "question": v[alias]}
                    break
        return v


class NoteArgs(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@tool("ui_ask_user", safety=SafetyClass.READ, timeout_s=QUESTION_TIMEOUT_S,
      description="Ask the user a short question and wait for the answer. Offer options when the "
                  "choice is between known alternatives; the user may still answer freely.")
async def ui_ask_user(ctx: ToolContext, args: AskArgs) -> ToolOutput:
    rt = (ctx.services or {}).get("runtime")
    if rt is None:
        raise MisesError(ErrorCode.DENIED, "no user interface is attached to this run",
                         hint="proceed without asking, and state your assumptions")
    if not take_question(ctx.workdir):
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"a sketch run may ask at most {MAX_SKETCH_QUESTIONS} questions",
                         hint="proceed without asking, and state your assumptions")
    answer = await rt.ask(ctx.run_id, args.question, args.options)
    return ToolOutput(summary="The user answered; the answer is in data.answer.",
                      data={"question": args.question, "answer": answer})


@tool("ui_note", safety=SafetyClass.READ,
      description="Show the user a short progress note without waiting for a reply.")
async def ui_note(ctx: ToolContext, args: NoteArgs) -> ToolOutput:
    ctx.events.publish(Event(type="note", run_id=ctx.run_id, data={"text": args.text}))
    return ToolOutput(summary="note shown")


def register_ui_tools(registry: ToolRegistry) -> None:
    registry.register(ui_ask_user)
    registry.register(ui_note)
