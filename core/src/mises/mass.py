"""Mass for every part in a build's tree (Plan 9 B2, D8).

- A built part weighs its solid volume (metrics.json `volume_mm3`) x the density of ITS
  material: the row's `material`, else the build default (the profile's `default_material`).
  The worker's own `mass_g` (built with whatever density it was handed) never decides; views
  overwrite `metrics.mass_g` with this number so every reader agrees.
- A catalog part keeps the mass its insert recorded (a stated catalog mass or an estimate).
- A group, or a part with children, weighs the recursive sum of its subtree (`sum`); parts in
  it whose mass is unknown are named in `mass_note`.
- Anything else is `mass_g: null`, `mass_source: "unknown"`, with a human reason.

Densities come from one place: the sim materials table (built-ins and the user's custom
materials, the same table studies use), then the slicer's filament table for filaments the
sim table does not carry (TPU). The BOM's made-part estimate and the assembly total read the
same numbers, so the BOM, the tree and the assembly never disagree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .events import Event
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput

DEFAULT_MATERIAL = "PLA"


def _lookup(material: str | None, home: Path | None) -> tuple[str, float] | None:
    key = (material or "").strip().lower()
    if not key:
        return None
    from .sim.materials import MATERIALS, load_custom
    for m in [*MATERIALS.values(), *load_custom(home)]:
        if m.name.lower() == key:
            return m.name, float(m.density_g_cm3)
    from .print.slicer import MATERIALS as FILAMENTS
    for name, f in FILAMENTS.items():
        if name.lower() == key:
            return name, float(f.density_g_cm3)
    return None


def density_g_cm3(material: str | None, home: Path | None = None) -> float | None:
    """The material's density in g/cm3, or None when no table knows it."""
    hit = _lookup(material, home)
    return hit[1] if hit else None


def canonical_material(material: str | None, home: Path | None = None) -> str | None:
    """The table's spelling of a material name ("petg" -> "PETG"), or None when unknown."""
    hit = _lookup(material, home)
    return hit[0] if hit else None


def known_materials(home: Path | None = None) -> list[str]:
    from .print.slicer import MATERIALS as FILAMENTS
    from .sim.materials import MATERIALS, load_custom
    names = [*MATERIALS, *(c.name for c in load_custom(home))]
    return names + [n for n in FILAMENTS if n.lower() not in {x.lower() for x in names}]


def default_material(home: Path | None) -> str:
    if home is None:
        return DEFAULT_MATERIAL
    from .profile import load_profile
    return load_profile(home).default_material or DEFAULT_MATERIAL


def build_density(store, home: Path, part_id: str) -> float:
    """The density a part's next build hands the worker (so the build's own metrics agree with
    the tree): its row's material, else the build default; PLA when neither is known."""
    try:   # a bare test context's store may not be a Store
        row = store.get_part(part_id) if store is not None else None
    except Exception:  # noqa: BLE001 - no row: the build default
        row = None
    mat = getattr(row, "material", None) or default_material(home)
    return density_g_cm3(mat, home) or density_g_cm3(DEFAULT_MATERIAL)  # type: ignore[return-value]


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _name(r: dict) -> str:
    label = (r.get("label") or "").strip()
    return label or r["id"]


def _own(r: dict, has_disk: bool, home: Path | None, default_mat: str,
         dens: dict) -> tuple[float | None, str, str | None]:
    """(mass_g, source, note) of the row itself, before any children."""
    kind, metrics = r.get("kind") or "part", r.get("metrics")
    if kind == "skeleton":
        return None, "unknown", "layout skeleton: it has no mass"
    if kind == "group":
        return None, "unknown", None
    if kind == "catalog":
        m = _num((metrics or {}).get("mass_g"))
        if m is not None and m >= 0:
            src = (metrics or {}).get("mass_source")
            note = (metrics or {}).get("mass_note") or ("stated catalog mass" if src == "catalog"
                                                        else "estimated from the catalog item")
            return m, "catalog", str(note)
        return None, "unknown", ("the catalog item states no mass" if has_disk
                                 else "the catalog part has no geometry or stated mass")
    if metrics is None:
        return None, "unknown", ("build failed: the part has no successful build yet"
                                 if has_disk else "not built yet")
    vol = _num(metrics.get("volume_mm3"))
    if vol is None or vol < 0:
        return None, "unknown", "the build reported no volume; rebuild the part"
    mat = r.get("material") or default_mat
    if mat not in dens:
        dens[mat] = _lookup(mat, home)
    hit = dens[mat]
    if hit is None:
        return None, "unknown", f"no density for material {mat}"
    shown, rho = hit
    src = "" if r.get("material") else ", build default"
    return (round(vol / 1000.0 * rho, 4), "built",
            f"{vol:.0f} mm3 x {rho:g} g/cm3 ({shown}{src})")


def annotate(rows: list[dict], home: Path | None, *, has_disk: set[str],
             default_mat: str | None = None) -> list[dict]:
    """Set mass_g / mass_source / mass_note on every tree row (in place) and return the rows.
    `has_disk` holds the ids whose part dir exists. A built part's `metrics.mass_g` is
    replaced by its tree mass (a copy; nothing on disk changes)."""
    default_mat = default_mat or default_material(home)
    dens: dict = {}
    by_id = {r["id"]: r for r in rows}
    own = {r["id"]: _own(r, r["id"] in has_disk, home, default_mat, dens) for r in rows}
    kids: dict[str, list[str]] = {}
    for r in rows:
        p = r.get("parent_id")
        if p in by_id and p != r["id"]:
            kids.setdefault(p, []).append(r["id"])
    memo: dict[str, tuple[float | None, list[str]]] = {}

    def subtree(i: str, seen: frozenset) -> tuple[float | None, list[str]]:
        """(known mass of the subtree or None, names of parts in it with unknown mass)."""
        if i in memo:
            return memo[i]
        m, _src, _note = own[i]
        r = by_id[i]
        total = m
        unknown: list[str] = []
        # a part row whose build dir is gone is hidden from the tree: it weighs nothing here
        if m is None and (r.get("kind") == "catalog"
                          or (r.get("kind") == "part" and i in has_disk)):
            unknown.append(_name(r))
        for c in kids.get(i, []):
            if c in seen:
                continue                      # a cycle is never followed
            cm, cu = subtree(c, seen | {c})
            if cm is not None:
                total = (total or 0.0) + cm
            unknown += cu
        memo[i] = (None if total is None else round(total, 4), unknown)
        return memo[i]

    for r in rows:
        i = r["id"]
        m, src, note = own[i]
        if kids.get(i) and r.get("kind") != "skeleton":
            total, unknown = subtree(i, frozenset({i}))
            n = len(kids[i])
            if total is None:
                m, src = None, "unknown"
                note = "no part in it has a known mass yet"
            else:
                m, src = total, "sum"
                note = f"sum of {n} item{'s' if n != 1 else ''}" + \
                    (" plus its own" if own[i][0] is not None else "")
            if unknown:
                k = len(unknown)
                names = ", ".join(unknown[:6]) + (", ..." if k > 6 else "")
                note += f"; excludes {k} part{'s' if k != 1 else ''} with no known mass: {names}"
        elif r.get("kind") == "group":
            note = "empty group"
        r["mass_g"], r["mass_source"], r["mass_note"] = m, src, note
        if r.get("kind") == "part" and own[i][1] == "built" and isinstance(r.get("metrics"), dict):
            r["metrics"] = {**r["metrics"], "mass_g": own[i][0]}
    return rows


def assembly_total(store, home: Path, project_id: str, instances) -> dict:
    """{total_mass_g, mass_unknown}: each instance adds its part's tree mass; parts with no
    known mass are listed with how many instances they have and why."""
    from . import projects
    rows = {r["id"]: r for r in projects.mass_rows(store, home, project_id, versions=False)}
    total = 0.0
    unknown: dict[str, dict] = {}
    for inst in instances:
        pid = inst.part_id
        r = rows.get(pid)
        if r is None:
            row = store.get_part(pid)
            r = rows[pid] = (projects.part_view(store, home, row) if row is not None else
                             {"mass_g": None, "mass_note": "not in the part tree"})
        if r.get("mass_g") is None:
            u = unknown.setdefault(pid, {"part_id": pid, "count": 0,
                                         "reason": r.get("mass_note") or "unknown mass"})
            u["count"] += 1
        else:
            total += r["mass_g"]
    return {"total_mass_g": round(total, 4), "mass_unknown": list(unknown.values())}


# ---- the agent tool

class SetMaterialArgs(BaseModel):
    part_id: str
    material: str | None = None


@tool("part_set_material", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      description="Set the material a part is made of (its mass is volume x this material's "
                  "density; the BOM and the assembly total follow). Use a name from the "
                  "materials table (PLA, PETG, ABS, ASA, PA-CF, TPU or a custom study "
                  "material); null goes back to the build default. Does not change the "
                  "geometry or the look (use part_set_appearance for that).")
async def part_set_material(ctx: ToolContext, args: SetMaterialArgs) -> ToolOutput:
    from . import projects
    row = projects.patch_part(ctx.store, Path(ctx.workdir), args.part_id,
                              material=args.material)
    mat = row.get("material") or f"{default_material(Path(ctx.workdir))} (build default)"
    mg = row.get("mass_g")
    summary = f"{args.part_id}: material {mat}, " + \
        (f"mass {mg:.2f} g" if mg is not None else f"mass unknown ({row.get('mass_note')})")
    events = getattr(ctx, "events", None)
    if events is not None:
        events.publish(Event(type="part_changed", run_id=ctx.run_id, data={
            "part_id": args.part_id, "version": row.get("version") or 1, "by": "agent",
            "run_id": ctx.run_id, "summary": summary}))
    return ToolOutput(summary=summary, data={"part_id": args.part_id,
                                             "material": row.get("material"), "mass_g": mg,
                                             "mass_source": row.get("mass_source")},
                      step_kind=StepKind.DIFF)


def register_mass_tools(registry: ToolRegistry) -> None:
    registry.register(part_set_material)


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def check_material(material: str | None, home: Path | None) -> str | None:
    """None stays None (the build default); a known name comes back in the table's spelling."""
    if material is None:
        return None
    name = canonical_material(str(material), home)
    if name is None:
        raise _bad(f"unknown material {material!r}",
                   hint=f"materials: {', '.join(known_materials(home))}")
    return name
