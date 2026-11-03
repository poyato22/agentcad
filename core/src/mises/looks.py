"""G4: the look a part shows when nobody gave it one (no appearance stored).

The user's ornicopter came out all grey: its agent set materials (PA12 gears, PETG wing roots,
carbon rods, an aluminium motor, a brass pinion) and the approved concept said "Teal PLA" and
"Translucent amber film", but only `part_set_appearance` gives a part a look, and it was never
called. Now a part with no appearance of its own shows, in order:

- its material's look when the material has one (metals, carbon fibre, rubber);
- else the approved concept's colour/material/finish (CMF) entry for its role: a film / wing
  membrane, a rod / tube / spar, or the body (the printed parts);
- else bought electronics read as what they are (a battery pack, a board);
- else nothing (the drafting grey).

It is a default, not a choice: rows carry it as `default_appearance` (GET /parts); `appearance`
stays what the user or an agent set, and setting one replaces the default everywhere.
"""
from __future__ import annotations

import re
import zlib

from . import appearance as _appearance

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_METAL = re.compile(r"\b(al|alu|aluminum|aluminium|steel|stainless|brass|copper|bronze|"
                    r"titanium|ti-6al-4v|iron|zinc)\b", re.I)
_CARBON = re.compile(r"\b(cf|carbon)\b|^cf\b|-cf\b", re.I)
_RUBBER = re.compile(r"\b(tpu|tpe|rubber|silicone|neoprene)\b", re.I)
_FILM_NAME = re.compile(r"film|membrane|sail|ripstop|fabric|canopy", re.I)
_ROD_NAME = re.compile(r"rod|spar|tube|boom|shaft|axle|dowel", re.I)
_FILM_CMF = re.compile(r"film|ripstop|membrane|fabric|mylar|foil|sail", re.I)
_ROD_CMF = re.compile(r"carbon|rod|tube|spar", re.I)
_CLEAR = re.compile(r"clear|translucent|transparent|tinted", re.I)
_BATTERY = re.compile(r"battery|lipo|li-?ion|cell_pack|\bcells?\b", re.I)
_BOARD = re.compile(r"board|pcb|receiver|esc\b|controller|flight_ctrl|driver", re.I)


def _metal_look(material: str) -> dict:
    m = material.lower()
    if "brass" in m or "bronze" in m:
        return {"preset": "polished_metal", "color": "#c9a24a"}
    if "copper" in m:
        return {"preset": "polished_metal", "color": "#c27a4e"}
    if "steel" in m or "stainless" in m or "iron" in m:
        return {"preset": "brushed_aluminum", "color": "#a9adb2"}
    if "titanium" in m:
        return {"preset": "brushed_aluminum", "color": "#9a9a94"}
    return {"preset": "brushed_aluminum"}


def material_look(material: str | None) -> dict | None:
    """The look a material has by itself (metal, carbon fibre, rubber); None for plastics,
    whose colour is the designer's choice."""
    if not material:
        return None
    if _CARBON.search(material):
        return {"preset": "matte_plastic", "color": "#1e2024", "roughness": 0.55}
    if _RUBBER.search(material):
        return {"preset": "rubber"}
    if _METAL.search(material):
        return _metal_look(material)
    return None


def _cmf_look(entry: dict) -> dict:
    finish = str(entry.get("finish") or "").lower()
    preset = "glossy_plastic" if finish in ("gloss", "glossy") else "matte_plastic"
    look = {"preset": preset, "color": entry["color"].lower()}
    if finish == "satin":
        look["roughness"] = 0.5
    if _CLEAR.search(str(entry.get("name") or "")):
        look["opacity"] = 0.6
    return look


def _role(entry: dict) -> str:
    name = str(entry.get("name") or "")
    if _FILM_CMF.search(name):
        return "film"
    if _ROD_CMF.search(name):
        return "rod"
    if _METAL.search(name):
        return "metal"
    return "body"


def concept_cmf(store, project_id: str | None) -> list[dict]:
    """The approved (or skipped-to) concept's chosen option's CMF entries: [{name, color,
    finish}], colours checked. [] when the build has no chosen concept. Never raises."""
    if not project_id or store is None:
        return []
    try:
        from .concept import ConceptStore
        cs = ConceptStore(store)
        rec = cs.get(project_id)
        if rec.get("status") not in ("approved", "skipped") or not rec.get("pitch_ids"):
            return []
        pitch = cs.get_pitch(rec["pitch_ids"][-1])
        chosen = pitch.get("chosen") or {}
        names = [chosen.get("option"), *((m or {}).get("option") for m in chosen.get("mix") or [])]
        label = rec.get("chosen_option")
        options = pitch.get("options") or []
        opt = next((o for n in names if n for o in options if o.get("name") == n), None) \
            or next((o for o in options if label and o.get("name") == label), None)
        out = []
        for e in (opt or {}).get("cmf") or []:
            if isinstance(e, dict) and isinstance(e.get("color"), str) and _HEX.match(e["color"]):
                out.append({"name": str(e.get("name") or "")[:60], "color": e["color"],
                            "finish": str(e.get("finish") or "")[:16]})
        return out
    except Exception:  # noqa: BLE001 - a default look is never a reason to fail a listing
        return []


#: muted part colours (the render palette's hues), picked per part id when nothing else applies
PART_PALETTE = ["#9ea8b8", "#cc8c6b", "#78a38a", "#8c91bd", "#c7b873", "#99809e", "#73a8b3",
                "#bd949e"]


def default_look(part_id: str, label: str | None, material: str | None,
                 cmf: list[dict]) -> dict | None:
    """The normalised appearance a part shows by default (see the module doc), or None."""
    name = f"{part_id} {label or ''}"
    roles = {}
    for e in cmf:
        roles.setdefault(_role(e), e)
    look = material_look(material)
    if look is None:
        if _FILM_NAME.search(name) and "film" in roles:
            look = _cmf_look(roles["film"])
        elif _ROD_NAME.search(name) and "rod" in roles:
            look = _cmf_look(roles["rod"])
        elif _BATTERY.search(name):
            look = {"preset": "matte_plastic", "color": "#2c3e5c"}
        elif _BOARD.search(name):
            look = {"preset": "matte_plastic", "color": "#1f5c3a"}
        elif "body" in roles:
            look = _cmf_look(roles["body"])
    if look is None:
        # no material look and no concept colour: a stable muted colour per part, so the app,
        # covers and the agent's renders all show the same distinct colours (never default grey)
        look = {"preset": "matte_plastic",
                "color": PART_PALETTE[zlib.crc32(part_id.encode()) % len(PART_PALETTE)]}
    try:
        return _appearance.normalize(look)
    except Exception:  # noqa: BLE001
        return None
