"""Catalog items as local parts: a parametric build123d script per item, built in the sandbox.

Fasteners and bearings use bd_warehouse (https://github.com/gumyr/bd_warehouse, Apache-2.0)
when it imports and has the size; otherwise, and for everything else, the part is a simple
envelope: the space the real part takes, with its mounting holes. The script prints which one
it built (`MISES_GEOMETRY=...`), so the part records what it really is.

The part's mass is the catalog mass when the item has one (mass_source "catalog"; an
envelope's volume says nothing about a motor's mass). Otherwise it is an estimate (mass_source
"estimate"): volume x material density x a stated fill factor, the factors written to
metrics.mass_note. `part.json` in the part directory records {kind: "catalog", project_id,
label, catalog_item_id, geometry, mass_g, mass_source, density_g_cm3, fill_factor, ...}; the
effective density reproduces the mass when the part is regenerated.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path

from ..cad.runner import run_build
from .. import edits as _edits
from ..cad import safe_io
from ..cad.tools import PART_ID_RE, _after_build, _lock
from ..naming import check_name
from ..types import ErrorCode, MisesError

STEEL_G_CM3 = 7.85
DEFAULT_G_CM3 = 1.24          # the mirror default (PLA)
_DENSITY = {"fastener": STEEL_G_CM3, "bearing": STEEL_G_CM3}
# A ball bearing is not a solid ring: races, balls and cage fill about 70% of the d x D x B
# envelope (SKF 6204: 110 g real vs 156 g as a solid steel ring = 0.70). Used only when an
# envelope bearing has no catalog mass.
_FILL = {"bearing": 0.7}
META = "part.json"

_HEADER = '''# AgentCAD catalog part from item {item_id}
# {geometry_note}
# Edit VARS to resize; the envelope keeps its mounting holes.
'''

_BD = {  # kind -> (import line, constructor expression)
    "screw": ("from bd_warehouse.fastener import SocketHeadCapScrew",
              'SocketHeadCapScrew(SIZE, v["length"], "iso4762", simple=True)'),
    "nut": ("from bd_warehouse.fastener import HexNut", 'HexNut(SIZE, "iso4032", simple=True)'),
    "washer": ("from bd_warehouse.fastener import PlainWasher", 'PlainWasher(SIZE, "iso7089")'),
    "bearing": ("from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing",
                'SingleRowDeepGrooveBallBearing(SIZE, "SKT")'),
}

# envelope bodies: `build123d` names are pre-imported in the worker. Holes are explicit
# subtractive cylinders (placed, not drilled from a workplane).
_ENVELOPE = {
    "screw": '''    with BuildPart() as p:
        Cylinder(v["head_d"] / 2, v["head_h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, -v["length"])):
            Cylinder(v["d"] / 2, v["length"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return p.part''',
    "nut": '''    with BuildPart() as p:
        with BuildSketch():
            RegularPolygon(v["s"] / math.sqrt(3), 6)
        extrude(amount=v["m"])
        Cylinder(v["d"] / 2, v["m"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                 mode=Mode.SUBTRACT)
    return p.part''',
    "washer": '''    with BuildPart() as p:
        Cylinder(v["outer_d"] / 2, v["h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(v["inner_d"] / 2, v["h"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                 mode=Mode.SUBTRACT)
    return p.part''',
    "bearing": '''    with BuildPart() as p:
        Cylinder(v["D"] / 2, v["B"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(v["d"] / 2, v["B"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                 mode=Mode.SUBTRACT)
    return p.part''',
    "motor": '''    with BuildPart() as p:
        Cylinder(v["bell_d"] / 2, v["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, v["height"])):
            Cylinder(v["shaft_d"] / 2, v["shaft_len"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        # mount holes in the base: each pair opposite on a circle of the pattern's diameter
        r_a, r_b = v["pattern_a"] / 2, v["pattern_b"] / 2
        c = math.cos(math.pi / 4)
        holes = [(r_a * c, r_a * c), (-r_a * c, -r_a * c), (-r_b * c, r_b * c), (r_b * c, -r_b * c)]
        with Locations(*holes):
            Cylinder(v["hole_d"] / 2, min(4.0, v["height"] / 2),
                     align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    return p.part''',
    "prop": '''    with BuildPart() as p:
        # the swept disc: the space the spinning prop needs
        Cylinder(v["diameter"] / 2, v["hub_h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(v["bore"] / 2, v["hub_h"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                 mode=Mode.SUBTRACT)
    return p.part''',
    "box": '''    with BuildPart() as p:
        Box(v["l"], v["w"], v["h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return p.part''',
    "cylinder": '''    with BuildPart() as p:
        Cylinder(v["d"] / 2, v["h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        if v["bore"] > 0:
            Cylinder(v["bore"] / 2, v["h"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                     mode=Mode.SUBTRACT)
    return p.part''',
    "board": '''    with BuildPart() as p:
        Box(v["l"], v["w"], v["t"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        if HOLES:
            with Locations(*HOLES):
                Cylinder(v["hole_d"] / 2, v["t"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                         mode=Mode.SUBTRACT)
    return p.part''',
    "servo": '''    with BuildPart() as p:
        Box(v["l"], v["w"], v["h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, v["tab_z"])):
            Box(v["tab_l"], v["w"], v["tab_t"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((-v["spacing"] / 2, 0, v["tab_z"]), (v["spacing"] / 2, 0, v["tab_z"])):
            Cylinder(v["hole_d"] / 2, v["tab_t"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                     mode=Mode.SUBTRACT)
    return p.part''',
    "stepper": '''    with BuildPart() as p:
        Box(v["face"], v["face"], v["length"], align=(Align.CENTER, Align.CENTER, Align.MAX))
        Cylinder(v["pilot_d"] / 2, v["pilot_h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(v["shaft_d"] / 2, v["shaft_len"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        a = v["pattern"] / 2
        with Locations((a, a, 0), (-a, a, 0), (a, -a, 0), (-a, -a, 0)):
            Cylinder(v["hole_d"] / 2, 4.5, align=(Align.CENTER, Align.CENTER, Align.MAX),
                     mode=Mode.SUBTRACT)
    return p.part''',
    "extrusion": '''    with BuildPart() as p:
        Box(v["w"], v["h"], v["length"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(*BORES):
            Cylinder(v["bore"] / 2, v["length"], align=(Align.CENTER, Align.CENTER, Align.MIN),
                     mode=Mode.SUBTRACT)
    return p.part''',
}

_VAR_KEYS = {
    "screw": ("length", "d", "head_d", "head_h"), "nut": ("s", "m", "d"),
    "washer": ("inner_d", "outer_d", "h"), "bearing": ("d", "D", "B"),
    "motor": ("bell_d", "height", "shaft_d", "shaft_len", "pattern_a", "pattern_b", "hole_d"),
    "prop": ("diameter", "hub_h", "bore"), "box": ("l", "w", "h"),
    "cylinder": ("d", "h", "bore"), "board": ("l", "w", "t", "hole_d"),
    "servo": ("l", "w", "h", "tab_l", "tab_t", "tab_z", "spacing", "hole_d"),
    "stepper": ("face", "length", "pilot_d", "pilot_h", "shaft_d", "shaft_len", "pattern",
                "hole_d"),
    "extrusion": ("w", "h", "length", "bore"),
}


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def shape_of(item: dict) -> dict:
    """The geometry recipe: built-ins carry one; URL items get a box or cylinder from dims."""
    shape = item.get("shape")
    if isinstance(shape, dict) and shape.get("kind") in _ENVELOPE:
        shape = dict(shape)
        if shape["kind"] == "motor":
            shape["pattern_a"], shape["pattern_b"] = shape.pop("pattern")
        return shape
    d = item.get("dims") or {}
    num = {k: float(v) for k, v in d.items()
           if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
    if all(k in num for k in ("length_mm", "width_mm", "height_mm")):
        return {"kind": "box", "l": num["length_mm"], "w": num["width_mm"],
                "h": num["height_mm"]}
    dia = num.get("od_mm") or num.get("diameter_mm")
    if dia:
        h = num.get("height_mm") or num.get("width_mm") or num.get("thickness_mm") \
            or num.get("length_mm")
        if h:
            return {"kind": "cylinder", "d": dia, "h": h, "bore": num.get("bore_mm", 0.0)}
    raise _bad("this item has no size to build an envelope from",
               "add length_mm, width_mm and height_mm (or od_mm/diameter_mm and height_mm) to"
               " its dims")


def script_for(item: dict) -> tuple[str, dict[str, float]]:
    """(part.py source, VARS) for an item. Only numbers from the item reach the script, as
    VARS or numeric constants: no item text is ever written into the source."""
    shape = shape_of(item)
    kind = shape["kind"]
    vars_ = {k: float(shape[k]) for k in _VAR_KEYS[kind]}
    for k, x in vars_.items():
        if not math.isfinite(x) or x < 0 or x > 10_000:
            raise _bad(f"{k}={x} is not a usable size in mm")
    item_id = item.get("id", "")
    lines = [_HEADER.format(item_id=item_id if re.fullmatch(r"[a-z0-9_]{1,64}", item_id) else "?",
                            geometry_note="bd_warehouse geometry, envelope fallback"
                            if kind in _BD and shape.get("size") else "parametric envelope")]
    size = shape.get("size")
    if kind in _BD and size:
        if not re.fullmatch(r"M\d+(\.\d+)?(-[\d.]+){0,2}", size):
            raise _bad(f"bad size {size!r}")
        lines.append(f"SIZE = {size!r}")
    if kind == "board":
        holes = [(float(x), float(y)) for x, y in shape.get("holes") or []]
        lines.append(f"HOLES = {holes!r}")
    if kind == "extrusion":
        bores = [(float(x), float(y)) for x, y in shape.get("bores") or [(0.0, 0.0)]]
        lines.append(f"BORES = {bores!r}")
    lines.append(f"VARS = {json.dumps(vars_)}")
    lines.append("")
    lines.append("")
    lines.append("def build(v: dict):")
    if kind in _BD and size:
        imp, ctor = _BD[kind]
        lines.append("    try:")
        lines.append(f"        {imp}")
        lines.append(f"        part = {ctor}")
        lines.append('        print("MISES_GEOMETRY=builtin")')
        lines.append("        return part")
        lines.append("    except Exception:  # bd_warehouse missing or size unknown")
        lines.append("        pass")
    lines.append('    print("MISES_GEOMETRY=envelope")')
    lines.append(_ENVELOPE[kind])
    return "\n".join(lines) + "\n", vars_


# ---- part ids

def _letters(seed: str, n: int) -> str:
    h = hashlib.sha256(seed.encode()).digest()
    return "".join(chr(ord("a") + b % 26) for b in h[:n])


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    s = re.sub(r"[\d_]+$", "", s[:32])         # names must not end in a number
    return s


def base_part_id(item: dict) -> str:
    base = item["id"] if item.get("source") == "builtin" else _slug(item.get("name", ""))
    base = _slug(base) or _slug(item.get("category", "")) or "catalog_part"
    if not base[0].isalpha():
        base = "part_" + base
    return base[:36]


def read_meta(part_dir: Path) -> dict | None:
    """part.json, read without following links (C6 safe_io); None if absent or unsafe."""
    try:
        meta = safe_io.read_json(part_dir, META)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError, safe_io.UnsafePath):
        return None


def choose_part_id(parts_root: Path, base: str, owner: dict, requested: str | None = None
                   ) -> tuple[str, bool]:
    """(part id, already there). `owner` is the meta that makes an existing part "the same"
    (e.g. same catalog item in the same build): inserting it again returns it."""
    def same(pid: str) -> bool:
        meta = read_meta(parts_root / pid)
        return meta is not None and all(meta.get(k) == v for k, v in owner.items())

    if requested is not None:
        if not PART_ID_RE.match(requested):
            raise _bad(f"part_id {requested!r} must match {PART_ID_RE.pattern}")
        check_name(requested)
        if os.path.lexists(parts_root / requested):
            if same(requested):
                return requested, True
            raise MisesError(ErrorCode.STALE_VERSION, f"part {requested!r} already exists",
                             hint="choose another part_id")
        return requested, False
    seed = json.dumps(owner, sort_keys=True)
    for pid in (base, f"{base}_{_letters(seed, 4)}", f"{base}_{_letters(seed, 8)}"):
        if not os.path.lexists(parts_root / pid):
            return pid, False
        if same(pid):
            return pid, True
    raise MisesError(ErrorCode.STALE_VERSION, f"could not find a free part id for {base!r}",
                     hint="pass part_id")


# ---- building

def _geometry_used(stdout: str) -> str:
    m = re.findall(r"MISES_GEOMETRY=(builtin|envelope)", stdout or "")
    return m[-1] if m else "envelope"




def choose_lock(parts_root: Path, base: str) -> asyncio.Lock:
    """Held while a part id is chosen AND its part is built, so two inserts whose names slug to
    the same base cannot pick the same free id."""
    return _lock(Path(parts_root) / f".choose-{base}")


async def build_part(parts_root: Path, part_id: str, script: str, vars_: dict[str, float],
                     meta: dict, *, mass_g: float | None = None,
                     density_g_cm3: float = DEFAULT_G_CM3, fill_factor: float = 1.0,
                     mass_note: str | None = None,
                     files: dict[str, bytes] | None = None) -> dict:
    """Write and build a NEW part dir (`parts_root/part_id`); on failure nothing is left, and
    an existing dir is never touched. Returns {"part_id", "metrics", "meta"}.

    Mass: `mass_g` (a stated catalog mass) wins and is recorded as mass_source "catalog";
    otherwise it is volume x density x fill_factor, recorded as mass_source "estimate" with
    the factors in mass_note."""
    if not PART_ID_RE.match(part_id):
        raise _bad(f"bad part id {part_id!r}")
    check_name(part_id)
    d = Path(parts_root) / part_id
    async with _lock(d):
        if os.path.lexists(d):
            raise MisesError(ErrorCode.STALE_VERSION, f"part {part_id!r} already exists",
                             hint="insert again: a free id is chosen")
        safe_io.ensure_dir(d)
        safe_io.write_text(d, "part.py", script)
        for fname, data in (files or {}).items():   # e.g. a reference part's STEP/STL
            safe_io.write_bytes(d, fname, data)
        effective = density_g_cm3 * fill_factor
        try:
            res = await run_build(d, vars_, density_g_cm3=effective)
            bad = _after_build(d)       # links / special files a build left: refuse the part
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        if bad or not res.ok:
            shutil.rmtree(d, ignore_errors=True)
            why = (f"the build left links or special files ({', '.join(bad[:5])})" if bad
                   else (res.error or "")[-400:])
            raise MisesError(ErrorCode.TOOL_FAILED, f"building {part_id} failed: {why}")
        metrics = res.metrics
        vol_cm3 = (metrics.get("volume_mm3") or 0) / 1000
        if mass_g is not None and mass_g > 0 and vol_cm3 > 0:
            effective = mass_g / vol_cm3
            metrics = {**metrics, "mass_g": mass_g, "mass_source": "catalog"}
        else:
            note = mass_note or (f"estimate: volume x {density_g_cm3:g} g/cm3"
                                 f" x fill factor {fill_factor:g}")
            metrics = {**metrics, "mass_source": "estimate", "mass_note": note}
        safe_io.write_text(d, "metrics.json", json.dumps(metrics))
        meta = {**meta, "geometry": _geometry_used(res.stdout_tail),
                "mass_g": metrics.get("mass_g"), "mass_source": metrics["mass_source"],
                "density_g_cm3": effective, "fill_factor": fill_factor}
        safe_io.write_text(d, "vars.json", json.dumps(res.vars))
        safe_io.write_text(d, META, json.dumps(meta))
        _edits.write_version(d, 1)      # C6 versions: a new part's first build is version 1
    return {"part_id": part_id, "metrics": metrics, "meta": meta}


def _existing_part(d: Path) -> dict:
    try:
        metrics = safe_io.read_json(d, "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        metrics = None
    return {"part_id": d.name, "metrics": metrics, "meta": read_meta(d), "existing": True}


async def insert_item(parts_root: Path, item: dict, project_id: str | None, *,
                      part_id: str | None = None) -> dict:
    """Add a catalog item to a build as a `kind: "catalog"` part. Inserting the same item into
    the same build again returns the existing part (assemblies instance it as often as
    needed) with `existing: true`."""
    parts_root = Path(parts_root)
    script, vars_ = script_for(item)
    owner = {"kind": "catalog", "catalog_item_id": item["id"], "project_id": project_id}
    base = base_part_id(item)
    dims = item.get("dims") or {}
    mass = dims.get("mass_g")
    mass = float(mass) if isinstance(mass, (int, float)) and not isinstance(mass, bool) else None
    category = item.get("category")
    density = _DENSITY.get(category, DEFAULT_G_CM3)
    fill = _FILL.get(category, 1.0)
    note = None
    if mass is None and category not in _DENSITY:
        note = (f"estimate: material unknown; volume x {density:g} g/cm3 (PLA) x fill factor"
                f" {fill:g}. Add the real mass to the item")
    meta = {**owner, "label": item.get("name", "")[:200], "category": category,
            "source": item.get("source"), "url": item.get("url"), "mount": item.get("mount")}
    async with choose_lock(parts_root, base):
        pid, existing = choose_part_id(parts_root, base, owner, part_id)
        if existing:
            return _existing_part(parts_root / pid)
        out = await build_part(parts_root, pid, script, vars_, meta, mass_g=mass,
                               density_g_cm3=density, fill_factor=fill, mass_note=note)
    return {**out, "existing": False}
