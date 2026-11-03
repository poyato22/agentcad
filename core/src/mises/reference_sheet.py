"""reference_sheet_*: H5 dimension sheets and named interface datums for a real-world object.

When no CAD exists for the object a build fits (or to add interface datums to an imported
reference part), the agent records every dimension it found with its source and a confidence,
plus named datums (bolt holes, rails, bed size) to design against. A sheet is one JSON file,
`<workdir>/references/<ref_id>.json`.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .cad import safe_io
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, ToolOutput

CONFIDENCE = ("high", "medium", "low")
REF_ID = r"^[a-z][a-z0-9_]{0,39}$"


def _conf(v: str) -> str:
    if v not in CONFIDENCE:
        raise ValueError(f"confidence must be one of {CONFIDENCE}")
    return v


class Dim(BaseModel):
    value_mm: float = Field(gt=-100_000, lt=100_000)
    source: str = Field(min_length=3, max_length=300,
                        description="Where the number comes from: a link, a manual page, a photo"
                                    " measured against a known object, or 'user measured'")
    confidence: str = Field(description="high (CAD or datasheet), medium (a drawing or a"
                                        " measured photo), low (a guess)")
    _c = field_validator("confidence")(lambda cls, v: _conf(v))


class Datum(BaseModel):
    kind: str = Field(description="hole, rail, slot, face, point or size")
    position_mm: list[float] | None = Field(None, min_length=3, max_length=3,
                                            description="x, y, z in the reference part's frame")
    axis: list[float] | None = Field(None, min_length=3, max_length=3)
    size_mm: float | None = Field(None, description="hole diameter, rail width, bed side...")
    note: str = Field("", max_length=300)
    source: str = Field(min_length=3, max_length=300)
    confidence: str
    _c = field_validator("confidence")(lambda cls, v: _conf(v))


class SheetSetArgs(BaseModel):
    ref_id: str = Field(pattern=REF_ID, description="short id of the real object, e.g. prusa_mini")
    name: str = Field("", max_length=120)
    part_id: str | None = Field(None, description="the reference part it describes, if imported")
    dims: dict[str, Dim] = Field(default_factory=dict,
                                 description="named dimensions, e.g. bed_x, frame_width")
    datums: dict[str, Datum] = Field(default_factory=dict,
                                     description="named interface features to design against,"
                                                 " e.g. bed_hole_front_left, y_rail")


class SheetGetArgs(BaseModel):
    ref_id: str = Field(pattern=REF_ID)


def load_sheet(workdir: Path | str, ref_id: str) -> dict | None:
    p = Path(workdir) / "references" / f"{ref_id}.json"
    if not p.is_file() or p.is_symlink():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def sheet_gaps(sheet: dict) -> list[str]:
    """Entries an agent should not rely on yet (low confidence)."""
    low = [f"dim {k}" for k, d in sheet.get("dims", {}).items() if d["confidence"] == "low"]
    return low + [f"datum {k}" for k, d in sheet.get("datums", {}).items()
                  if d["confidence"] == "low"]


def _report(sheet: dict) -> tuple[str, list[str]]:
    gaps = sheet_gaps(sheet)
    return (f"Sheet {sheet['ref_id']}: {len(sheet['dims'])} dimension(s),"
            f" {len(sheet['datums'])} datum(s); {len(gaps)} low confidence"
            + (f" (confirm before relying on: {', '.join(gaps)})." if gaps else ".")), gaps


@tool("reference_sheet_set", safety=SafetyClass.LOCAL_MUTATE, scope_arg="ref_id",
      description="Create or update the dimension sheet of a real object the build attaches to"
                  " (no CAD found, or to add datums to an imported reference part). Every"
                  " dimension and datum needs a source and a confidence (high/medium/low). Datums"
                  " are named interface features (bolt holes, rails, bed size) that you design"
                  " against by reading them back with reference_sheet_get. Entries given here"
                  " replace entries of the same name; others are kept.")
async def reference_sheet_set(ctx: ToolContext, args: SheetSetArgs) -> ToolOutput:
    d = Path(ctx.workdir) / "references"
    safe_io.ensure_dir(d)
    sheet = load_sheet(ctx.workdir, args.ref_id) or {"ref_id": args.ref_id, "dims": {},
                                                     "datums": {}}
    if args.name:
        sheet["name"] = args.name
    if args.part_id:
        sheet["part_id"] = args.part_id
    sheet["dims"].update({k: v.model_dump() for k, v in args.dims.items()})
    sheet["datums"].update({k: v.model_dump() for k, v in args.datums.items()})
    safe_io.write_text(d, f"{args.ref_id}.json", json.dumps(sheet, indent=1))
    text, gaps = _report(sheet)
    return ToolOutput(summary=text, data={"sheet": sheet, "low_confidence": gaps})


@tool("reference_sheet_get", safety=SafetyClass.READ,
      description="Read a reference object's dimension sheet: dimensions and named datums, each"
                  " with its source and confidence.")
async def reference_sheet_get(ctx: ToolContext, args: SheetGetArgs) -> ToolOutput:
    sheet = load_sheet(ctx.workdir, args.ref_id)
    if sheet is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no sheet {args.ref_id!r}",
                         hint="create it with reference_sheet_set")
    text, gaps = _report(sheet)
    return ToolOutput(summary=text, data={"sheet": sheet, "low_confidence": gaps})


SHEET_TOOLS = [reference_sheet_set, reference_sheet_get]


def register_sheet_tools(registry: ToolRegistry) -> None:
    for t in SHEET_TOOLS:
        registry.register(t)
