"""Estimate how shape-heavy a request is, so routing can prefer a strong vision-capable model
and the UI can warn when a small or non-vision model is driving complex shape work (G-R6).

This is a cheap keyword heuristic over the user's own prompt (trusted text). It never runs tool
output. The estimate rides on the run record as `shape_complexity`.
"""
from __future__ import annotations

import re

# weight -> the terms that, when present, push a request toward "shape-heavy".
_SIGNALS: dict[str, float] = {}


def _add(weight: float, *terms: str) -> None:
    for t in terms:
        _SIGNALS[t] = max(_SIGNALS.get(t, 0.0), weight)


# organic / aero bodies and freeform surfaces: the cases that fall apart as boxes today
_add(0.7, "aircraft", "airplane", "aeroplane", "airfoil", "aerofoil", "wing", "fuselage",
     "bomber", "fighter jet", "drone", "quadcopter", "propeller", "turbine blade", "impeller",
     "boat hull", "ship hull", "car body", "fairing", "cockpit", "nacelle",
     "blended wing", "flying wing")
_add(0.6, "organic", "freeform", "sculpt", "sculpted", "ergonomic", "streamlined",
     "aerodynamic", "nurbs", "spline surface", "lofted", "loft", "swept surface",
     "blended", "teardrop", "biomimetic", "contoured")
# enclosures with internals and multi-part systems
_add(0.5, "enclosure", "internals", "internal components", "pack the", "cavity",
     "assembly", "mechanism", "linkage", "gearbox", "housing with", "vented case")
_add(0.35, "curved", "curve", "sweep", "revolve", "shell", "fillet", "chamfer", "rounded",
     "dome", "turbine", "blade", "helix", "spiral", "case", "housing", "lid", "cowling")
# explicitly simple prismatic parts pull the score down
_add(0.0, "box", "plate", "block", "cube", "cylinder", "washer", "spacer", "standoff",
     "bracket", "flat")

_SIMPLE = {"box", "plate", "block", "cube", "cylinder", "washer", "spacer", "flat"}

HIGH = 0.6
MEDIUM = 0.3


def estimate(prompt: str) -> dict:
    """Return {score, level, signals, vision_recommended} for `prompt`.

    score is 0..1, saturating as more/heavier signals appear. level is "low"|"medium"|"high".
    vision_recommended is True when a vision-capable model is worth preferring."""
    text = (prompt or "").lower()
    matched: list[str] = []
    weight = 0.0
    for term, w in _SIGNALS.items():
        if w <= 0:
            continue
        if re.search(r"\b" + re.escape(term) + r"\b", text):
            matched.append(term)
            weight += w
    # saturating combine so a long prompt with many weak hits cannot run away
    score = 1.0 - _prod(1.0 - min(w, 0.95) for w in (_SIGNALS[t] for t in matched))
    simple_only = bool(matched) and all(t in _SIMPLE for t in matched)
    if simple_only or not matched:
        score = min(score, 0.2)
    score = round(float(score), 2)
    level = "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"
    return {"score": score, "level": level, "signals": sorted(set(matched)),
            "vision_recommended": level != "low"}


def _prod(xs) -> float:
    out = 1.0
    for x in xs:
        out *= x
    return out
