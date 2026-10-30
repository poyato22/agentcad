"""Parses an untrusted drawing file in a sandboxed child: python -I parse_worker.py <job_dir> pdf|dxf

The parent (mises.imports.drawing_import) runs this under sandbox-exec (writes only under
job_dir, no network, no new processes) with a timeout. It reads job_dir/input.pdf or
job_dir/input.dxf and writes job_dir/result.json carrying $MISES_NONCE:

- pdf: the first MAX_PAGES pages rasterized at DPI (fewer pixels when a page would pass
  MAX_PIXELS) as page-<n>.png, plus the page count.
- dxf: the modelspace geometry as plain numbers (lines, circles, arcs, polylines with bulges,
  splines and ellipses flattened to polylines, block inserts expanded), DIMENSION entities with
  their measurement and definition points, and TEXT/MTEXT strings (cut to MAX_TEXT).

Nothing here imports mises: the child only needs pypdfium2 / ezdxf / Pillow. The parent
validates everything in result.json before using it.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

DPI = 200
MAX_PAGES = 6
MAX_PIXELS = 40_000_000
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


def _pdf(job: Path) -> dict:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(job / "input.pdf"))
    except pdfium.PdfiumError as e:
        raise SystemExit(f"could not open the PDF ({e}); it may be damaged or password-protected")
    total = len(pdf)
    pages = []
    for i in range(min(total, MAX_PAGES)):
        page = pdf[i]
        w_pt, h_pt = page.get_size()
        if not (w_pt > 0 and h_pt > 0 and math.isfinite(w_pt * h_pt)):
            raise SystemExit(f"page {i + 1} has no size")
        scale = DPI / 72
        if w_pt * h_pt * scale * scale > MAX_PIXELS:
            scale = math.sqrt(MAX_PIXELS / (w_pt * h_pt))
        img = page.render(scale=scale).to_pil()
        name = f"page-{i + 1}.png"
        img.save(job / name, "PNG")
        pages.append({"file": name, "width": img.width, "height": img.height,
                      "dpi": round(scale * 72, 1)})
        page.close()
    pdf.close()
    return {"pages_total": total, "pages": pages}


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
        pts = {}
        for k in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "text_midpoint"):
            v = e.dxf.get(k)
            if v is not None:
                pts[k] = _xy(v)
        out.add({"t": "dim", "layer": layer, "dimtype": int(e.dimtype),
                 "measurement": measurement, "angle": float(e.dxf.get("angle", 0.0)),
                 "text": str(e.dxf.get("text", ""))[:MAX_TEXT], "pts": pts})
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
    result = _pdf(job) if mode == "pdf" else _dxf(job) if mode == "dxf" else None
    if result is None:
        raise SystemExit(f"unknown mode {mode!r}")
    tmp = job / "result.json.tmp"
    tmp.write_text(json.dumps({"nonce": nonce, **result}))
    os.replace(tmp, job / "result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
