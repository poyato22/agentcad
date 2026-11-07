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


#: kept for older callers and tests: every colour any palette can give a plastic part
PART_PALETTE: list[str] = []

#: Curated build palettes: a main body colour, a second structural tone, one accent used
#: sparingly, a dark for frames / electronics, and a light. One palette per build (its concept's
#: colours when it has an approved concept), so every part of a build belongs together.
PALETTES: list[dict[str, str]] = [
    {"name": "Graphite and signal orange", "primary": "#d4d6d9", "secondary": "#4b5058",
     "accent": "#e8642c", "dark": "#202327", "light": "#f0f0ee"},
    {"name": "Workshop teal", "primary": "#2f6f73", "secondary": "#d8d4c8", "accent": "#e3a33b",
     "dark": "#23272b", "light": "#eceae4"},
    {"name": "Navy and sand", "primary": "#2a4463", "secondary": "#d6cbb4", "accent": "#d9583b",
     "dark": "#1d2229", "light": "#efece6"},
    {"name": "Field green", "primary": "#55715a", "secondary": "#cbc5b2", "accent": "#e0b23c",
     "dark": "#22261f", "light": "#eeece4"},
    {"name": "Arctic blue", "primary": "#e6e9ec", "secondary": "#7d8996", "accent": "#2f7de1",
     "dark": "#25292e", "light": "#f5f6f7"},
    {"name": "Racing red", "primary": "#b8352e", "secondary": "#dedcd7", "accent": "#f0b429",
     "dark": "#1e1f22", "light": "#f2f1ee"},
]
ROLES = ("primary", "secondary", "accent", "dark", "light")
PART_PALETTE[:] = sorted({c for pal in PALETTES for k, c in pal.items() if k in ROLES})

_ACCENT_NAME = re.compile(r"button|knob|cap\b|tip|fin\b|fins|trim|handle|grip|bumper|led\b|"
                          r"light|logo|badge|foot|feet|pad\b|pads|nozzle|eye|trigger|switch", re.I)
_SECOND_NAME = re.compile(r"frame|chassis|base|bracket|mount|plate|arm|link|leg|strut|rib|"
                          r"spar|beam|rail|truss|joint|hinge|gear|pulley|hub|wheel|ring", re.I)
_DARK_NAME = re.compile(r"motor|servo|battery|lipo|board|pcb|esc\b|controller|camera|sensor|"
                        r"cable|wire|tire|tyre|track|belt", re.I)


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
        # no material look and no concept colour: the build's palette, by the part's role, so
        # the parts of one build go together (it was a hashed colour per part: random-looking)
        pal = palette_for(project_id, cmf)
        look = {"preset": "matte_plastic", "color": pal[part_role(part_id, label)]}
    try:
        return _appearance.normalize(look)
    except Exception:  # noqa: BLE001
        return None
