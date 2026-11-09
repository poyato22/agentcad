"""Print simulation: printability of a built part from its STL.

Two sources, both labelled as estimates:
- a direct mesh check (numpy): overhang area beyond 45 degrees from vertical, first-layer bed
  contact area, height, separate bodies and the minimum gap between them (print-in-place);
- the PrusaSlicer CLI, run headless in a sandbox (no network, no child processes, writes only
  into the check's own dir): print time, filament length / grams, and how much support
  material it generates (summed from the G-code's `;TYPE:Support material` sections).

Candidate orientations are the six axis-aligned "this side down" poses; the mesh check scores
them all, the slicer runs on the as-built pose and the best few. The recommendation is the
pose with the least support (slicer support grams when sliced, else mesh overhang area), then
the most bed contact, then the lowest height, then the shortest slicer time.
"""
from __future__ import annotations

import asyncio
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..types import ErrorCode, MisesError
from . import slicer

FIDELITY = "slicer_estimate"
OVERHANG_DEG = 45.0
DEFAULT_MIN_CLEARANCE_MM = 0.3
BED_TOL_MM = 0.05
FILAMENT_D_MM = 1.75

# "<side> down": the rotation that turns that side of the part toward the bed (-Z)
ORIENTATIONS: dict[str, np.ndarray] = {
    "as_built": np.eye(3),
    "flip_z": np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], float),        # +Z side down
    "x_down": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float),        # -X side down
    "x_up_down": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float),     # +X side down
    "y_down": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float),        # -Y side down
    "y_up_down": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float),     # +Y side down
}
ORIENTATION_DOC = {
    "as_built": "as modelled (-Z side on the bed)", "flip_z": "flipped (+Z side on the bed)",
    "x_down": "-X side on the bed", "x_up_down": "+X side on the bed",
    "y_down": "-Y side on the bed", "y_up_down": "+Y side on the bed",
}

# material-specific slicer settings on top of slicer.build_ini (temperatures live there)
MATERIAL_EXTRA: dict[str, dict[str, str]] = {
    "PLA": {"filament_type": "PLA", "max_fan_speed": "100", "min_fan_speed": "100",
            "filament_max_volumetric_speed": "15"},
    "PETG": {"filament_type": "PETG", "max_fan_speed": "50", "min_fan_speed": "30",
             "filament_max_volumetric_speed": "8", "filament_retract_length": "0.8"},
    "ABS": {"filament_type": "ABS", "max_fan_speed": "15", "min_fan_speed": "0",
            "filament_max_volumetric_speed": "12"},
    "ASA": {"filament_type": "ASA", "max_fan_speed": "15", "min_fan_speed": "0",
            "filament_max_volumetric_speed": "12"},
    "TPU": {"filament_type": "FLEX", "max_fan_speed": "60", "min_fan_speed": "30",
            "filament_max_volumetric_speed": "3.5", "retract_length": "0",
            "perimeter_speed": "25", "infill_speed": "25", "external_perimeter_speed": "20"},
}


def analysis_ini(model: str, material: str, *, supports: bool = True) -> str:
    """slicer.build_ini plus relative E (so support extrusion can be summed), auto supports at
    45 degrees and the material's extras. Later keys override earlier ones."""
    extra = {"use_relative_e_distances": "1", "layer_gcode": "G92 E0",
             "support_material": "1" if supports else "0", "support_material_auto": "1",
             "support_material_threshold": f"{OVERHANG_DEG:g}",
             **MATERIAL_EXTRA.get(material.upper(), {})}
    kv: dict[str, str] = {}
    for line in slicer.build_ini(model, material).splitlines():
        k, _, v = line.partition(" = ")
        kv[k] = v
    kv.update(extra)
    return "".join(f"{k} = {v}\n" for k, v in kv.items())


# ---------------------------------------------------------------- mesh I/O

def read_stl(path: Path) -> np.ndarray:
    """Triangles (n, 3, 3) from a binary or ASCII STL."""
    data = Path(path).read_bytes()
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                      ("a", "<u2")]), count=n, offset=84)
            return rec["v"].astype(float)
    nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
    if not nums or len(nums) % 3:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{path.name} is not a readable STL")
    return np.array(nums, dtype=float).reshape(-1, 3, 3)


def write_stl(path: Path, tris: np.ndarray) -> None:
    n = _normals(tris)
    rec = np.zeros(len(tris), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                              ("a", "<u2")]))
    rec["n"], rec["v"] = n, tris
    Path(path).write_bytes(b"\0" * 80 + struct.pack("<I", len(tris)) + rec.tobytes())


def _cross(tris: np.ndarray) -> np.ndarray:
    return np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])


def _normals(tris: np.ndarray) -> np.ndarray:
    c = _cross(tris)
    ln = np.linalg.norm(c, axis=1, keepdims=True)
    return np.divide(c, ln, out=np.zeros_like(c), where=ln > 0)


def _areas(tris: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(_cross(tris), axis=1)


# ---------------------------------------------------------------- per-orientation mesh check

@dataclass
class PoseCheck:
    name: str
    overhang_area_mm2: float
    bed_contact_mm2: float
    height_mm: float
    footprint_mm: tuple[float, float]
    slice: dict | None = None

    @property
    def support_score(self) -> float:
        if self.slice and self.slice.get("support_g") is not None:
            return float(self.slice["support_g"])
        return self.overhang_area_mm2 * 1e-3      # same order as grams, for unsliced poses

    def to_dict(self) -> dict:
        return {"orientation": self.name, "doc": ORIENTATION_DOC[self.name],
                "overhang_area_mm2": round(self.overhang_area_mm2, 1),
                "bed_contact_mm2": round(self.bed_contact_mm2, 1),
                "height_mm": round(self.height_mm, 2),
                "footprint_mm": [round(self.footprint_mm[0], 2), round(self.footprint_mm[1], 2)],
                "slicer": self.slice}


def pose_check(tris: np.ndarray, name: str, *, overhang_deg: float = OVERHANG_DEG) -> PoseCheck:
    """Overhang area: downward faces tilted more than overhang_deg from vertical
    (n_z < -sin(overhang_deg)) that are not on the bed. Bed contact: downward faces within
    BED_TOL_MM of the lowest point."""
    t = tris @ ORIENTATIONS[name].T
    zmin = float(t[..., 2].min())
    n, a = _normals(t), _areas(t)
    down = n[:, 2] < -math.sin(math.radians(overhang_deg)) - 1e-6
    on_bed = (n[:, 2] < -0.999) & (t[..., 2].max(axis=1) <= zmin + BED_TOL_MM)
    lo, hi = t.reshape(-1, 3).min(axis=0), t.reshape(-1, 3).max(axis=0)
    return PoseCheck(name, float(a[down & ~on_bed].sum()), float(a[on_bed].sum()),
                     float(hi[2] - lo[2]), (float(hi[0] - lo[0]), float(hi[1] - lo[1])))


# ---------------------------------------------------------------- bodies and clearance

def bodies(tris: np.ndarray) -> list[np.ndarray]:
    """Separate solid bodies: connected shells (faces sharing a vertex, vertices welded at
    1e-4 mm). A shell with negative signed volume is a cavity and joins the body around it."""
    v = np.round(tris.reshape(-1, 3) / 1e-4).astype(np.int64)
    _, inv = np.unique(v, axis=0, return_inverse=True)
    inv = inv.reshape(-1, 3)
    parent = np.arange(inv.max() + 1)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root
    for a, b, c in inv:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    roots = np.array([find(x) for x in inv[:, 0]])
    shells = [tris[roots == r] for r in np.unique(roots)]
    solids = [s for s in shells if _volume(s) > 0]
    cavities = [s for s in shells if _volume(s) <= 0]
    if not solids:
        return shells
    for cav in cavities:                       # the smallest solid whose box holds the cavity
        lo, hi = cav.reshape(-1, 3).min(0), cav.reshape(-1, 3).max(0)
        best = None
        for i, s in enumerate(solids):
            slo, shi = s.reshape(-1, 3).min(0), s.reshape(-1, 3).max(0)
            if np.all(slo <= lo + 1e-6) and np.all(shi >= hi - 1e-6):
                if best is None or _volume(s) < _volume(solids[best]):
                    best = i
        if best is not None:
            solids[best] = np.concatenate([solids[best], cav])
    return solids


def _volume(tris: np.ndarray) -> float:
    return float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum() / 6)


def _samples(tris: np.ndarray) -> np.ndarray:
    """Vertices, edge midpoints and centroids: exact for vertex-to-face gaps (flat faces,
    pins in holes), within half an edge for edge-to-edge gaps."""
    mids = (tris + np.roll(tris, -1, axis=1)) / 2
    return np.unique(np.concatenate([tris.reshape(-1, 3), mids.reshape(-1, 3),
                                     tris.mean(axis=1)]).round(6), axis=0)


def point_triangle_distance(p: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """(n, m) distances from n points to m triangles (closest point on each triangle)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    nrm = _normals(tris)
    d = p[:, None, :] - a[None]                                    # (n, m, 3)
    plane = np.einsum("nmk,mk->nm", d, nrm)
    proj = p[:, None, :] - plane[..., None] * nrm[None]
    inside = np.ones(plane.shape, bool)
    for u, w in ((a, b), (b, c), (c, a)):
        e = np.cross(w - u, nrm)                                   # inward-ish edge normal
        inside &= np.einsum("nmk,mk->nm", proj - u[None], e) <= 1e-9
    inside2 = np.ones(plane.shape, bool)
    for u, w in ((a, b), (b, c), (c, a)):
        e = np.cross(w - u, nrm)
        inside2 &= np.einsum("nmk,mk->nm", proj - u[None], e) >= -1e-9
    inside |= inside2
    best = np.where(inside, np.abs(plane), np.inf)
    for u, w in ((a, b), (b, c), (c, a)):
        seg = w - u
        L = np.maximum(np.einsum("mk,mk->m", seg, seg), 1e-30)
        t = np.clip(np.einsum("nmk,mk->nm", p[:, None, :] - u[None], seg) / L, 0, 1)
        q = u[None] + t[..., None] * seg[None]
        best = np.minimum(best, np.linalg.norm(p[:, None, :] - q, axis=2))
    return best


def min_gap(a: np.ndarray, b: np.ndarray, search_mm: float) -> float | None:
    """The minimum distance between two bodies' surfaces, or None when none is within
    search_mm (bounding boxes prefilter both sides)."""
    best = math.inf
    for src, dst in ((a, b), (b, a)):
        lo = dst.reshape(-1, 3).min(0) - search_mm
        hi = dst.reshape(-1, 3).max(0) + search_mm
        pts = _samples(src)
        pts = pts[np.all((pts >= lo) & (pts <= hi), axis=1)]
        slo, shi = src.reshape(-1, 3).min(0) - search_mm, src.reshape(-1, 3).max(0) + search_mm
        keep = np.all((dst.max(axis=1) >= slo) & (dst.min(axis=1) <= shi), axis=1)
        tri = dst[keep]
        if not len(pts) or not len(tri):
            continue
        step = max(1, int(2_000_000 // max(len(tri), 1)))
        for i in range(0, len(pts), step):
            best = min(best, float(point_triangle_distance(pts[i:i + step], tri).min()))
    return None if best > search_mm else best


def clearance_check(tris: np.ndarray, min_clearance_mm: float) -> dict:
    bs = bodies(tris)
    search = max(4 * min_clearance_mm, 2.0)
    pairs = []
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            g = min_gap(bs[i], bs[j], search)
            if g is not None:
                pairs.append({"bodies": [i, j], "gap_mm": round(g, 4),
                              "ok": g >= min_clearance_mm - 1e-6})
    worst = min((p["gap_mm"] for p in pairs), default=None)
    return {"bodies": len(bs), "min_clearance_mm": min_clearance_mm,
            "min_gap_mm": worst, "search_mm": search, "pairs": pairs[:20],
            "ok": all(p["ok"] for p in pairs),
            "note": ("one body: no print-in-place gaps to check" if len(bs) < 2 else
                     f"no two bodies are within {search:g} mm" if worst is None else
                     "gaps measured from vertices, edge midpoints and face centres to the "
                     "other body's faces")}


# ---------------------------------------------------------------- slicer pass

def support_from_gcode(gcode: str, density_g_cm3: float) -> dict:
    """Sum the relative E of extrusions inside `;TYPE:Support material*` sections."""
    e_support = 0.0
    in_support = False
    for line in gcode.splitlines():
        if line.startswith(";TYPE:"):
            in_support = line.startswith(";TYPE:Support material")
        elif in_support and line.startswith(("G1 ", "G0 ")):
            m = re.search(r"\bE(-?[0-9.]+)", line)
            if m:
                e = float(m.group(1))
                if e > 0:
                    e_support += e
    area = math.pi * (FILAMENT_D_MM / 2) ** 2
    return {"support_filament_mm": round(e_support, 1),
            "support_g": round(e_support * area / 1000 * density_g_cm3, 2)}


async def slice_pose(tris: np.ndarray, name: str, out_dir: Path, *, model: str,
                     material: str, home: Path | None = None,
                     timeout_s: float = slicer.SLICE_TIMEOUT_S) -> dict:
    """Slice one pose with PrusaSlicer in the sandbox; time, filament and support amounts."""
    from ..platform import kill_process_tree, sandboxed_command
    exe = slicer.require_slicer()
    pm, mat = slicer.model_of(model), slicer.material_of(material)
    d = Path(out_dir) / name
    d.mkdir(parents=True, exist_ok=True)
    stl, ini, gcode = d / "pose.stl", d / "settings.ini", d / "pose.gcode"
    write_stl(stl, tris @ ORIENTATIONS[name].T)
    ini.write_text(analysis_ini(model, material))
    gcode.unlink(missing_ok=True)
    (d / "datadir").mkdir(exist_ok=True)
    argv = [str(exe), "--datadir", str(d / "datadir"), "--export-gcode", "--load", str(ini),
            "--center", f"{pm.center[0]},{pm.center[1]}", "--output", str(gcode), str(stl)]
    argv = sandboxed_command(argv, d, home=home)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(d), "TMPDIR": str(d)}
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/nice", "-n", "15", *argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=d,
        start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except BaseException:
        kill_process_tree(proc.pid)
        await proc.wait()
        raise
    finally:
        kill_process_tree(proc.pid)
    if proc.returncode != 0 or not gcode.is_file():
        tail = (err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
                or f"exit {proc.returncode}")[-1500:]
        raise MisesError(ErrorCode.TOOL_FAILED, f"PrusaSlicer failed ({name}): {tail}")
    text = gcode.read_text(errors="replace")
    est = slicer.parse_estimate(text, mat.usd_per_kg)
    sup = support_from_gcode(text, mat.density_g_cm3)
    return {**est.to_dict(), **sup, "needs_supports": sup["support_g"] > 0.05}


# ---------------------------------------------------------------- the whole check

@dataclass
class CheckOptions:
    model: str = "MK4"
    material: str = "PLA"
    min_clearance_mm: float = DEFAULT_MIN_CLEARANCE_MM
    max_print_time_h: float | None = None
    slice: bool = True
    slice_candidates: int = 3
    orientations: list[str] = field(default_factory=lambda: list(ORIENTATIONS))


def _rank_key(p: PoseCheck):
    return (round(p.support_score, 2), -round(p.bed_contact_mm2, 0), round(p.height_mm, 1),
            (p.slice or {}).get("time_s") or 0)


async def check_part(stl: Path, out_dir: Path, opts: CheckOptions, *,
                     home: Path | None = None) -> dict:
    tris = read_stl(stl)
    if not len(tris):
        raise MisesError(ErrorCode.INVALID_INPUT, f"{stl} has no triangles")
    poses = {n: pose_check(tris, n) for n in dict.fromkeys(["as_built", *opts.orientations])
             if n in ORIENTATIONS}
    warnings: list[str] = []
    sliced = False
    if opts.slice:
        if slicer.find_slicer() is None:
            warnings.append(f"PrusaSlicer is not installed ({slicer.INSTALL_COMMAND}): time, "
                            "filament and support grams are missing; the mesh check stands")
        else:
            by_mesh = sorted(poses.values(), key=_rank_key)
            todo = list(dict.fromkeys(["as_built",
                                       *[p.name for p in by_mesh[:max(1, opts.slice_candidates)]]]))
            for n in todo:
                poses[n].slice = await slice_pose(tris, n, out_dir, model=opts.model,
                                                  material=opts.material, home=home)
            sliced = True
    ranked = sorted((p for p in poses.values() if not sliced or p.slice), key=_rank_key)
    best, built = ranked[0], poses["as_built"]
    rec = best.slice or {}
    hb = (rec.get("time_s") or 0) / 3600
    time_ok = None if opts.max_print_time_h is None or not rec else hb <= opts.max_print_time_h
    if time_ok is False:
        warnings.append(f"slicer estimate {rec['time_text']} is over the "
                        f"{opts.max_print_time_h:g} h limit")
    clr = clearance_check(tris, opts.min_clearance_mm)
    if not clr["ok"]:
        warnings.append(f"print-in-place gap {clr['min_gap_mm']:g} mm is under the "
                        f"{opts.min_clearance_mm:g} mm minimum: the bodies may fuse")
    if best.bed_contact_mm2 < 20:
        warnings.append(f"only {best.bed_contact_mm2:.0f} mm2 on the bed: add a brim or a flat")
    tradeoff = _tradeoff(built, best)
    printable = clr["ok"] and time_ok is not False
    return {"fidelity": FIDELITY, "label": "slicer estimate (PrusaSlicer CLI, simplified "
            f"{opts.model} 0.4 mm / {opts.material} 0.2 mm profile) plus a direct mesh check",
            "printer": opts.model, "material": opts.material,
            "triangles": int(len(tris)), "sliced": sliced,
            "recommended": best.to_dict(), "as_built": built.to_dict(),
            "candidates": [p.to_dict() for p in sorted(poses.values(), key=_rank_key)],
            "tradeoff": tradeoff, "clearance": clr,
            "max_print_time_h": opts.max_print_time_h, "time_ok": time_ok,
            "printable": printable, "warnings": warnings,
            "limits": ["slicer times are PrusaSlicer's estimate for a simplified profile, not "
                       "a measured print", "support grams count auto supports at 45 degrees",
                       "only the six axis-aligned poses are tried", "warping and bridging "
                       "sag are not modelled"]}


def _tradeoff(built: PoseCheck, best: PoseCheck) -> str:
    if best.name == built.name:
        return "the as-built orientation is already the best of the candidates"
    bits = [f"{ORIENTATION_DOC[best.name]} instead of as built"]
    bs, ss = built.slice or {}, best.slice or {}
    if bs and ss:
        bits.append(f"support {bs['support_g']:g} g -> {ss['support_g']:g} g")
        dt = (ss["time_s"] - bs["time_s"]) / 60
        bits.append(f"time {'+' if dt >= 0 else ''}{dt:.0f} min")
    else:
        bits.append(f"overhang {built.overhang_area_mm2:.0f} -> {best.overhang_area_mm2:.0f} mm2")
    bits.append(f"bed contact {built.bed_contact_mm2:.0f} -> {best.bed_contact_mm2:.0f} mm2")
    bits.append(f"height {built.height_mm:.1f} -> {best.height_mm:.1f} mm")
    return "; ".join(bits)
