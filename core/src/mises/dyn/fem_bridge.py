"""Dynamics -> FEM (Plan 10 C3, research §4.4): the peak joint reactions on a link become a
CalculiX static load case on that link's part.

For link L at its peak step t*:
- support: the bore (cylindrical face) of L's parent joint, `fixed` (it carries the actuator
  torque about the joint axis as well; a pinned bore would leave the link free to spin about
  it, a singular model), else the face nearest the anchor;
- loads: at each child joint, the force + moment the child pushes back with (remote force at
  the anchor on that joint's bore, moment on the same face); at each floor contact on L, the
  contact force (remote force on the face nearest the contact point).

Vectors come out of MuJoCo in world axes at t*; they are turned into the body frame at t*
(xmat), which at the exported pose is world-aligned, then into the part's own axes through the
instance's transform. Points also go through the body origin.
"""
from __future__ import annotations

import math

import numpy as np

MIN_FORCE_N = 0.01
MIN_MOMENT_NMM = 1.0
BORE_REACH_MM = 15.0


def body_origins(model: dict) -> dict[str, np.ndarray]:
    """Absolute body origins (m) at the exported pose."""
    out: dict[str, np.ndarray] = {}
    for b in model["bodies"]:
        p = out.get(b.get("parent") or "", np.zeros(3)) if b.get("parent") else np.zeros(3)
        out[b["name"]] = p + np.array(b["pos"], dtype=float)
    return out


class Frames:
    """World vectors / points at t* -> one instance's part coordinates (mm)."""

    def __init__(self, xmat, xpos, origin_m, T_inst):
        self.R = np.array(xmat, dtype=float).reshape(3, 3)
        self.x = np.array(xpos, dtype=float)
        self.o = np.array(origin_m, dtype=float)
        T = np.array(T_inst, dtype=float)
        self.Ri, self.ti = T[:3, :3], T[:3, 3]

    def vec(self, v) -> np.ndarray:
        return self.Ri.T @ (self.R.T @ np.array(v, dtype=float))

    def point(self, p) -> np.ndarray:
        at_export = self.R.T @ (np.array(p, dtype=float) - self.x) + self.o     # m, world
        return self.Ri.T @ (at_export * 1000.0 - self.ti)                       # mm, part


def nearest_face(metrics: dict, point_mm, axis=None, *, avoid: set | None = None) -> dict:
    """The bore at a joint: the cylindrical face whose axis passes nearest the point (and runs
    along `axis` when given), else the face whose centre is nearest. {face, kind, distance}."""
    p = np.array(point_mm, dtype=float)
    faces = [f for f in metrics.get("face_list") or [] if f.get("id") not in (avoid or set())]
    best = None
    if axis is not None:
        ax = np.array(axis, dtype=float)
        ax = ax / max(np.linalg.norm(ax), 1e-12)
        for f in faces:
            if f.get("type") != "cylinder" or f.get("radius") is None:
                continue
            c = np.array(f["center"], dtype=float)
            off = c - p
            radial = np.linalg.norm(off - np.dot(off, ax) * ax)
            d = radial + 0.2 * abs(np.dot(off, ax))
            if radial <= max(2.0, 0.6 * float(f["radius"])) and d <= BORE_REACH_MM + float(
                    f["radius"]) and (best is None or d < best[1]):
                best = (f, d)
    if best is not None:
        return {"face": best[0]["id"], "kind": "bore", "distance_mm": round(best[1], 2)}
    near = min(faces, key=lambda f: float(np.linalg.norm(np.array(f["center"]) - p)),
               default=None)
    if near is None:
        return {"face": None, "kind": "none", "distance_mm": None}
    return {"face": near["id"], "kind": "nearest",
            "distance_mm": round(float(np.linalg.norm(np.array(near["center"]) - p)), 2)}


def _unit(v) -> list[float]:
    n = float(np.linalg.norm(v))
    return [float(x) / n for x in v] if n > 0 else [0.0, 0.0, -1.0]


def load_case(link: dict, frames: Frames, metrics: dict, *, label: str) -> dict:
    """{case: LoadCase dict for sim settings, notes, entries} for one link's peak."""
    notes: list[str] = []
    parent_axis = frames.vec(link["axis"]) if link.get("axis") else None
    sup = nearest_face(metrics, frames.point(link["anchor_m"]), parent_axis)
    if sup["face"] is None:
        raise ValueError("the part has no faces in its metrics (rebuild it)")
    if sup["kind"] != "bore":
        notes.append(f"no bore found at the parent joint: held on the nearest face "
                     f"{sup['face']} ({sup['distance_mm']} mm from the anchor)")
    loads, entries = [], []
    for ch in link.get("children") or []:
        f = np.array(ch["force_n"], dtype=float)
        m = np.array(ch["moment_nm"], dtype=float)
        pa = frames.point(ch["anchor_m"])
        cax = ch.get("axis") or link.get("axis")
        bore = nearest_face(metrics, pa, frames.vec(cax) if cax else None,
                            avoid={sup["face"]})
        fp, mp = frames.vec(f), frames.vec(m)
        if np.linalg.norm(fp) >= MIN_FORCE_N:
            loads.append({"kind": "remote_force", "face": bore["face"],
                          "newtons": round(float(np.linalg.norm(fp)), 4),
                          "direction": [round(x, 6) for x in _unit(fp)],
                          "point": [round(float(x), 3) for x in pa]})
        if np.linalg.norm(mp) * 1000 >= MIN_MOMENT_NMM:
            loads.append({"kind": "moment", "face": bore["face"],
                          "n_mm": round(float(np.linalg.norm(mp)) * 1000, 3),
                          "axis": [round(x, 6) for x in _unit(mp)]})
        entries.append({"from": ch["joint"], "face": bore["face"], "face_kind": bore["kind"],
                        "force_n": round(float(np.linalg.norm(fp)), 3),
                        "moment_nm": round(float(np.linalg.norm(mp)), 4)})
    for ct in link.get("contacts") or []:
        fp = frames.vec(ct["force_n"])
        if np.linalg.norm(fp) < MIN_FORCE_N:
            continue
        pc = frames.point(ct["pos_m"])
        face = nearest_face(metrics, pc, None, avoid={sup["face"]})
        loads.append({"kind": "remote_force", "face": face["face"],
                      "newtons": round(float(np.linalg.norm(fp)), 4),
                      "direction": [round(x, 6) for x in _unit(fp)],
                      "point": [round(float(x), 3) for x in pc]})
        entries.append({"from": "floor contact", "face": face["face"], "face_kind": "nearest",
                        "force_n": round(float(np.linalg.norm(fp)), 3)})
    merged = _merge(loads)
    if not merged:
        raise ValueError("no load enters this link at its peak (a leaf with no contact)")
    if len(merged) > 12:
        merged = sorted(merged, key=lambda x: -(x.get("newtons") or x.get("n_mm", 0) / 100))[:12]
        notes.append("more than 12 loads: the 12 largest kept")
    case = {"name": label[:60], "supports": [{"kind": "fixed", "face": sup["face"]}],
            "loads": merged,
            "note": ("dynamics peak (rigid-body reactions); " + "; ".join(notes))[:300]}
    return {"case": case, "support": sup, "entries": entries, "notes": notes}


def _merge(loads: list[dict]) -> list[dict]:
    """Two contact points on the same face become one resultant remote force."""
    out, by_face = [], {}
    for ld in loads:
        if ld["kind"] != "remote_force":
            out.append(ld)
            continue
        k = ld["face"]
        if k not in by_face:
            by_face[k] = [ld]
        else:
            by_face[k].append(ld)
    for face, group in by_face.items():
        if len(group) == 1:
            out.insert(0, group[0])
            continue
        F = sum(np.array(g["direction"]) * g["newtons"] for g in group)
        w = sum(g["newtons"] for g in group)
        P = sum(np.array(g["point"]) * g["newtons"] for g in group) / max(w, 1e-12)
        if np.linalg.norm(F) >= MIN_FORCE_N:
            out.insert(0, {"kind": "remote_force", "face": face,
                           "newtons": round(float(np.linalg.norm(F)), 4),
                           "direction": [round(x, 6) for x in _unit(F)],
                           "point": [round(float(x), 3) for x in P]})
    return out


def load_index(link: dict, size_m: float) -> float:
    """How hard a link is loaded, to pick the "most loaded" ones: the peak bending moment at
    its parent joint plus the peak force over its own length (N m)."""
    return float(link.get("moment_perp_nm") or 0.0) + float(link["peak_force_n"]) * size_m


def rank(reactions: list[dict], sizes: dict[str, float], n: int) -> list[dict]:
    scored = [(load_index(r, sizes.get(r["body"], 0.05)), r) for r in reactions]
    scored.sort(key=lambda x: -x[0])
    return [dict(r, load_index_nm=round(s, 4)) for s, r in scored[:n]]


def deg(rad: float) -> float:
    return math.degrees(rad)
