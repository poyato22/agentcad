"""Drop / impact studies: an ESTIMATE. The impact is replaced by an equivalent static load
F = m g k (loads.impact_equivalent, k stated) applied as a body load toward the impact face,
which is held fixed (the floor). No explicit dynamics: a *DYNAMIC run of a drop needs contact
and microsecond time steps that do not fit a laptop study budget (see the C7 report)."""
from __future__ import annotations

from .environments import FACE_PRESETS
from .mesh import FACE_TAG_RE, tag_face_ids
from .fem import FemJob
from .settings import GravityLoad, LoadCase, Support
from .static import run_static
from .types import SimError

G = 9.81


def impact_newtons(mass_kg: float, drop_m: float, k: float) -> dict:
    """{newtons, estimate, formula, source}. Uses C3's engineering.loads.impact_equivalent when
    it exists (merge note: C3 defines it in core/src/mises/engineering/loads.py), else the same
    formula locally."""
    try:
        from ..engineering.loads import impact_equivalent  # type: ignore[import-not-found]
    except ImportError:
        impact_equivalent = None
    if impact_equivalent is not None:
        r = impact_equivalent(mass_kg, drop_m, k)
        if isinstance(r, (int, float)):
            n, formula = float(r), "F = m g k"
        elif isinstance(r, dict):
            n = float(r.get("newtons") or r.get("n") or r.get("force_n"))
            formula = str(r.get("formula") or "F = m g k")
        else:
            n = float(getattr(r, "newtons"))
            formula = str(getattr(r, "formula", "F = m g k"))
        return {"newtons": n, "estimate": True, "formula": formula,
                "source": "engineering.loads.impact_equivalent"}
    return {"newtons": mass_kg * G * k, "estimate": True,
            "formula": f"F = m g k = {mass_kg:.4g} kg x {G} m/s2 x {k:g}",
            "source": "local (engineering.loads not present)"}


def impact_direction(face: str, metrics: dict) -> tuple[float, float, float]:
    """The outward normal of the impact face: the inertial load points at the floor."""
    if face in FACE_PRESETS:
        axis, side = FACE_PRESETS[face]
        d = [0.0, 0.0, 0.0]
        d[axis] = -1.0 if side == "min" else 1.0
        return (d[0], d[1], d[2])
    if FACE_TAG_RE.match(face):  # a tagged face: the first one it names
        face = tag_face_ids(face, metrics.get("face_tags"))[0]
    f = next((x for x in metrics.get("face_list") or [] if x.get("id") == face), None)
    if f is None or not f.get("normal"):
        raise SimError("bad_setup", f"no face {face} with a normal on this part",
                       hint="use a preset (bottom, top, ...) or a planar face id")
    return tuple(float(x) for x in f["normal"])  # type: ignore[return-value]


async def run_drop(job: FemJob, s, *, sim_id: str, part_id: str) -> dict:
    m = job.material
    mass_g = s.mass_g or job.metrics.get("volume_mm3", 0) * m.density_g_cm3 / 1000.0
    if mass_g <= 0:
        raise SimError("bad_setup", "the part has no mass", hint="set mass_g")
    imp = impact_newtons(mass_g / 1000.0, s.drop_m, s.k)
    g_mult = imp["newtons"] / (mass_g / 1000.0 * G)
    case = LoadCase(name="impact", supports=[Support(kind="fixed", face=s.impact_face)],
                    loads=[GravityLoad(kind="gravity", g=min(g_mult, 1000.0),
                                       direction=impact_direction(s.impact_face, job.metrics))],
                    note=f"equivalent static load {imp['newtons']:.4g} N ({imp['formula']})")
    out = await run_static(job, [case], env=None, sim_id=sim_id, part_id=part_id)
    out["results"].update({"impact": {**imp, "mass_g": mass_g, "drop_m": s.drop_m, "k": s.k,
                                      "g_equivalent": g_mult,
                                      "impact_velocity_m_s": (2 * G * s.drop_m) ** 0.5}})
    out["warnings"].insert(0, f"ESTIMATE: the drop is an equivalent static load of "
                              f"{imp['newtons']:.3g} N (k = {s.k:g}); real impacts depend on "
                              "the floor, the contact and the drop angle")
    return out
