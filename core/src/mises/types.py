"""Core enums, errors, message blocks and small shared models."""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class SafetyClass(StrEnum):
    READ = "read"
    LOCAL_MUTATE = "local_mutate"
    ONSHAPE_MUTATE = "onshape_mutate"
    IRREVERSIBLE = "irreversible"


class ErrorCode(StrEnum):
    STALE_VERSION = "stale_version"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NEEDS_APPROVAL = "needs_approval"
    TRANSIENT = "transient"
    INVALID_INPUT = "invalid_input"
    OUT_OF_SCOPE = "out_of_scope"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    TOOL_FAILED = "tool_failed"
    CANCELLED = "cancelled"


class MisesError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    @property
    def retryable(self) -> bool:
        return self.code == ErrorCode.TRANSIENT

    def to_dict(self) -> dict[str, Any]:
        return {"error": str(self.code), "message": self.message,
                "hint": self.hint, "retryable": self.retryable}


class StepKind(StrEnum):
    MESSAGE = "message"
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DIFF = "diff"
    DECISION = "decision"
    ERROR = "error"
    ONSHAPE_CALL = "onshape_call"
    SIM_RUN = "sim_run"
    MEASURE = "measure"
    VERIFY = "verify"
    COMPACTION = "compaction"


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    media_type: str
    data_b64: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


Block = Annotated[TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
                  Field(discriminator="type")]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[Block]
    # assistant only: the model's thinking for this turn, for providers that require it back on
    # later turns (Kimi's reasoning_content); never shown as answer text
    reasoning: str | None = None


class ToolOutput(BaseModel):
    ok: bool = True
    summary: str
    data: dict = Field(default_factory=dict)
    handles: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    step_kind: StepKind = StepKind.TOOL_RESULT


class Caps(BaseModel):
    onshape_calls_run: int = 200
    onshape_calls_year: int = 2500
    max_turns: int = 40
    usd_run: float = 5.0


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
