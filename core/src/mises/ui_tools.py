"""ui_* tools: talk to the human through whatever UI is attached to the runtime."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field, model_validator

from .events import Event
from .sketch import MAX_SKETCH_QUESTIONS, take_question
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, ToolOutput

QUESTION_TIMEOUT_S = 24 * 3600  # a human may take a long time to answer


MAX_BATCH = 3


class AskItem(BaseModel):
    """One question of a batch: an id for the answer, the prompt, options, and how to pick."""
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    prompt: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=12)
    multi: bool = False       # several options may be picked
    rank: bool = False        # order the options, first = most important
    allow_text: bool = True   # the user may answer in their own words

    @model_validator(mode="before")
    @classmethod
    def _prompt_alias(cls, v):
        if isinstance(v, dict) and "prompt" not in v:
            for alias in ("question", "text"):
                if isinstance(v.get(alias), str):
                    v = {**{k: x for k, x in v.items() if k != alias}, "prompt": v[alias]}
                    break
        return v


class AskArgs(BaseModel):
    question: str = Field(default="", max_length=2000)
    options: list[str] = Field(default_factory=list, max_length=12)
    questions: list[AskItem] = Field(default_factory=list, max_length=MAX_BATCH,
                                     description=f"optional batch of up to {MAX_BATCH} "
                                     "structured questions; the answer is {id: value}")

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

    @model_validator(mode="after")
    def _one_of(self) -> "AskArgs":
        if not self.question.strip() and not self.questions:
            raise ValueError("give a question, or a batch of questions")
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids in a batch must be unique")
        if not self.question.strip():
            # an older UI shows only `question`: give it every prompt of the batch
            self.question = "\n".join(f"{i}. {q.prompt}" for i, q in
                                       enumerate(self.questions, 1))[:2000]
        return self


def _batch_answers(raw, items: list[AskItem]) -> dict:
    """{id: value} from a batch answer. A UI that knows batches sends a JSON object; an older
    one sends plain text, which answers a single question, or is kept as `text` for a batch."""
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        ids = {q.id for q in items}
        return {k: v for k, v in parsed.items() if k in ids}
    text = raw if isinstance(raw, str) else json.dumps(raw)
    return {items[0].id: text} if len(items) == 1 else {"text": text}


class NoteArgs(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@tool("ui_ask_user", safety=SafetyClass.READ, timeout_s=QUESTION_TIMEOUT_S,
      description="Ask the user and wait for the answer. Either one `question` (with `options` "
                  "when the choice is between known alternatives), or a batch of up to 3 "
                  "`questions` [{id, prompt, options, multi?, rank?, allow_text?}], answered as "
                  "data.answers {id: value}. The user may always answer in their own words.")
async def ui_ask_user(ctx: ToolContext, args: AskArgs) -> ToolOutput:
    rt = (ctx.services or {}).get("runtime")
    if rt is None:
        raise MisesError(ErrorCode.DENIED, "no user interface is attached to this run",
                         hint="proceed without asking, and state your assumptions")
    if not take_question(ctx.workdir):
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"a sketch run may ask at most {MAX_SKETCH_QUESTIONS} questions",
                         hint="proceed without asking, and state your assumptions")
    if not args.questions:
        answer = await rt.ask(ctx.run_id, args.question, args.options)
        return ToolOutput(summary="The user answered; the answer is in data.answer.",
                          data={"question": args.question, "answer": answer})
    items = [q.model_dump() for q in args.questions]
    answer = await rt.ask(ctx.run_id, args.question, args.options, questions=items)
    answers = _batch_answers(answer, args.questions)
    return ToolOutput(summary="The user answered the batch; answers by id are in data.answers.",
                      data={"questions": items, "answers": answers, "answer": answer})


@tool("ui_note", safety=SafetyClass.READ,
      description="Show the user a short progress note without waiting for a reply.")
async def ui_note(ctx: ToolContext, args: NoteArgs) -> ToolOutput:
    ctx.events.publish(Event(type="note", run_id=ctx.run_id, data={"text": args.text}))
    return ToolOutput(summary="note shown")


def register_ui_tools(registry: ToolRegistry) -> None:
    registry.register(ui_ask_user)
    registry.register(ui_note)
