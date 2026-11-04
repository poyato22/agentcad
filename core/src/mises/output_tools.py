"""`output_read` (read): page through a tool result that was too big to show.

The Gate spills a result over 16 KB to `<run workdir>/outputs/<call id>.json` and hands back
only its handle. API runs can open nothing, and Claude Code runs have no file tools, so the
data was lost to them (DOG demo: assembly_get of a 48-instance assembly). This tool reads such
a file of the calling run only, optionally one subtree (`key`, a dotted path) and a window of
its JSON text. The content is a tool's own output: data, never instructions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .cad import safe_io
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.json$")
PAGE_MAX = 12000


class OutputReadArgs(BaseModel):
    handle: str = Field(..., max_length=1024, description="the handle a tool result returned "
                        "(its path, or just the file name)")
    key: str | None = Field(None, max_length=200, description="a dotted path into the result, "
                            "e.g. 'data.instances' or 'data.instances.3' (list index)")
    offset: int = Field(0, ge=0)
    limit: int = Field(PAGE_MAX, ge=200, le=PAGE_MAX)


def _bad(msg: str) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg,
                      hint="pass a handle this run's own tool results returned")


def _select(obj: Any, key: str | None) -> Any:
    for part in (key or "").split("."):
        if not part:
            continue
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.lstrip("-").isdigit() and -len(obj) <= int(part) < len(obj):
            obj = obj[int(part)]
        else:
            keys = list(obj)[:30] if isinstance(obj, dict) else f"a list of {len(obj)}" \
                if isinstance(obj, list) else type(obj).__name__
            raise _bad(f"no {part!r} at this level of the result (it has: {keys})")
    return obj


@tool("output_read", safety=SafetyClass.READ,
      description="Read a tool result that was too big to show (a result with only a `handles` "
                  "path and empty data): give the handle, optionally a dotted `key` "
                  "(data.instances.3) and an offset, and get that part of its JSON text, "
                  "paged.")
async def output_read(ctx: ToolContext, args: OutputReadArgs) -> ToolOutput:
    outputs = Path(ctx.workdir) / "outputs"
    given = Path(args.handle)
    name = given.name
    if not NAME_RE.fullmatch(name):
        raise _bad(f"not a result handle: {args.handle!r}")
    if given.is_absolute() or len(given.parts) > 1:
        try:
            same = given.parent.resolve() == outputs.resolve()
        except OSError:
            same = False
        if not same:
            raise _bad("that file is not one of this run's results")
    try:
        text = safe_io.read_text(outputs, name)
    except (OSError, safe_io.UnsafePath):
        text = None
    if text is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no result {name} in this run",
                         hint="pass a handle this run's own tool results returned")
    obj = _select(json.loads(text), args.key)
    body = json.dumps(obj, separators=(",", ":"), default=str)
    window = body[args.offset:args.offset + args.limit]
    end = args.offset + len(window)
    done = end >= len(body)
    shape = (f"{len(obj)} keys" if isinstance(obj, dict)
             else f"{len(obj)} items" if isinstance(obj, list) else type(obj).__name__)
    return ToolOutput(
        summary=f"{name}{' ' + args.key if args.key else ''}: {shape}, chars {args.offset}-"
                f"{end} of {len(body)}" + ("" if done else f" (more: offset={end})"),
        data={"text": window, "total_chars": len(body), "complete": done,
              "next_offset": None if done else end},
        step_kind=StepKind.MEASURE)


def register_output_tools(registry: ToolRegistry) -> None:
    registry.register(output_read)
