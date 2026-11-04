"""Concept sketch geometry and images (research 2026-10-30-design-quality §4.4).

- The massing script (VARS + build(v), like any part script) is built in the sandboxed build
  worker (cad/runner.run_build) in its own dir under `<home>/concepts/`, never in the core.
- Scale and ground are core-made meshes (numbers only): a ground disc under the massing (it
  reads as a ground line in the side elevation and a floor in the 3/4 view) and, for anything
  person-sized, a 1.75 m stylised figure standing beside it.
- Rendering runs in the sandboxed render worker with `style="sketch"` (G-R's NPR line art);
  the 3/4 front view asks for a perspective camera, the elevations for orthographic. A renderer
  that does not know the style draws its default (shaded with edges): the sketch still shows
  the massing, only less hand-drawn.
- Pitch silhouettes are the render worker's masks (black fill), drawn to PNG here.
- Callouts (agent) and marks (user) are stored as data in normalised image coordinates; the
  overlay SVG is generated here from that data with every text escaped, never taken from the
  model as markup.
"""
from __future__ import annotations

import asyncio
import io
import math
import struct
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from ..cad import safe_io
from ..cad.render import FIGURE_HEIGHT_MM
from ..cad.render_job import MeshIn, RenderOut, run_render_job
from ..cad.runner import run_build
from ..types import ErrorCode, MisesError

SKETCH_SIZE = 1024
MASK_SIZE = 256
# (forward, up): where each view's camera looks. The front of a design is -Y (the standard
# "front" camera sits on -Y); the 3/4 view is from ahead, to the right (+X) and a little above.
VIEW_CAMERAS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "three_quarter": ((-0.62, 1.0, -0.36), (0.0, 0.0, 1.0)),
    "side": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "front": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "rear": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
}
VIEW_LABEL = {"three_quarter": "3/4 front", "side": "Side", "front": "Front", "rear": "Rear",
              "top": "Top"}
PERSPECTIVE_VIEWS = ("three_quarter",)
DEFAULT_VIEWS = ("three_quarter", "side")
SILHOUETTE_VIEWS = ("front", "side")
FIGURE_MM = FIGURE_HEIGHT_MM            # one shared figure height (FW-core M-7)
# a figure only helps read scale when the design is person-sized (mm): beside a kettle or a
# robot dog a person would shrink the design in its own sketch; those get their size in the
# notes instead
FIGURE_MIN_HEIGHT_MM = 600.0
FIGURE_MIN_EXTENT_MM = 1200.0
SLENDER_RATIO = 5.0                      # height / footprint that counts as slender
MASSING_COLOR = (0.88, 0.88, 0.86)
FIGURE_COLOR = (0.70, 0.72, 0.76)
GROUND_COLOR = (0.93, 0.93, 0.92)
MARK_RGB = (61, 139, 255)              # the app's blue: "the user marked this"


# ---- meshes (triangles as numbers; written as binary STL for the render worker)

def _stl(tris: np.ndarray) -> bytes:
    tris = np.asarray(tris, dtype=np.float32).reshape(-1, 3, 3)
    out = bytearray(b"mises concept".ljust(80, b"\0")) + struct.pack("<I", len(tris))
    for t in tris:
        out += struct.pack("<3f", 0.0, 0.0, 0.0) + t.tobytes() + b"\0\0"
    return bytes(out)


def _loft(cx: float, cy: float, rings: list[tuple[float, float, float]], n: int = 16) -> np.ndarray:
    """A closed solid through elliptic rings [(z, rx, ry)] around (cx, cy), capped at both
    ends: limbs, torso and head of the scale figure, and the ground disc."""
    a = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    pts = [np.stack([cx + rx * np.cos(a), cy + ry * np.sin(a), np.full(n, z)], axis=1)
           for z, rx, ry in rings]
    tris = []
    for p, q in zip(pts, pts[1:]):
        for i in range(n):
            j = (i + 1) % n
            tris += [(p[i], p[j], q[j]), (p[i], q[j], q[i])]
    for ring, flip in ((pts[0], True), (pts[-1], False)):
        c = ring.mean(axis=0)
        for i in range(n):
            j = (i + 1) % n
            tris.append((c, ring[j], ring[i]) if flip else (c, ring[i], ring[j]))
    return np.array(tris, dtype=np.float64)


def figure_tris(x: float, y: float, z0: float, height: float = FIGURE_MM) -> np.ndarray:
    """A plain standing figure `height` tall, feet at z0, facing -Y: legs, torso, arms, neck,
    head. Proportions of a 1.75 m adult (about 7.5 heads)."""
    s = height / FIGURE_MM
    def L(dx, rings):
        return _loft(x + dx * s, y, [(z0 + z * s, rx * s, ry * s) for z, rx, ry in rings])
    parts = [
        L(-95, [(0, 55, 75), (80, 55, 60), (480, 65, 70), (860, 85, 85)]),       # left leg
        L(95, [(0, 55, 75), (80, 55, 60), (480, 65, 70), (860, 85, 85)]),        # right leg
        L(0, [(840, 175, 105), (1050, 160, 95), (1300, 200, 110), (1440, 190, 100)]),  # torso
        L(-235, [(780, 35, 40), (1100, 45, 50), (1420, 55, 60)]),                # left arm
        L(235, [(780, 35, 40), (1100, 45, 50), (1420, 55, 60)]),                 # right arm
        L(0, [(1430, 45, 45), (1530, 45, 45)]),                                  # neck
        L(0, [(1520, 55, 65), (1560, 85, 100), (1650, 95, 110), (1720, 75, 90),
              (1750, 30, 35)]),                                                  # head
    ]
    return np.concatenate(parts, axis=0)


def ground_tris(cx: float, cy: float, z_top: float, radius: float,
                thickness: float) -> np.ndarray:
    return _loft(cx, cy, [(z_top - thickness, radius, radius), (z_top, radius, radius)], n=64)


def scene_extras(bbox: dict, scale_figure: str = "auto") -> tuple[dict[str, np.ndarray], dict]:
    """The ground disc (and the figure, when wanted) for a massing whose metrics bbox is
    `bbox` ({min, max}); plus the framing box of everything drawn (the cameras' frame) and
    whether the figure is in it."""
    lo = np.asarray(bbox["min"], dtype=np.float64)
    hi = np.asarray(bbox["max"], dtype=np.float64)
    size = hi - lo
    extent = float(size.max())
    # a slender design shorter than a person (a model rocket, a lamp, a pole) shrinks to a
    # sliver beside the figure: it reads better framed alone, its size in the notes
    slender = (float(size[2]) < FIGURE_MM and
               float(size[2]) > SLENDER_RATIO * max(1.0, float(max(size[0], size[1]))))
    want = scale_figure == "on" or (scale_figure == "auto" and not slender and (
        float(size[2]) >= FIGURE_MIN_HEIGHT_MM or extent >= FIGURE_MIN_EXTENT_MM))
    meshes: dict[str, np.ndarray] = {}
    gap = max(150.0, 0.15 * extent)
    lo_all, hi_all = lo.copy(), hi.copy()
    if want:
        fx, fy = float(hi[0] + gap + 240.0), float(hi[1] + gap)
        meshes["figure"] = figure_tris(fx, fy, float(lo[2]))
        lo_all = np.minimum(lo_all, [fx - 300.0, fy - 120.0, lo[2]])
        hi_all = np.maximum(hi_all, [fx + 300.0, fy + 120.0, lo[2] + FIGURE_MM])
    cx, cy = float((lo_all[0] + hi_all[0]) / 2), float((lo_all[1] + hi_all[1]) / 2)
    radius = 0.5 * float(np.hypot(hi_all[0] - lo_all[0], hi_all[1] - lo_all[1])) * 1.08
    thickness = max(1.0, 0.004 * float(hi_all[2] - lo_all[2]))
    meshes["ground"] = ground_tris(cx, cy, float(lo[2]), radius, thickness)
    lo_all = np.minimum(lo_all, [cx - radius, cy - radius, lo[2] - thickness])
    hi_all = np.maximum(hi_all, [cx + radius, cy + radius, hi[2]])
    framing = {"min": [round(float(v), 3) for v in lo_all],
               "max": [round(float(v), 3) for v in hi_all]}
    return meshes, {"framing": framing, "figure": want,
                    "massing_size_mm": [round(float(v), 1) for v in size]}


def write_extras(d: Path, meshes: dict[str, np.ndarray]) -> dict[str, Path]:
    """Each extra mesh as `<d>/<name>/model.stl` (the render job reads part dirs)."""
    out = {}
    for name, tris in meshes.items():
        sub = Path(d) / name
        safe_io.ensure_dir(sub)
        safe_io.write_bytes(sub, "model.stl", _stl(tris))
        out[name] = sub
    return out


# ---- the massing build (sandboxed build worker)

async def build_massing(d: Path, script: str) -> dict:
    """Build `script` (VARS + build(v)) in `d` with the sandboxed worker; its metrics. A failed
    build raises INVALID_INPUT with the worker's error (the agent fixes the script)."""
    from ..cad.tools import _after_build
    safe_io.ensure_dir(d)
    safe_io.scrub(d)
    safe_io.write_text(d, "part.py", script)
    res = await run_build(d, {})
    bad = _after_build(d)
    if bad:
        raise MisesError(ErrorCode.INVALID_INPUT, "the massing build left links or special "
                         f"files behind ({', '.join(bad[:5])}); refused")
    if not res.ok:
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"the massing script failed to build: {(res.error or '')[-600:]}",
                         hint="fix massing_script: VARS dict plus build(v) returning one Part "
                              "or Compound of rough volumes (boxes, lofts, capsules)")
    m = res.metrics or {}
    if not isinstance(m.get("bbox"), dict) or not safe_io.exists(d, "model.stl"):
        raise MisesError(ErrorCode.TOOL_FAILED, "the massing built no mesh to sketch")
    return m


CLASH_WORKER = Path(__file__).resolve().parent / "clash_worker.py"


async def massing_clashes(massing_dir: Path, tolerance_pct: float = 2.0) -> dict:
    """Plan 10 C3 (E26): the solids of a built massing that overlap. A pair clashes when its
    common volume is over `tolerance_pct` % of the smaller solid (and over 1 mm3). Runs the
    check in the build sandbox on a copy of model.step. {solids, clashes, checked, note?}."""
    import secrets
    import shutil
    import sys

    from ..sim.proc import run_sandboxed
    from ..sim.types import SimError
    job = Path(massing_dir).parent / f"clash_{secrets.token_hex(4)}"
    safe_io.ensure_dir(job)
    try:
        if safe_io.copy_out(massing_dir, "model.step", job) is None:
            return {"solids": [], "clashes": [], "checked": False,
                    "note": "the massing has no model.step"}
        nonce = secrets.token_hex(16)
        try:
            await run_sandboxed([sys.executable, "-I", str(CLASH_WORKER), str(job)], job,
                                timeout_s=120, what="massing interference",
                                env={"MISES_NONCE": nonce}, memory_limit_mb=2048)
        except SimError as e:
            return {"solids": [], "clashes": [], "checked": False, "note": e.message}
        try:
            res = safe_io.read_json(job, "result.json")
        except (OSError, ValueError, safe_io.UnsafePath):
            res = None
        if not isinstance(res, dict) or res.get("nonce") != nonce or not res.get("ok"):
            return {"solids": [], "clashes": [], "checked": False,
                    "note": (res or {}).get("error") or "the interference check gave no result"}
        solids = res.get("solids") or []
        clashes = []
        for p in res.get("pairs") or []:
            a, b = solids[p["a"]], solids[p["b"]]
            small = max(min(a["volume_mm3"], b["volume_mm3"]), 1e-9)
            share = 100.0 * p["volume_mm3"] / small
            if p["volume_mm3"] > 1.0 and share > tolerance_pct:
                clashes.append({"solids": [p["a"], p["b"]],
                                "labels": [a.get("label") or f"solid {p['a']}",
                                           b.get("label") or f"solid {p['b']}"],
                                "centers_mm": [a["center"], b["center"]],
                                "overlap_mm3": round(p["volume_mm3"], 1),
                                "overlap_pct_of_smaller": round(share, 1)})
        clashes.sort(key=lambda c: -c["overlap_pct_of_smaller"])
        failed = res.get("failed_pairs") or []
        if res.get("incomplete") or failed:
            # fail closed: a pair we could not intersect may clip
            note = res.get("incomplete") or (
                f"the boolean failed for {len(failed)} pair(s) of solids "
                f"({', '.join(f'{a}-{b}' for a, b in failed[:6])})")
            return {"solids": solids, "clashes": clashes, "checked": False, "note": note}
        return {"solids": solids, "clashes": clashes, "checked": True}
    finally:
        shutil.rmtree(job, ignore_errors=True)


# ---- rendering (sandboxed render worker)

def cameras_for(views: list[str]) -> dict[str, list[list[float]]]:
    return {v: [list(VIEW_CAMERAS[v][0]), list(VIEW_CAMERAS[v][1])] for v in views}


def camera_record(view: str, framing: dict, size: int, style: str) -> dict:
    """What maps a normalised image point back to 3D: the camera's direction and up, the
    projection asked for, and the box it frames."""
    fwd, up = VIEW_CAMERAS[view]
    return {"view": view, "forward": list(fwd), "up": list(up),
            "projection": "perspective" if view in PERSPECTIVE_VIEWS else "orthographic",
            "framing": framing, "size": [size, size], "style": style}


async def render_sketch(jobs_root: Path, massing_dir: Path, extras: dict[str, Path],
                        views: list[str], *, size: int = SKETCH_SIZE) -> RenderOut:
    """The sketch images of `views` (key "sk"): massing + ground + figure, style "sketch"."""
    meshes = [MeshIn(massing_dir, "massing", MASSING_COLOR)]
    if "ground" in extras:
        meshes.append(MeshIn(extras["ground"], "ground", GROUND_COLOR))
    if "figure" in extras:
        meshes.append(MeshIn(extras["figure"], "figure", FIGURE_COLOR))
    persp = [v for v in views if v in PERSPECTIVE_VIEWS]
    ortho = [v for v in views if v not in PERSPECTIVE_VIEWS]
    renders = []
    if persp:
        renders.append({"key": "sk_p", "views": persp, "size": size, "style": "sketch",
                        "perspective": True, "cameras": cameras_for(persp)})
    if ortho:
        renders.append({"key": "sk_o", "views": ortho, "size": size, "style": "sketch",
                        "perspective": False, "cameras": cameras_for(ortho)})
    out = await run_render_job(jobs_root, meshes, renders=renders)
    merged = {**out.pngs.get("sk_p", {}), **out.pngs.get("sk_o", {})}
    out.pngs = {"sk": {v: merged[v] for v in views if v in merged}}
    return out


async def render_silhouettes(jobs_root: Path, massing_dir: Path) -> dict[str, bytes]:
    """Black-fill front and side silhouettes of the massing alone (pitch thumbnails)."""
    masks = [{"key": v, "view": v, "size": MASK_SIZE, "cameras": cameras_for([v])}
             for v in SILHOUETTE_VIEWS]
    out = await run_render_job(jobs_root, [MeshIn(massing_dir, "massing", MASSING_COLOR)],
                               masks=masks)
    return await asyncio.to_thread(
        lambda: {v: mask_png(out.masks[v]) for v in SILHOUETTE_VIEWS if v in out.masks})


def mask_png(mask: np.ndarray) -> bytes:
    """A bool mask as a PNG: near-black fill on a transparent background."""
    from PIL import Image
    m = np.asarray(mask, dtype=bool)
    rgba = np.zeros((*m.shape, 4), dtype=np.uint8)
    rgba[m] = (27, 30, 35, 255)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---- overlays

def overlay_svg(callouts: list[dict], size: int = SKETCH_SIZE) -> str:
    """An SVG of the agent's callouts for one image (normalised coordinates), every text
    escaped. Leader lines run from the label (x, y) to the point it names (`to`)."""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
             f'width="{size}" height="{size}">']
    for c in callouts:
        x, y = c["x"] * size, c["y"] * size
        if c.get("to"):
            tx, ty = c["to"][0] * size, c["to"][1] * size
            parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" '
                         'stroke="#1b1e23" stroke-width="1.5"/>'
                         f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3" fill="#1b1e23"/>')
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="18" fill="#1b1e23" '
                     f'font-family="sans-serif">{escape(c["text"])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _font(px: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=px)
    except Exception:  # noqa: BLE001 - a Pillow without FreeType: the small bitmap font
        return ImageFont.load_default()


def _draw_marks(png: bytes, marks: list[tuple[int, dict]]) -> bytes:
    from PIL import Image, ImageDraw
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
    w, h = im.size
    lw = max(3, round(w / 256))
    d = ImageDraw.Draw(im)
    for n, m in marks:
        if m["kind"] == "circle":
            cx, cy, r = m["cx"] * w, m["cy"] * h, m["r"] * w
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=MARK_RGB, width=lw)
            lx, ly = cx + r * 0.72, cy - r * 0.72
        elif m["kind"] == "lasso":
            pts = [(p[0] * w, p[1] * h) for p in m["points"]]
            d.line([*pts, pts[0]], fill=MARK_RGB, width=lw, joint="curve")
            lx, ly = max(pts, key=lambda p: p[0] - p[1])
        else:
            lx, ly = m["x"] * w, m["y"] * h
        br = max(11, round(w / 70))
        d.ellipse((lx - br, ly - br, lx + br, ly + br), fill=MARK_RGB, outline=(255, 255, 255),
                  width=2)
        d.text((lx, ly), str(n), fill=(255, 255, 255), font=_font(round(br * 1.3)),
               anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def annotate(png: bytes, marks: list[tuple[int, dict]]) -> bytes:
    """The sketch image with the user's numbered marks drawn on top (in the app's blue), so a
    model sees exactly what was circled. Runs off the event loop."""
    return await asyncio.to_thread(_draw_marks, png, marks)
