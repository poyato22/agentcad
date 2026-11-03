"""reference_ask_measurement: H5 step 5. Ask the user for one critical measurement of a real
object, with an image that marks where to measure (a dimension line with end ticks and a
label) on a photo they gave or on a render of a reference part.

The image is saved under the renders directory and its id rides on the question (`image_id`),
so the app shows it on the question card (GET /renders/{id}.png). The answer comes back as
text; the agent should record it in the dimension sheet as "user measured", confidence high.
"""
from __future__ import annotations

import asyncio
import io

from pydantic import BaseModel, Field

from . import references as refs_mod
from .cad import render as render_mod
from .cad import views as views_mod
from .tools.registry import ToolContext, tool
from .types import ErrorCode, MisesError, SafetyClass, ToolImage, ToolOutput
from .ui_tools import QUESTION_TIMEOUT_S, take_question

_RED = (214, 69, 65)


def annotate(png_or_jpeg: bytes, p0: tuple[float, float], p1: tuple[float, float],
             label: str, *, max_edge: int = 768) -> bytes:
    """The image with a red dimension line from p0 to p1 (fractions of width/height, 0..1),
    a tick at each end and `label` beside the middle. Returns PNG bytes."""
    from PIL import Image, ImageDraw
    with Image.open(io.BytesIO(png_or_jpeg)) as im:
        im = im.convert("RGB")
    im.thumbnail((max_edge, max_edge))
    w, h = im.size
    a, b = (p0[0] * w, p0[1] * h), (p1[0] * w, p1[1] * h)
    d = ImageDraw.Draw(im)
    d.line([a, b], fill=_RED, width=3)
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = max((dx * dx + dy * dy) ** 0.5, 1e-6)
    nx, ny = -dy / n * 9, dx / n * 9           # tick = a short line across each end
    for (x, y) in (a, b):
        d.line([(x - nx, y - ny), (x + nx, y + ny)], fill=_RED, width=3)
    mx, my = (a[0] + b[0]) / 2 + 8, (a[1] + b[1]) / 2 - 14
    text = label[:60]
    box = d.textbbox((mx, my), text)
    d.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=(255, 255, 255))
    d.text((mx, my), text, fill=_RED)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class Point(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class AskMeasureArgs(BaseModel):
    question: str = Field(min_length=3, max_length=500,
                          description="What to measure, e.g. 'Distance between the two front bed"
                                      " screws, centre to centre, in mm?'")
    label: str = Field(min_length=1, max_length=60, description="Short tag drawn on the image")
    start: Point = Field(description="one end of the measurement on the image (fractions)")
    end: Point
    reference_image_id: str | None = Field(None, description="a photo of the object to mark up")
    part_id: str | None = Field(None, description="else a reference part to render and mark up")
    view: str = Field("iso", description="the part's view: front, top, side or iso")


@tool("reference_ask_measurement", safety=SafetyClass.READ, timeout_s=QUESTION_TIMEOUT_S,
      description="Ask the user for one critical measurement of a real object (when a fit depends"
                  " on it and no source gives it). Shows them an image with a dimension line where"
                  " to measure. Answer comes back as text in data.answer; record it in the"
                  " dimension sheet as 'user measured', high confidence. Ask for at most 3.")
async def reference_ask_measurement(ctx: ToolContext, args: AskMeasureArgs) -> ToolOutput:
    rt = (ctx.services or {}).get("runtime")
    if rt is None:
        raise MisesError(ErrorCode.DENIED, "no user interface is attached to this run",
                         hint="proceed without asking, and state your assumptions")
    if (args.reference_image_id is None) == (args.part_id is None):
        raise MisesError(ErrorCode.INVALID_INPUT, "give either reference_image_id or part_id")
    if args.view not in ("front", "top", "side", "iso"):
        raise MisesError(ErrorCode.INVALID_INPUT, "view must be front, top, side or iso")
    if args.reference_image_id is not None:
        views_mod._check_reference_scope(ctx, [args.reference_image_id])
        home = views_mod._home(ctx)
        try:
            base = await asyncio.to_thread(refs_mod.reference_bytes, home, args.reference_image_id)
        except (MisesError, OSError, TypeError) as e:
            raise MisesError(ErrorCode.NOT_FOUND, "no such reference image",
                             hint="use an id from mises_context_get `references`") from e
    else:
        ids, _ = views_mod._resolve_targets(ctx, args.part_id, None, None)
        shown = views_mod._placed(ctx, ids, place=True)
        out = await views_mod._render(
            ctx, views_mod._mesh_inputs(ctx, shown),
            renders=[{"key": "ask", "views": [args.view], "size": 512, "section": None,
                      "cameras": None}])
        base = out.pngs["ask"][args.view]
    if not take_question(ctx.workdir):
        raise MisesError(ErrorCode.INVALID_INPUT, "too many questions this run",
                         hint="proceed without asking, and state your assumptions")
    png = await asyncio.to_thread(annotate, base, (args.start.x, args.start.y),
                                  (args.end.x, args.end.y), args.label)
    tag = views_mod._tag(ctx, "ask")
    views_mod._write_pngs(ctx, tag, {"measure": png})
    image_id = f"{tag}_measure"
    answer = await rt.ask(ctx.run_id, args.question, [], image_id=image_id)
    return ToolOutput(
        summary="The user answered; the value is in data.answer (the user's own words:"
                " read the number and unit, convert to mm).",
        data={"question": args.question, "answer": answer, "image_id": image_id},
        images=[ToolImage(media_type="image/png",
                          data_b64=views_mod._b64(render_mod.fit_png(png, 512)),
                          label="where the user was asked to measure")])
