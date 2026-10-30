"""Silhouette matching of renders against reference images (G1 / G-R4).

A reference is scored only against the view it shows:
- tagged `front|top|side|iso`: that one view;
- `sheet` with `regions`: each region against its own view;
- untagged (`unknown`, or a sheet without regions): every view is tried, and only the single
  best-matching view is reported, marked "untagged reference" (it is a guess, not a verdict on
  the other three views).
Each comparison aligns position and scale (bounding box) and tries the 8 orientations (0, 90,
180, 270 degrees, each optionally mirrored), keeping the best, so a reference drawn rotated or
flipped still matches its own view.
"""
from __future__ import annotations

import numpy as np

from . import render as render_mod

VIEWS = ("front", "top", "side", "iso")
UNTAGGED = "untagged reference"


def orientations(mask: np.ndarray):
    """(rotation degrees, mirrored, mask) for the 8 rotations/mirrors of `mask`."""
    for mirrored in (False, True):
        m = np.fliplr(mask) if mirrored else mask
        for k in range(4):
            yield 90 * k, mirrored, np.rot90(m, k)


def best_orientation(render_mask: np.ndarray, ref_mask: np.ndarray) -> tuple[float, int, bool]:
    """(best aligned IoU, rotation, mirrored) of `ref_mask` against `render_mask`."""
    best = (-1.0, 0, False)
    for rot, mir, m in orientations(ref_mask):
        iou = render_mod.align_and_iou(render_mask, m)
        if iou > best[0] + 1e-9:
            best = (iou, rot, mir)
    return best


def crop(mask: np.ndarray, region: dict) -> np.ndarray:
    h, w = mask.shape
    y0, y1 = int(region["y0"] * h), max(int(region["y0"] * h) + 1, int(region["y1"] * h))
    x0, x1 = int(region["x0"] * w), max(int(region["x0"] * w) + 1, int(region["x1"] * w))
    return mask[y0:y1, x0:x1]


def needed_views(refs: list[dict]) -> list[str]:
    """The render views the references need masks for."""
    out: set[str] = set()
    for r in refs:
        view = r.get("view") or "unknown"
        if view in VIEWS:
            out.add(view)
        elif view == "sheet" and r.get("regions"):
            out.update(v for v in r["regions"] if v in VIEWS)
        else:
            out.update(VIEWS)
    return [v for v in VIEWS if v in out]


def score(refs: list[dict], ref_masks: dict[str, np.ndarray],
          render_masks: dict[str, np.ndarray]) -> tuple[dict[str, float], list[dict]]:
    """({view: best IoU over the references that apply to it}, per-reference scores).

    `refs` are reference summaries ({reference_id, view, regions}); `ref_masks` the foreground
    mask of each reference image by id; `render_masks` the render's silhouette per view."""
    per_view: dict[str, float] = {}
    scores: list[dict] = []

    def note(entry: dict) -> None:
        scores.append(entry)
        v = entry["view"]
        per_view[v] = max(per_view.get(v, 0.0), entry["iou"])

    for r in refs:
        rid = r["reference_id"]
        mask = ref_masks.get(rid)
        if mask is None or not mask.any():
            scores.append({"reference_id": rid, "view": None, "iou": None,
                           "note": "reference image has no foreground to compare"})
            continue
        view = r.get("view") or "unknown"
        if view in VIEWS:
            if view in render_masks:
                iou, rot, mir = best_orientation(render_masks[view], mask)
                note({"reference_id": rid, "view": view, "iou": round(iou, 3),
                      "rotation": rot, "mirrored": mir, "tagged": True})
            continue
        if view == "sheet" and r.get("regions"):
            for v, region in r["regions"].items():
                if v in render_masks:
                    iou, rot, mir = best_orientation(render_masks[v], crop(mask, region))
                    note({"reference_id": rid, "view": v, "iou": round(iou, 3),
                          "rotation": rot, "mirrored": mir, "tagged": True, "region": region})
            continue
        best = None
        for v in VIEWS:
            if v not in render_masks:
                continue
            iou, rot, mir = best_orientation(render_masks[v], mask)
            if best is None or iou > best[0]:
                best = (iou, v, rot, mir)
        if best is not None:
            note({"reference_id": rid, "view": best[1], "iou": round(best[0], 3),
                  "rotation": best[2], "mirrored": best[3], "tagged": False, "note": UNTAGGED})
    return {v: round(per_view[v], 3) for v in VIEWS if v in per_view}, scores
