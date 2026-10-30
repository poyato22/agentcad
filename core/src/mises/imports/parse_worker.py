"""Parses an untrusted drawing file in a sandboxed child:
python -I parse_worker.py <job_dir> pdf|image|dxf|render

The parent (mises.imports.drawing_import) runs this under sandbox-exec (writes only under
job_dir, no network, no new processes) with a timeout. It reads job_dir/input.<ext> and writes
job_dir/result.json carrying $MISES_NONCE:

- pdf: the first MAX_PAGES pages rasterized at DPI (fewer pixels when a page would pass
  MAX_PIXELS), each made ready for a model's image input here (long edge MAX_IMAGE_EDGE, PNG,
  else JPEG within IMAGE_B64_BUDGET), plus overlapping 2x2 tiles of a big sheet (so its
  dimension text stays legible), and each page's text layer (a vector PDF's numbers).
- image: a PNG / JPEG upload made ready the same way (decoded here, never in the parent).
- dxf: the modelspace geometry as plain numbers (lines, circles, arcs, polylines with bulges,
  splines and ellipses flattened to polylines, block inserts expanded), DIMENSION entities with
  their measurement (times the dimension's DIMLFAC) and definition points, and TEXT/MTEXT
  strings without formatting codes (cut to MAX_TEXT).
- render: Mises's own drawing (a DXF the core wrote) rasterized to render.png, for the
  second-look comparison.

Nothing here imports mises: the child only needs pypdfium2 / ezdxf / Pillow. The parent
validates everything in result.json before using it.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import sys
from pathlib import Path

DPI = 200
MAX_PAGES = 6
MAX_IMAGES = 16                  # pages first, then tiles
MAX_PIXELS = 40_000_000
MAX_IMAGE_EDGE = 1568            # same as mises.sketch
IMAGE_B64_BUDGET = int(3.75 * 1024 * 1024)
TILE_FROM = 2 * MAX_IMAGE_EDGE   # a rendered page longer than this also gets 2x2 tiles
TILE_OVERLAP = 0.08
MAX_PAGE_TEXT = 20_000
MAX_ENTITIES = 20_000
MAX_TEXT = 300
MAX_TEXTS = 200
MAX_INSERT_DEPTH = 3
FLATTEN_MM = 0.01


def _limits() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass


def _ready(img, job: Path, stem: str) -> dict:
    """Save `img` for a model: long edge <= MAX_IMAGE_EDGE, PNG when it fits the budget (line
    art), else JPEG stepping down in quality and size."""
    from PIL import Image
    img = img.copy()
    img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    ext = "png"
    quality = 85
    while len(base64.b64encode(buf.getvalue())) >= IMAGE_B64_BUDGET:
        ext = "jpg"
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if quality > 40:
            quality -= 15
        else:
            img = img.resize((max(1, img.width * 4 // 5), max(1, img.height * 4 // 5)))
    name = f"{stem}.{ext}"
    (job / name).write_bytes(buf.getvalue())
    return {"file": name, "width": img.width, "height": img.height}


def _tiles(img, job: Path, stem: str) -> list[dict]:
    w, h = img.size
    tw, th = int(w / 2 * (1 + TILE_OVERLAP)), int(h / 2 * (1 + TILE_OVERLAP))
    out = []
    for k, (x, y) in enumerate(((0, 0), (w - tw, 0), (0, h - th), (w - tw, h - th)), 1):
        t = _ready(img.crop((x, y, x + tw, y + th)), job, f"{stem}-tile-{k}")
        t["tile"] = k
        out.append(t)
    return out


def _pdf(job: Path) -> dict:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(job / "input.pdf"))
    except pdfium.PdfiumError as e:
        raise SystemExit(f"could not open the PDF ({e}); it may be damaged or password-protected")
    total = len(pdf)
    pages, tiles = [], []
    for i in range(min(total, MAX_PAGES)):
        page = pdf[i]
        w_pt, h_pt = page.get_size()
        if not (w_pt > 0 and h_pt > 0 and math.isfinite(w_pt * h_pt)):
            raise SystemExit(f"page {i + 1} has no size")
        scale = DPI / 72
        if w_pt * h_pt * scale * scale > MAX_PIXELS:
            scale = math.sqrt(MAX_PIXELS / (w_pt * h_pt))
        img = page.render(scale=scale).to_pil()
        try:
            tp = page.get_textpage()
            text = tp.get_text_bounded()[:MAX_PAGE_TEXT]
            tp.close()
        except Exception:  # noqa: BLE001 - no text layer is a normal case (a scan)
            text = ""
        item = _ready(img, job, f"page-{i + 1}")
        item.update(page=i + 1, dpi=round(scale * 72, 1), text=text,
                    full_px=[img.width, img.height])
        pages.append(item)
        if max(img.size) > TILE_FROM:
            for t in _tiles(img, job, f"page-{i + 1}"):
                t["page"] = i + 1
                tiles.append(t)
        page.close()
    pdf.close()
    room = max(0, MAX_IMAGES - len(pages))
    return {"pages_total": total, "pages": pages, "tiles": tiles[:room]}


def _image(job: Path) -> dict:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    src = next((job / n for n in ("input.png", "input.jpg") if (job / n).is_file()), None)
    if src is None:
        raise SystemExit("no image")
    try:
        with Image.open(src, formats=["PNG", "JPEG"]) as im:
            if im.width * im.height > MAX_PIXELS:
                raise SystemExit("the image is too large (over 40 megapixels)")
            if im.format == "JPEG":
                im.draft("RGB", (MAX_IMAGE_EDGE * 2, MAX_IMAGE_EDGE * 2))
            img = ImageOps.exif_transpose(im)
            img.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise SystemExit("could not read this image; the file may be damaged") from None
    item = _ready(img, job, "page-1")
    item.update(page=1, text="", full_px=[img.width, img.height])
    return {"pages_total": 1, "pages": [item], "tiles": []}


def _render(job: Path) -> dict:
    """Mises's own drawing DXF → render.png (lines, arcs, circles, splines, and text)."""
    import ezdxf
    from ezdxf import bbox, path
    from PIL import Image, ImageDraw, ImageFont

    doc = ezdxf.readfile(str(job / "input.dxf"))
    msp = doc.modelspace()
    ext = bbox.extents(msp)
    if not ext.has_data:
        raise SystemExit("the drawing is empty")
    x0, y0 = ext.extmin.x, ext.extmin.y
    w, h = max(ext.size.x, 1e-3), max(ext.size.y, 1e-3)
    s = (MAX_IMAGE_EDGE - 20) / max(w, h)
    W, H = int(w * s) + 20, int(h * s) + 20
    img = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(img)

    def P(x, y):
        return (10 + (x - x0) * s, H - 10 - (y - y0) * s)

    for e in msp:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            text = e.plain_text() if t == "MTEXT" else e.dxf.text
            ins = e.dxf.insert
            size = max(8, int((e.dxf.get("height", 2.5) or 2.5) * s))
            font = ImageFont.load_default(size=size)
            rot = float(e.dxf.get("rotation", 0.0) or 0.0)
            if abs(rot) < 1:
                x, y = P(ins.x, ins.y)
                g.text((x, y - size), text, fill="black", font=font)
            else:
                box = font.getbbox(text)
                tile = Image.new("L", (box[2] + 2, box[3] + 2), 255)
                ImageDraw.Draw(tile).text((1, 1), text, fill=0, font=font)
                tile = tile.rotate(rot, expand=True, fillcolor=255)
                x, y = P(ins.x, ins.y)
                img.paste(Image.new("RGB", tile.size, "black"),
                          (int(x - tile.width), int(y - tile.height)),
                          Image.eval(tile, lambda v: 255 - v))
            continue
        try:
            pts = [P(v.x, v.y) for v in path.make_path(e).flattening(0.05)]
        except (TypeError, ValueError):
            continue
        if len(pts) >= 2:
            g.line(pts, fill="black", width=1)
    img.save(job / "render.png", "PNG", optimize=True)
    return {"file": "render.png", "width": W, "height": H}


class _Collector:
    def __init__(self) -> None:
        self.entities: list[dict] = []
        self.texts: list[str] = []
        self.skipped: dict[str, int] = {}
        self.truncated = False

    def add(self, item: dict) -> None:
        if len(self.entities) >= MAX_ENTITIES:
            self.truncated = True
            return
        self.entities.append(item)

    def skip(self, kind: str) -> None:
        self.skipped[kind] = self.skipped.get(kind, 0) + 1


def _xy(v) -> list[float]:
    return [float(v[0]), float(v[1])]


def _entity(e, out: _Collector, depth: int) -> None:
    t = e.dxftype()
    layer = str(e.dxf.get("layer", "0"))[:64]
    if t == "LINE":
        out.add({"t": "line", "layer": layer, "a": _xy(e.dxf.start), "b": _xy(e.dxf.end)})
    elif t in ("CIRCLE", "ARC"):
        ocs = e.ocs()
        c = ocs.to_wcs(e.dxf.center)
        item = {"t": t.lower(), "layer": layer, "c": _xy(c), "r": float(e.dxf.radius)}
        if t == "ARC":
            a0, a1 = float(e.dxf.start_angle), float(e.dxf.end_angle)
            if e.dxf.extrusion[2] < 0:          # mirrored OCS: angles run the other way
                a0, a1 = 180.0 - a1, 180.0 - a0
            item.update(a0=a0, a1=a1)
        out.add(item)
    elif t == "LWPOLYLINE":
        ocs = e.ocs()
        flip = e.dxf.extrusion[2] < 0
        elev = float(e.dxf.get("elevation", 0.0))
        pts = []
        for x, y, b in e.get_points("xyb"):
            w = ocs.to_wcs((x, y, elev))
            pts.append([float(w[0]), float(w[1]), -float(b) if flip else float(b)])
        out.add({"t": "poly", "layer": layer, "pts": pts, "closed": bool(e.closed)})
    elif t == "POLYLINE":
        if not e.is_2d_polyline:
            out.skip("POLYLINE_3D")
            return
        pts = [[float(v.dxf.location[0]), float(v.dxf.location[1]), float(v.dxf.get("bulge", 0))]
               for v in e.vertices]
        out.add({"t": "poly", "layer": layer, "pts": pts, "closed": bool(e.is_closed)})
    elif t in ("SPLINE", "ELLIPSE"):
        try:
            pts = [_xy(p) + [0.0] for p in e.flattening(FLATTEN_MM)]
        except Exception:  # noqa: BLE001 - a broken curve is skipped, not fatal
            out.skip(t)
            return
        closed = len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) < 1e-6
        out.add({"t": "poly", "layer": layer, "pts": pts, "closed": closed, "flattened": True})
    elif t == "DIMENSION":
        try:
            m = e.get_measurement()
            measurement = float(m) if isinstance(m, (int, float)) else float(abs(m))
        except Exception:  # noqa: BLE001
            measurement = None
        try:     # a scaled detail view states the length times its DIMLFAC
            lfac = float(e.override().get("dimlfac", 1.0) or 1.0)
        except Exception:  # noqa: BLE001
            lfac = 1.0
        if measurement is not None and int(e.dimtype) not in (2, 5) and lfac > 0:
            measurement *= lfac
        pts = {}
        for k in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "text_midpoint"):
            v = e.dxf.get(k)
            if v is not None:
                pts[k] = _xy(v)
        out.add({"t": "dim", "layer": layer, "dimtype": int(e.dimtype),
                 "measurement": measurement, "angle": float(e.dxf.get("angle", 0.0)),
                 "text": _plain(str(e.dxf.get("text", "")))[:MAX_TEXT], "pts": pts,
                 "dimlfac": lfac})
    elif t in ("TEXT", "MTEXT", "ATTRIB"):
        if len(out.texts) < MAX_TEXTS:
            s = e.plain_text() if t == "MTEXT" else str(e.dxf.get("text", ""))
            out.texts.append(s[:MAX_TEXT])
    elif t == "INSERT":
        if depth >= MAX_INSERT_DEPTH:
            out.skip("INSERT_NESTED")
            return
        try:
            for v in e.virtual_entities():
                _entity(v, out, depth + 1)
                if out.truncated:
                    return
            for a in e.attribs:
                _entity(a, out, depth + 1)
        except Exception:  # noqa: BLE001
            out.skip("INSERT")
    else:
        out.skip(t)


def _plain(text: str) -> str:
    """A dimension's override text without MTEXT formatting ({\\fArial|b0;100} → 100)."""
    try:
        from ezdxf.tools.text import plain_mtext
        return plain_mtext(text)
    except Exception:  # noqa: BLE001
        return text


def _dxf(job: Path) -> dict:
    from ezdxf import recover
    from ezdxf.lldxf.const import DXFError

    try:
        doc, _auditor = recover.readfile(str(job / "input.dxf"))
    except (OSError, DXFError) as e:
        raise SystemExit(f"could not read the DXF ({type(e).__name__}); it may be damaged")
    out = _Collector()
    for e in doc.modelspace():
        _entity(e, out, 0)
        if out.truncated:
            break
    return {"units": int(doc.header.get("$INSUNITS", 0) or 0), "entities": out.entities,
            "texts": out.texts, "skipped": out.skipped, "truncated": out.truncated}


def main(argv: list[str]) -> int:
    _limits()
    job, mode = Path(argv[1]), argv[2]
    nonce = os.environ.get("MISES_NONCE", "")
    modes = {"pdf": _pdf, "image": _image, "dxf": _dxf, "render": _render}
    if mode not in modes:
        raise SystemExit(f"unknown mode {mode!r}")
    result = modes[mode](job)
    tmp = job / "result.json.tmp"
    tmp.write_text(json.dumps({"nonce": nonce, **result}))
    os.replace(tmp, job / "result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
