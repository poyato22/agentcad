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


#: kept for older callers and tests: every colour any palette can give a plastic part
PART_PALETTE: list[str] = []

#: A build's filament set: the spools its printed parts come off, so they go together and look
#: like real prints. Each colour is a common real filament shade (PLA / PETG / ASA catalogues):
#: a main colour, a second tone, one accent used sparingly, a dark and a light.
PALETTES: list[dict[str, str]] = [
    {"name": "Grey and signal orange filaments", "primary": "#cfd1d3", "secondary": "#4d5157",
     "accent": "#f2692a", "dark": "#1e1f22", "light": "#f1f0ec"},
    {"name": "Teal and sand filaments", "primary": "#1f7a7a", "secondary": "#d8d1bf", "accent": "#f2b632",
     "dark": "#232528", "light": "#eeebe3"},
    {"name": "Navy and white filaments", "primary": "#1f3a5f", "secondary": "#e6e6e2", "accent": "#e2462f",
     "dark": "#1c1e22", "light": "#f3f2ee"},
    {"name": "Olive and bone filaments", "primary": "#5b6b45", "secondary": "#d9d2bd", "accent": "#e8a33a",
     "dark": "#212320", "light": "#efece3"},
    {"name": "White and azure filaments", "primary": "#ececea", "secondary": "#8a9199", "accent": "#1f7ae0",
     "dark": "#212327", "light": "#f7f7f5"},
    {"name": "Red and silver filaments", "primary": "#c0302a", "secondary": "#b9bcbf", "accent": "#f4c430",
     "dark": "#1d1e21", "light": "#f2f1ee"},
]
ROLES = ("primary", "secondary", "accent", "dark", "light")
PART_PALETTE[:] = sorted({c for pal in PALETTES for k, c in pal.items() if k in ROLES})

_ACCENT_NAME = re.compile(r"button|knob|cap\b|tip|fin\b|fins|trim|handle|grip|bumper|led\b|"
                          r"light|logo|badge|foot|feet|pad\b|pads|nozzle|eye|trigger|switch", re.I)
_SECOND_NAME = re.compile(r"frame|chassis|base|bracket|mount|plate|arm|link|leg|strut|rib|"
                          r"spar|beam|rail|truss|joint|hinge|gear|pulley|hub|wheel|ring", re.I)
_DARK_NAME = re.compile(r"motor|servo|battery|lipo|board|pcb|esc\b|controller|camera|sensor|"
                        r"cable|wire|tire|tyre|track|belt", re.I)


#: Bought parts look like themselves (name -> look). First match wins, so specific names first.
COMPONENT_LOOKS: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"sg90|micro[ _-]?servo|9g[ _-]?servo", re.I),
     {"preset": "glossy_plastic", "color": "#2f5fb8", "opacity": 0.85}),      # translucent blue case
    (re.compile(r"servo", re.I), {"preset": "matte_plastic", "color": "#1b1c1f", "roughness": 0.6}),
    (re.compile(r"bldc|brushless|outrunner|motor[ _-]?bell", re.I),
     {"preset": "anodized", "color": "#a9adb3"}),
    (re.compile(r"stepper|nema", re.I), {"preset": "matte_plastic", "color": "#2a2c30", "roughness": 0.5,
                                         "metalness": 0.4}),
    (re.compile(r"\bmotor|gearmotor|n20", re.I), {"preset": "brushed_aluminum", "color": "#b8bcc1"}),
    (re.compile(r"bearing", re.I), {"preset": "polished_metal", "color": "#c9ccd0"}),
    (re.compile(r"screw|bolt|\bnut\b|nuts|washer|standoff|insert|rivet|\bpin\b|dowel", re.I),
     {"preset": "brushed_aluminum", "color": "#9ea2a7"}),
    (re.compile(r"spring", re.I), {"preset": "polished_metal", "color": "#b4b8bd"}),
    (re.compile(r"magnet", re.I), {"preset": "polished_metal", "color": "#c7c9cc"}),
    (re.compile(r"lipo|battery|cell[ _-]?pack|18650", re.I),
     {"preset": "glossy_plastic", "color": "#2b3a55"}),                          # shrink-wrapped pack
    (re.compile(r"pcb|board|esc\b|controller|receiver|driver|arduino|esp32|raspberry|flight[ _-]?ctrl", re.I),
     {"preset": "matte_plastic", "color": "#1d5a36", "roughness": 0.5}),        # green solder mask
    (re.compile(r"camera", re.I), {"preset": "matte_plastic", "color": "#151618"}),
    (re.compile(r"prop(eller)?\b|props\b", re.I), {"preset": "matte_plastic", "color": "#1c1d20"}),
    (re.compile(r"tire|tyre|belt|o[ _-]?ring|grommet|bumper[ _-]?pad", re.I), {"preset": "rubber"}),
    (re.compile(r"\bled\b|lens|window|canopy|visor", re.I), {"preset": "clear"}),
    (re.compile(r"cable|wire|harness", re.I), {"preset": "matte_plastic", "color": "#1a1a1c", "roughness": 0.7}),
]

#: printed plastics: their finish (the colour is the filament's, from the build's set)
_PLASTIC_FINISH: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"cf\b|-cf|carbon", re.I), {"preset": "matte_plastic", "roughness": 0.75}),   # matte, dark
    (re.compile(r"petg|pctg|\bpc\b|polycarbonate", re.I), {"preset": "glossy_plastic", "roughness": 0.3}),
    (re.compile(r"asa|abs", re.I), {"preset": "matte_plastic", "roughness": 0.55}),
    (re.compile(r"pa ?12|pa ?11|sls|mjf", re.I), {"preset": "matte_plastic", "roughness": 0.9}),
    (re.compile(r"pa ?6|nylon|pom|delrin|acetal", re.I), {"preset": "matte_plastic", "roughness": 0.6}),
    (re.compile(r"resin|sla", re.I), {"preset": "glossy_plastic", "roughness": 0.35}),
    (re.compile(r"pla", re.I), {"preset": "matte_plastic", "roughness": 0.65}),
]


#: a printed part made to carry a bought one ("servo mount", "battery tray") is not that part
_HOLDER = re.compile(r"mount|bracket|holder|housing|case\b|cover|clip|cradle|plate|tray|bay\b|"
                     r"seat|pocket|clamp|adapter|horn|arm|boss|cap\b|guard|shroud|enclosure", re.I)


_CARRIER = re.compile(r"mount|bracket|holder|cradle|plate|tray|clamp|adapter", re.I)


def component_look(part_id: str, label: str | None) -> dict | None:
    """How a bought part looks, from its name (a servo, a bearing, a screw, a LiPo, a board).
    None for a printed part that holds one (a servo mount, a battery tray)."""
    name = f"{part_id} {label or ''}".replace("_", " ")
    if _HOLDER.search(name):
        return None
    for pat, look in COMPONENT_LOOKS:
        if pat.search(name):
            return dict(look)
    return None


def printed_look(material: str | None, colour: str) -> dict:
    """A printed part: the filament colour with its plastic's real finish. Carbon-filled
    filaments (PA-CF, PETG-CF) only come in near-black, and SLS/MJF nylon prints grey."""
    m = material or ""
    for pat, fin in _PLASTIC_FINISH:
        if pat.search(m):
            if pat.pattern.startswith("cf"):
                return {**fin, "color": "#2a2b2e"}
            if "sls" in pat.pattern and re.search(r"pa ?12|pa ?11|sls|mjf", m, re.I):
                return {**fin, "color": "#7f8286"}
            return {**fin, "color": colour}
    return {"preset": "matte_plastic", "roughness": 0.65, "color": colour}


def palette_for(project_id: str | None, cmf: list[dict] | None = None) -> dict[str, str]:
    """The build's palette: from its approved concept's colours when it has them (the body
    colour is the primary, a second distinct colour the accent), else one of PALETTES chosen
    once per build (stable: the same build always gets the same one)."""
    base = PALETTES[zlib.crc32((project_id or "").encode()) % len(PALETTES)]
    if not cmf:
        return dict(base)
    body = next((e["color"].lower() for e in cmf if _role(e) == "body"), None)
    if body is None:
        return dict(base)
    others = [e["color"].lower() for e in cmf if e["color"].lower() != body and _role(e) == "body"]
    return {**base, "name": "From the concept", "primary": body,
            "accent": others[0] if others else base["accent"]}


def part_role(part_id: str, label: str | None = None) -> str:
    """Which palette colour a part takes by default, from its name: small highlight parts the
    accent, frames and mechanism the secondary tone, electronics and drives the dark, the rest
    (shells, bodies, covers) the primary."""
    name = f"{part_id} {label or ''}".replace("_", " ")
    if _CARRIER.search(name) and not _ACCENT_NAME.search(name):
        return "secondary"                 # what carries a motor or a board: structure
    if _DARK_NAME.search(name):
        return "dark"
    if _ACCENT_NAME.search(name):
        return "accent"
    if _SECOND_NAME.search(name):
        return "secondary"
    return "primary"


def _hex_rgb(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def nearest_in_palette(color: str, palette: dict[str, str]) -> tuple[str, str]:
    """(role, colour) of the palette colour closest to `color` (a weighted RGB distance that
    follows how eyes weigh red, green and blue)."""
    r, g, b = _hex_rgb(color)
    def dist(c: str) -> float:
        r2, g2, b2 = _hex_rgb(c)
        rm = (r + r2) / 2
        return (2 + rm / 256) * (r - r2) ** 2 + 4 * (g - g2) ** 2 + (2 + (255 - rm) / 256) * (b - b2) ** 2
    role = min(ROLES, key=lambda k: dist(palette[k]))
    return role, palette[role]


def default_look(part_id: str, label: str | None, material: str | None,
                 cmf: list[dict], project_id: str | None = None) -> dict | None:
    """The normalised appearance a part shows by default (see the module doc), or None."""
    name = f"{part_id} {label or ''}"
    roles = {}
    for e in cmf:
        roles.setdefault(_role(e), e)
    # what it will really look like: a bought part as itself, a metal / carbon / rubber part as
    # its material, else a print in the build's filaments (by the part's role), with its
    # plastic's finish; the approved concept's colours are the filament set when it has them
    look = material_look(material) or component_look(part_id, label)
    if look is None:
        if _FILM_NAME.search(name) and "film" in roles:
            look = _cmf_look(roles["film"])
        elif _ROD_NAME.search(name) and "rod" in roles:
            look = _cmf_look(roles["rod"])
        elif "body" in roles and part_role(part_id, label) not in ("accent", "dark"):
            look = _cmf_look(roles["body"])        # the approved concept's filament
    if look is None:
        pal = palette_for(project_id, cmf)
        look = printed_look(material, pal[part_role(part_id, label)])
    try:
        return _appearance.normalize(look)
    except Exception:  # noqa: BLE001
        return None
