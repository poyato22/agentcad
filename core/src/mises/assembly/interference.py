"""G2: the ONE list of overlapping parts in a build's assembly. The app's red boxes (GET
/projects/{id}/assembly/interference) and assembly_health's overlaps (what the lead is told to
fix) both come from `interferences` here, so the agent sees exactly what the user sees.

- The volumes are the sandboxed worker's OCCT common of each pair, taken in the first part's own
  frame (cad/worker.py `_assembly_interference`): a pair's verdict depends only on how the two
  parts sit against each other.
- Overlaps under TOL_MM3 are numerical touching (a rod end 0.01 mm into its socket floor), not
  a clash: never listed.
- An intended contact (a press fit, a bearing seat, a pin in its bore) is declared with
  `assembly_allow_contact` or a joint's `design.contact`: it stays in the list with `intended`
  (its reason), drawn differently by the app, and is never reported as a problem.
"""
from __future__ import annotations

from pathlib import Path

#: an overlap below this (mm^3) is numerical touching, not red and not reported
TOL_MM3 = 0.1


def allowed_contacts(asm_store, asm, project_id: str) -> dict[tuple[str, str], str]:
    """{(instance id, instance id) sorted: reason} for every declared intended contact: the
    build's assembly_allow_contact rows and each joint whose design carries `contact`."""
    out: dict[tuple[str, str], str] = {}
    for j in asm.joints:
        reason = getattr(getattr(j, "design", None), "contact", None)
        if reason:
            out[tuple(sorted((j.a.instance_id, j.b.instance_id)))] = reason
    if hasattr(asm_store, "list_contacts"):
        for c in asm_store.list_contacts(project_id):
            out[tuple(sorted((c["a"], c["b"])))] = c["reason"]
    return out


def classify(hits: list[dict], asm, allowed: dict[tuple[str, str], str]) -> list[dict]:
    """The worker's raw hits → the list: under TOL_MM3 dropped, ids filled from names when the
    worker gave none, `intended` set (reason or None)."""
    by_name = {i.name: i.id for i in asm.instances}
    out = []
    for h in hits:
        vol = round(float(h.get("volume_mm3") or 0.0), 3)
        if vol < TOL_MM3:
            continue
        a_id = h.get("a_id") or by_name.get(h.get("a"), h.get("a"))
        b_id = h.get("b_id") or by_name.get(h.get("b"), h.get("b"))
        item = {"a": h.get("a"), "b": h.get("b"), "a_id": a_id, "b_id": b_id,
                "volume_mm3": vol, "intended": allowed.get(tuple(sorted((a_id, b_id))))}
        for k in ("center", "bbox"):
            if h.get(k) is not None:
                item[k] = h[k]
        out.append(item)
    return out


async def interferences(asm_store, root: Path, project_id: str, *, asm=None, res=None,
                        timeout_s: float = 90) -> list[dict]:
    """Every overlapping pair of the build's assembly at its solved pose (see the module doc).
    Raises MisesError like run_geometry (NOT_FOUND when no part is built)."""
    from .geometry import run_geometry
    from .pose import solve_assembly
    if asm is None:
        asm = asm_store.get(project_id)
    if res is None:
        res = solve_assembly(asm, root)
    geo = await run_geometry(root, asm, res, want="interference", timeout_s=timeout_s)
    return classify(geo.interferences, asm, allowed_contacts(asm_store, asm, project_id))


def problems(items: list[dict]) -> list[dict]:
    """The overlaps that are problems (not declared intended)."""
    return [i for i in items if not i.get("intended")]
