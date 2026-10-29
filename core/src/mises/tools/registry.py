"""Tool definitions, the @tool decorator and the registry."""
from __future__ import annotations

import copy
import inspect
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from ..events import EventBus
from ..store import AgentPolicy, Store
from ..types import ErrorCode, MisesError, SafetyClass, ToolOutput

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass
class ToolContext:
    run_id: str
    user: str
    store: Store
    events: EventBus
    workdir: Path
    policy: AgentPolicy
    call_id: str
    services: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_model: type[BaseModel]
    safety: SafetyClass
    handler: Callable[[ToolContext, BaseModel], Awaitable[ToolOutput]]
    onshape_cost: int = 0
    scope_arg: str | None = None
    timeout_s: float = 120
    # Optional, for irreversible tools: computes what the call would change; the gate puts the
    # result in the approval card's `deltas`. It runs before approval, so it must not mutate.
    preview: Callable[[ToolContext, BaseModel], Awaitable[dict]] | None = None
    # Optional: derives the scope keys a call touches from its args (e.g. "onshape:<did>" and
    # "onshape:<did>/<eid>" from a URL). The call is in scope if "*" or ANY key is allowed.
    # Takes precedence over scope_arg.
    scope_keys: Callable[[BaseModel], list[str]] | None = None
    # Optional, per call: a reason string when THIS call needs a human's approval card although
    # its safety class does not ask for one (e.g. a fetch from a domain the user has not
    # allowed); None means no card. Safety class semantics are unchanged.
    needs_approval: Callable[[ToolContext, BaseModel], str | None] | None = None
    # reaches the open web (outside Mises and Onshape): MCP open_world_hint
    open_world: bool = False


def _input_model_of(fn: Callable) -> type[BaseModel]:
    params = list(inspect.signature(fn).parameters.values())
    if len(params) != 2:
        raise TypeError(f"{fn.__name__}: handler must take (ctx, args)")
    try:
        ann = typing.get_type_hints(fn).get(params[1].name)
    except Exception:
        ann = params[1].annotation
    if not (isinstance(ann, type) and issubclass(ann, BaseModel)):
        raise TypeError(f"{fn.__name__}: second parameter must be annotated with a pydantic model")
    return ann


def tool(name: str, *, description: str, safety: SafetyClass = SafetyClass.IRREVERSIBLE,
         onshape_cost: int = 0, scope_arg: str | None = None, timeout_s: float = 120,
         preview: Callable[[ToolContext, Any], Awaitable[dict]] | None = None,
         scope_keys: Callable[[Any], list[str]] | None = None,
         needs_approval: Callable[[ToolContext, Any], str | None] | None = None,
         open_world: bool = False):
    def deco(fn: Callable[[ToolContext, Any], Awaitable[ToolOutput]]) -> ToolDef:
        return ToolDef(name=name, description=description, input_model=_input_model_of(fn),
                       safety=safety, handler=fn, onshape_cost=onshape_cost,
                       scope_arg=scope_arg, timeout_s=timeout_s, preview=preview,
                       scope_keys=scope_keys, needs_approval=needs_approval,
                       open_world=open_world)
    return deco


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = copy.deepcopy(defs[ref.rsplit("/", 1)[1]])
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return _inline_refs({**target, **extra}, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, t: ToolDef) -> None:
        if not NAME_RE.match(t.name):
            raise ValueError(f"invalid tool name: {t.name!r}")
        if t.name in self._tools:
            raise ValueError(f"duplicate tool: {t.name}")
        self._tools[t.name] = t

    def get(self, name: str) -> ToolDef:
        t = self._tools.get(name)
        if t is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown tool: {name}")
        return t

    def list(self, names: list[str] | None = None,
             classes: set[SafetyClass] | None = None) -> list[ToolDef]:
        return [t for t in self._tools.values()
                if (names is None or t.name in names)
                and (classes is None or t.safety in classes)]

    def schema(self, t: ToolDef) -> dict:
        raw = t.input_model.model_json_schema()
        return {"name": t.name, "description": t.description,
                "input_schema": _inline_refs(raw, raw.get("$defs", {}))}
