"""Chart data per study (GET /sims/{id}/charts) for the inline study cards and the Studies
page: plain series, no styling. Each chart: {id, kind: "line"|"bar"|"histogram", title,
x: {label, unit}, y: {label, unit}, series: [{name, points: [[x, y], ...]}], bands?: [...]}."""
from __future__ import annotations

from pathlib import Path

from .fields import FIELDS_FILE, Fields, histogram

MAX_POINTS = 400
WIND_TRANSIENT = 0.2      # the first 20% of lattice steps are the start-up transient


def _thin(pts: list) -> list:
    if len(pts) <= MAX_POINTS:
        return pts
    step = len(pts) / MAX_POINTS
    return [pts[int(i * step)] for i in range(MAX_POINTS)] + [pts[-1]]


def _convergence(rec: dict) -> dict | None:
    hist = ((rec.get("results") or {}).get("convergence") or {}).get("history") or []
    if rec.get("type") == "wind" or len(hist) < 2:
        return None
    metric = {"static": ("peak von Mises", "MPa"), "drop": ("peak von Mises", "MPa"),
              "modal": ("first frequency", "Hz"), "buckling": ("buckling factor", ""),
              "thermal": ("max temperature", "degC")}.get(rec.get("type"), ("metric", ""))
    return {"id": "convergence", "kind": "line", "title": "Mesh convergence",
            "x": {"label": "nodes", "unit": ""}, "y": {"label": metric[0], "unit": metric[1]},
            "series": [{"name": metric[0],
                        "points": [[h["nodes"], h["metric"]] for h in hist]}]}


def _hist(sim_dir: Path, channel: str, title: str, unit: str) -> dict | None:
    from ..cad import safe_io
    try:
        data = safe_io.read_bytes(sim_dir, FIELDS_FILE)   # never follows a link (C6)
        if data is None:
            return None
        f = Fields(data)
    except (OSError, ValueError, KeyError, safe_io.UnsafePath):
        return None
    if channel not in f.ch:
        return None
    h = histogram(f.ch[channel])
    if not h["counts"]:
        return None
    return {"id": f"{channel}_histogram", "kind": "histogram", "title": title,
            "x": {"label": channel.replace("_", " "), "unit": unit},
            "y": {"label": "nodes", "unit": ""}, "edges": h["edges"], "counts": h["counts"]}


def charts(rec: dict, sim_dir: Path) -> dict:
    typ = rec.get("type", "static")
    r = rec.get("results") or {}
    out: list[dict] = []
    if rec.get("status") == "done":
        c = _convergence(rec)
        if c:
            out.append(c)
        if typ in ("static", "drop"):
            h = _hist(sim_dir, "von_mises", "Stress distribution", "MPa")
            if h:
                h["markers"] = [{"label": "allowable", "x": r.get("allowable_mpa")}]
                out.append(h)
            path = r.get("load_path") or []
            if len(path) > 1:  # a nonlinear study: how the part took the load
                out.append({"id": "load_path", "kind": "line", "title": "Load path",
                            "x": {"label": "load fraction", "unit": ""},
                            "y": {"label": "max displacement", "unit": "mm"},
                            "series": [{"name": "max displacement",
                                        "points": _thin([[a, b] for a, b in path])}],
                            "bands": [] if r.get("converged") is not False else [
                                {"label": "no equilibrium past here", "axis": "x",
                                 "from": r.get("load_fraction"), "to": 1.0}]})
        elif typ == "thermal":
            h = _hist(sim_dir, "temperature", "Temperature distribution", "degC")
            if h:
                h["markers"] = [{"label": "HDT", "x": r.get("hdt_c")}]
                out.append(h)
        elif typ == "modal":
            band = r.get("band_hz")
            out.append({"id": "modal_frequencies", "kind": "bar", "title": "Natural frequencies",
                        "x": {"label": "mode", "unit": ""}, "y": {"label": "frequency",
                                                                  "unit": "Hz"},
                        "series": [{"name": "frequency",
                                    "points": [[m["mode"], m["freq_hz"]] for m in r["modes"]],
                                    "flags": [m["in_band"] for m in r["modes"]]}],
                        "bands": [{"label": "excitation band", "axis": "y", "from": band[0],
                                   "to": band[1]}] if band else []})
        elif typ == "buckling":
            out.append({"id": "buckling_factors", "kind": "bar", "title": "Buckling factors",
                        "x": {"label": "mode", "unit": ""}, "y": {"label": "load factor",
                                                                  "unit": ""},
                        "series": [{"name": "factor", "points": [[i + 1, f] for i, f in
                                                                 enumerate(r["factors"])]}],
                        "bands": [{"label": "buckles", "axis": "y", "from": 0, "to": 1}]})
        elif typ == "wind":
            hist = (r.get("convergence") or {}).get("history") or []
            # QA B1-13: the first lattice steps swing Cd to -80, which flattens the converged
            # value to a line at 0; the start-up transient is left out (the average uses the
            # second half anyway)
            if len(hist) > 4:
                cut = WIND_TRANSIENT * hist[-1]["step"]
                hist = [h for h in hist if h["step"] >= cut]
            if hist:
                out.append({"id": "cd_history", "kind": "line", "title": "Drag convergence",
                            "x": {"label": "lattice step", "unit": ""},
                            "y": {"label": "Cd", "unit": ""},
                            "series": [{"name": "Cd", "points": _thin(
                                [[h["step"], h["cd"]] for h in hist])},
                                {"name": "Cd (lattice total)", "points": _thin(
                                    [[h["step"], h["cd_lattice_total"]] for h in hist])}]})
            sweep = [s for s in r.get("yaw_sweep") or [] if "cd" in s]
            if len(sweep) > 1:
                sweep.sort(key=lambda s: s["yaw_deg"])
                out.append({"id": "cd_vs_yaw", "kind": "line", "title": "Cd vs yaw",
                            "x": {"label": "yaw", "unit": "deg"}, "y": {"label": "coefficient",
                                                                        "unit": ""},
                            "series": [{"name": "Cd", "points": [[s["yaw_deg"], s["cd"]]
                                                                 for s in sweep]},
                                       {"name": "Cl", "points": [[s["yaw_deg"], s["cl"]]
                                                                 for s in sweep]}],
                            "table": sweep})
    return {"sim_id": rec.get("sim_id"), "type": typ, "status": rec.get("status"),
            "label": rec.get("label"), "fidelity_class": rec.get("fidelity_class"),
            "charts": out}
