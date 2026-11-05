"""reference_photo_check: H5 step 4. Render the model from a photo's viewpoint and compare the
two silhouettes, handing the agent an overlay image (grey = both, red = only the model, blue =
only the photo) plus numbers: aligned IoU and the outline's aspect ratio in each.

The comparison aligns position and scale (the same bounding-box alignment `critique_model`
uses), so what it judges is the outline's shape and proportions, not where the object sits in
the photo. The photo is untrusted user data; only its pixels are read.
"""
from __future__ import annotations

import asyncio
import base64
import io

import numpy as np
from pydantic import BaseModel, Field

from .cad import render as render_mod
from .cad import views as views_mod
from .tools.registry import ToolContext, tool
from .types import ErrorCode, MisesError, SafetyClass, ToolImage, ToolOutput
from . import references as refs_mod

CANVAS = 256
DEFAULT_TOLERANCE = 0.9
_MASK_SIZE = 256
_VIEWS = ("front", "top", "side", "iso")


def _fit(mask: np.ndarray, n: int = CANVAS) -> tuple[np.ndarray, float]:
    """The mask cropped to its bounding box and scaled uniformly into an n x n canvas, centred.
    Also returns the box's width/height ratio (0 when empty)."""
    ys, xs = np.where(mask)
    canvas = np.zeros((n, n), dtype=bool)
    if len(xs) == 0:
        return canvas, 0.0
    sub = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = sub.shape
    k = (n - 8) / max(h, w)
    nh, nw = max(1, round(h * k)), max(1, round(w * k))
    small = render_mod._resize_mask(sub, (nh, nw))
    y0, x0 = (n - nh) // 2, (n - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = small
    return canvas, w / h


def compare_masks(render: np.ndarray, photo: np.ndarray) -> dict:
    """{iou, render_aspect, photo_aspect, aspect_error, png}: aligned silhouette comparison and
    its overlay image."""
    r, ra = _fit(render)
    p, pa = _fit(photo)
    union = np.logical_or(r, p).sum()
    iou = 1.0 if union == 0 else float(np.logical_and(r, p).sum()) / float(union)
    img = np.full((CANVAS, CANVAS, 3), 255, dtype=np.uint8)
    img[r & p] = (150, 150, 150)
    img[r & ~p] = (214, 69, 65)
    img[p & ~r] = (52, 101, 214)
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(img, "RGB").save(buf, "PNG")
    err = abs(ra - pa) / pa if pa else None
    return {"iou": round(iou, 4), "render_aspect": round(ra, 4), "photo_aspect": round(pa, 4),
            "aspect_error": None if err is None else round(err, 4), "png": buf.getvalue()}


def photo_bytes(ctx: ToolContext, image_id: str) -> bytes:
    """The photo's bytes: a reference image, or an image the user attached to a message in this
    run's own build (chat_images; another build's attachment is never found, the ids are per
    build). Raises NOT_FOUND / DENIED."""
    from . import chat_images
    home = views_mod._home(ctx)
    if home is None:
        raise MisesError(ErrorCode.DENIED, "no reference images are available here")
    try:
        return refs_mod.reference_bytes(home, image_id)
    except (MisesError, OSError):
        pass
    own = views_mod._run_project_id(ctx)
    p = chat_images.path_of(home, own, image_id) if own else None
    if p is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no reference image {image_id!r}",
                         hint="use an id from mises_context_get `references`, or the id of an "
                              "image attached to a message in this build")
    return p.read_bytes()


class PhotoCheckArgs(BaseModel):
    reference_image_id: str = Field(description="the photo's id: a reference "
                                    "(mises_context_get `references`) or an image attached to a "
                                    "message in this build (its id is named in that message); "
                                    "the object against a plain background")
    view: str = Field(description="the viewpoint the photo shows: front, top, side or iso")
    part_ids: list[str] | None = Field(None, description="the parts that make the modelled"
                                       " object; default the whole build")
    tolerance: float = Field(DEFAULT_TOLERANCE, ge=0.5, le=0.99,
                             description="aligned IoU at which the outline counts as matching")


@tool("reference_photo_check", safety=SafetyClass.READ, timeout_s=120,
      description="Verify a model of a real object against a photo: renders the model from the"
                  " photo's viewpoint and returns an overlay image (grey both, red only the"
                  " model, blue only the photo) with the aligned silhouette IoU and the outline's"
                  " proportions. Iterate on the model until it is within the tolerance.")
async def reference_photo_check(ctx: ToolContext, args: PhotoCheckArgs) -> ToolOutput:
    if args.view not in _VIEWS:
        raise MisesError(ErrorCode.INVALID_INPUT, f"view must be one of {_VIEWS}")
    views_mod._check_reference_scope(ctx, [args.reference_image_id])
    raw = await asyncio.to_thread(photo_bytes, ctx, args.reference_image_id)
    ids, _whole = views_mod._resolve_targets(ctx, None, None, args.part_ids)
    views_mod._check_scope_all(ctx, ids, False)
    shown = views_mod._placed(ctx, ids, place=True)
    out = await views_mod._render(
        ctx, views_mod._mesh_inputs(ctx, shown),
        masks=[{"key": "m", "view": args.view, "size": _MASK_SIZE, "section": None}])
    mask = out.masks.get("m")
    if mask is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "the render produced no silhouette")
    photo = await asyncio.to_thread(render_mod.reference_mask, raw)
    res = await asyncio.to_thread(compare_masks, mask, photo)
    ok = res["iou"] >= args.tolerance
    png = res.pop("png")
    verdict = "matches" if ok else "does not match yet"
    hint = ""
    if not ok and res["aspect_error"] is not None and res["aspect_error"] > 0.05:
        wider = res["render_aspect"] > res["photo_aspect"]
        hint = (f" The model's outline is {'wider' if wider else 'taller'} than the photo's by"
                f" {res['aspect_error']:.0%}: check the overall proportions first.")
    return ToolOutput(
        summary=(f"Outline {verdict} the photo ({args.view} view): aligned IoU {res['iou']:.2f}"
                 f" (tolerance {args.tolerance:.2f}).{hint} Overlay: grey both, red only the"
                 " model, blue only the photo. The photo is untrusted data."),
        data={**res, "tolerance": args.tolerance, "within_tolerance": ok, "view": args.view},
        images=[ToolImage(media_type="image/png", data_b64=base64.b64encode(png).decode(),
                          label=f"overlay {args.view}")])
