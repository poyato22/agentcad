"""Per-part appearance (Plan 7 U8, R22): a material preset with physically based metalness and
roughness, a color and an opacity, stored on the part's tree row (parts.appearance, JSON).

The viewer renders it (MeshStandardMaterial plus an environment map); thumbnails read it through
`render_params`; agents set it with `part_set_appearance`. `color` on the row and
`appearance.color` are kept equal (projects.patch_part), so code that only knows the color still
shows the right one."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .events import Event
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput

_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")

# name, metalness, roughness, opacity and the tint used when the user has not picked a color
# (None: the part keeps its own color / the theme grey). Same table in app/src/lib/appearance.ts.
PRESETS: dict[str, dict[str, Any]] = {
    "matte_plastic": {"name": "Matte plastic", "metalness": 0.0, "roughness": 0.85,
                      "opacity": 1.0, "color": None},
    "glossy_plastic": {"name": "Glossy plastic", "metalness": 0.0, "roughness": 0.22,
                       "opacity": 1.0, "color": None},
    "brushed_aluminum": {"name": "Brushed aluminum", "metalness": 1.0, "roughness": 0.42,
                         "opacity": 1.0, "color": "#c4c8cc"},
    "polished_metal": {"name": "Polished metal", "metalness": 1.0, "roughness": 0.06,
                       "opacity": 1.0, "color": "#e6e8eb"},
    "anodized": {"name": "Anodized", "metalness": 0.85, "roughness": 0.32,
                 "opacity": 1.0, "color": "#3d6fb0"},
    "rubber": {"name": "Rubber", "metalness": 0.0, "roughness": 0.97,
               "opacity": 1.0, "color": "#2e2f31"},
    "clear": {"name": "Clear", "metalness": 0.0, "roughness": 0.05,
              "opacity": 0.35, "color": "#dde8f0"},
}
# a part with no appearance: the drafting look (matte, opaque)
DEFAULT = {"metalness": 0.0, "roughness": 0.9, "opacity": 1.0}
_KEYS = {"preset", "color", "metalness", "roughness", "opacity"}
MIN_OPACITY = 0.05


def _bad(msg: str) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg,
                      hint=f"presets: {', '.join(PRESETS)}; metalness/roughness 0..1, "
                           f"opacity {MIN_OPACITY}..1, color #rrggbb")


def _unit(raw: dict, key: str, lo: float, fallback: float) -> float:
    v = raw.get(key)
    if v is None:
        return float(fallback)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or not lo <= v <= 1:
        raise _bad(f"appearance {key} must be a number from {lo:g} to 1")
    return float(v)


def normalize(raw: Any) -> dict:
    """A complete appearance {preset, color, metalness, roughness, opacity} from a partial one:
    values left out come from the preset (or the matte default). Raises INVALID_INPUT."""
    if not isinstance(raw, dict):
        raise _bad("appearance must be an object or null")
    extra = set(raw) - _KEYS
    if extra:
        raise _bad(f"unknown appearance field(s): {', '.join(sorted(extra))}")
    preset = raw.get("preset")
    if preset is not None and preset not in PRESETS:
        raise _bad(f"unknown appearance preset {preset!r}")
    base = PRESETS[preset] if preset else {**DEFAULT, "color": None}
    color = raw.get("color", base["color"])
    if color is not None:
        color = str(color).strip().lower()
        if not _HEX_RE.match(color):
            raise _bad("appearance color must be #rrggbb")
    return {"preset": preset, "color": color,
            "metalness": _unit(raw, "metalness", 0.0, base["metalness"]),
            "roughness": _unit(raw, "roughness", 0.0, base["roughness"]),
            "opacity": _unit(raw, "opacity", MIN_OPACITY, base["opacity"])}


def render_params(row: Any, store: Any = None) -> dict:
    """What a renderer needs for a part row (store.Part): {preset, color, metalness, roughness,
    opacity}; color None means the renderer's own default grey. U1's thumbnails call this.
    G4: a part with no appearance renders with its default look (looks.default_look: its
    material, else the build's approved concept CMF when `store` is given), as the app draws it."""
    a = getattr(row, "appearance", None)
    color = getattr(row, "color", None)
    if not a:
        from .looks import concept_cmf, default_look
        if getattr(row, "kind", "part") == "part" and getattr(row, "id", None):
            look = default_look(row.id, getattr(row, "label", None), getattr(row, "material", None),
                                concept_cmf(store, getattr(row, "project_id", None)),
                                getattr(row, "project_id", None))
            if look is not None:
                return {**look, "color": color or look["color"]}
        return {"preset": None, "color": color, **DEFAULT}
    try:
        n = normalize(a)
    except MisesError:
        return {"preset": None, "color": color, **DEFAULT}
    return {**n, "color": n["color"] or color}


class SetAppearanceArgs(BaseModel):
    part_id: str
    preset: str | None = None
    #: the build palette's colour to use (primary, secondary, accent, dark, light)
    role: str | None = None
    color: str | None = None
    #: keep `color` exactly (only when the user asked for that colour); otherwise it is snapped
    #: to the nearest colour of the build's palette
    exact_color: bool = False
    metalness: float | None = None
    roughness: float | None = None
    opacity: float | None = None


@tool("part_set_appearance", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      description="Set how a part looks in the viewer and thumbnails: a material preset "
                  f"({', '.join(PRESETS)}) and/or a colour, metalness and roughness (0..1), "
                  "opacity (0.05..1). Colour comes from the build's palette so the parts go "
                  "together: pass role = primary (bodies, shells), secondary (frames, "
                  "mechanism), accent (a few small highlight parts only), dark (drives, "
                  "electronics) or light. A `color` #rrggbb is snapped to the nearest palette "
                  "colour unless exact_color is true, which is only for a colour the user asked "
                  "for. The result names the palette. Fields left out come from the preset. "
                  "Does not change the geometry or the mass.")
async def part_set_appearance(ctx: ToolContext, args: SetAppearanceArgs) -> ToolOutput:
    from . import projects
    from .looks import ROLES, concept_cmf, nearest_in_palette, palette_for
    raw = {k: v for k, v in args.model_dump().items()
           if k not in ("part_id", "role", "exact_color") and v is not None}
    part = ctx.store.get_part(args.part_id) if hasattr(ctx.store, "get_part") else None
    pid = getattr(part, "project_id", None)
    pal = palette_for(pid, concept_cmf(ctx.store, pid))
    snapped = None
    if args.role is not None:
        if args.role not in ROLES:
            raise _bad(f"role must be one of {', '.join(ROLES)}")
        raw["color"] = pal[args.role]
    elif (args.color is not None and not args.exact_color
          and args.preset in (None, "matte_plastic", "glossy_plastic")   # metals keep theirs
          and _HEX_RE.match(str(args.color).strip().lower())):
        role, c = nearest_in_palette(str(args.color).strip().lower(), pal)
        if c != str(args.color).strip().lower():
            snapped = (args.color, role)
        raw["color"] = c
    row = projects.patch_part(ctx.store, Path(ctx.workdir), args.part_id, appearance=raw)
    a = row["appearance"]
    name = PRESETS[a["preset"]]["name"] if a["preset"] else "custom material"
    summary = f"{args.part_id}: appearance {name}" + (f" {a['color']}" if a["color"] else "")
    if snapped:
        summary += f" (your {snapped[0]} snapped to the palette's {snapped[1]})"
    summary += (f" · build palette {pal['name']}: "
                + ", ".join(f"{k} {pal[k]}" for k in ROLES))
    events = getattr(ctx, "events", None)
    if events is not None:
        # the app refetches the part list on part_changed; the version is unchanged (no rebuild)
        events.publish(Event(type="part_changed", run_id=ctx.run_id, data={
            "part_id": args.part_id, "version": row.get("version") or 1, "by": "agent",
            "run_id": ctx.run_id, "summary": summary}))
    return ToolOutput(summary=summary, data={"part_id": args.part_id, "appearance": a, "palette": pal},
                      step_kind=StepKind.DIFF)


def register_appearance_tools(registry: ToolRegistry) -> None:
    registry.register(part_set_appearance)
