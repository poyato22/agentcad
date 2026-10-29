"""Versioned part edits shared by the user (UI calls) and agents.

A part carries a small integer `version` stored next to its build outputs. Every successful
edit — a user vars or source change, an agent rebuild, or a feature op — bumps it. A caller that
passes a `base_version` that no longer matches the stored one gets a conflict and must re-read:
the user's edits and the agent's edits never silently clobber each other, and they share one
source of truth (part.py).

User edits are also appended to a global, append-only log (``home/edits.jsonl``) so an agent's
context can show the last few as data. Feature ops are recorded by appending a ``# mises-op``
block to part.py; each block wraps the current ``build`` and applies one build123d operation with
concrete geometry (face centres/normals, edge/face indices) resolved from the part's metrics when
the op was added, so regeneration is deterministic.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from .types import ErrorCode, MisesError

VERSION_FILE = "version.json"
EDIT_LOG = "edits.jsonl"
OP_MARK = "# mises-op"
MAX_LOG_LINES = 2000
RECENT_USER_EDITS = 10

OpKind = Literal["hole", "fillet", "chamfer", "shell", "extrude"]
OP_KINDS: tuple[str, ...] = ("hole", "fillet", "chamfer", "shell", "extrude")


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


# ---- version (per part dir)

def read_version(part_dir: Path) -> int:
    """The part's stored version, or 0 when it has none yet (a brand-new part, or one built
    before versioning). The first successful edit bumps it to 1."""
    try:
        v = json.loads((Path(part_dir) / VERSION_FILE).read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        v = None
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    return 0


def write_version(part_dir: Path, version: int) -> None:
    (Path(part_dir) / VERSION_FILE).write_text(json.dumps({"version": int(version)}))


def check_base_version(part_dir: Path, base_version: int | None) -> int | None:
    """Return the current version if `base_version` is stale (a conflict), else None.
    `base_version` None means the caller makes no claim and no conflict is raised."""
    current = read_version(part_dir)
    if base_version is not None and int(base_version) != current:
        return current
    return None


# ---- global user-edit log (per home)

def log_user_edit(home: Path, part_id: str, *, version: int, summary: str,
                  run_id: str | None) -> None:
    """Append one line to the global user-edit log, trimming it when it grows large."""
    path = Path(home) / EDIT_LOG
    rec = {"ts": time.time(), "part_id": part_id, "version": int(version), "by": "user",
           "summary": summary[:500], "run_id": run_id}
    with path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    _trim_log(path)


def _trim_log(path: Path) -> None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    if len(lines) > MAX_LOG_LINES:
        path.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n")


def recent_user_edits(home: Path, limit: int = RECENT_USER_EDITS) -> list[dict]:
    """The most recent user edits (oldest first within the window), as data for an agent."""
    path = Path(home) / EDIT_LOG
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines[-limit * 2:]:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("by") == "user":
            out.append(rec)
    return out[-limit:]


# ---- feature ops: append a `# mises-op` block to the source

def op_count(source: str) -> int:
    return sum(1 for ln in source.splitlines() if ln.startswith(OP_MARK))


def _num(v: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _bad(f"{name} must be a number")
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        raise _bad(f"{name} must be finite")
    if positive and f <= 0:
        raise _bad(f"{name} must be > 0")
    return f


def _vec(v: Any, name: str) -> list[float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 3):
        raise _bad(f"{name} must be a 3-vector")
    return [_num(c, f"{name}[{i}]") for i, c in enumerate(v)]


def _face(metrics: dict, face_id: Any) -> dict:
    faces = metrics.get("face_list") or []
    for f in faces:
        if f.get("id") == face_id:
            if not f.get("normal"):
                raise _bad(f"face {face_id!r} has no usable normal", "pick a flat face")
            return f
    raise _bad(f"unknown face id {face_id!r}",
               hint="list faces with mirror_faces and use an id like 'f0'")


def _edge_indices(metrics: dict, edge_ids: Any) -> list[int]:
    edges = {e.get("id"): i for i, e in enumerate(metrics.get("edge_list") or [])}
    if not isinstance(edge_ids, list) or not edge_ids:
        raise _bad("edge_ids must be a non-empty list of edge ids like 'e3'")
    out = []
    for eid in edge_ids:
        if eid not in edges:
            raise _bad(f"unknown edge id {eid!r}", "list edges with mirror_edges")
        out.append(edges[eid])
    return out


def _face_indices(metrics: dict, face_ids: Any) -> list[int]:
    ids = {f.get("id"): i for i, f in enumerate(metrics.get("face_list") or [])}
    if not isinstance(face_ids, list) or not face_ids:
        raise _bad("face_ids must be a non-empty list of face ids like 'f0'")
    out = []
    for fid in face_ids:
        if fid not in ids:
            raise _bad(f"unknown face id {fid!r}", "list faces with mirror_faces")
        out.append(ids[fid])
    return out


def _diag(metrics: dict) -> float:
    s = metrics.get("bbox", {}).get("size") or [10.0, 10.0, 10.0]
    return max(10.0, 2.0 * sum(c * c for c in s) ** 0.5)


def build_op_block(source: str, op: str, target: dict, params: dict, metrics: dict) -> str:
    """Return the source with one `# mises-op` block appended for `op`. Geometry named by
    `target` (face_id / edge_ids / face_ids) is resolved to concrete numbers from `metrics`
    now, so the block does not depend on ids resolving the same way at regenerate time."""
    if op not in OP_KINDS:
        raise _bad(f"unknown op {op!r}", f"ops: {', '.join(OP_KINDS)}")
    target = target or {}
    params = params or {}
    k = op_count(source)
    body, record = _op_body(op, target, params, metrics)
    header = f"{OP_MARK} {op} " + json.dumps(record, sort_keys=True)
    lines = [header,
             f"_mises_prev_{k} = build",
             f"def build(v, __prev=_mises_prev_{k}):",
             "    import build123d as _m",
             "    _p = __prev(v)",
             "    _p = _p.part if hasattr(_p, 'part') else _p"]
    lines += ["    " + ln for ln in body]
    lines.append("    return _p")
    return source.rstrip("\n") + "\n\n\n" + "\n".join(lines) + "\n"


def _op_body(op: str, target: dict, params: dict, metrics: dict) -> tuple[list[str], dict]:
    if op == "hole":
        return _hole(target, params, metrics)
    if op in ("fillet", "chamfer"):
        return _round(op, target, params, metrics)
    if op == "shell":
        return _shell(target, params, metrics)
    return _extrude(target, params, metrics)


def _hole(target: dict, params: dict, metrics: dict) -> tuple[list[str], dict]:
    f = _face(metrics, target.get("face_id"))
    c, n = f["center"], f["normal"]
    d = _num(params.get("diameter"), "diameter", positive=True)
    through = bool(params.get("through", True))
    depth = None if through else _num(params.get("depth"), "depth", positive=True)
    height = _diag(metrics) if through else depth
    align_z = "_m.Align.CENTER" if through else "_m.Align.MAX"
    body = [f"_pl = _m.Plane(origin={tuple(c)}, z_dir={tuple(n)})",
            f"_p = _p - _pl * _m.Cylinder(radius={d / 2.0!r}, height={float(height)!r}, "
            f"align=(_m.Align.CENTER, _m.Align.CENTER, {align_z}))"]
    record = {"face_id": target.get("face_id"), "center": c, "normal": n, "diameter": d,
              "through": through, "depth": depth}
    cb_d = params.get("counterbore_d")
    if cb_d is not None:
        cbd = _num(cb_d, "counterbore_d", positive=True)
        cbdepth = _num(params.get("counterbore_depth"), "counterbore_depth", positive=True)
        if cbd <= d:
            raise _bad("counterbore_d must be larger than diameter")
        body.append(f"_p = _p - _pl * _m.Cylinder(radius={cbd / 2.0!r}, height={cbdepth!r}, "
                    "align=(_m.Align.CENTER, _m.Align.CENTER, _m.Align.MAX))")
        record.update({"counterbore_d": cbd, "counterbore_depth": cbdepth})
    return body, record


def _round(op: str, target: dict, params: dict, metrics: dict) -> tuple[list[str], dict]:
    idx = _edge_indices(metrics, target.get("edge_ids"))
    amount = _num(params.get("radius" if op == "fillet" else "length"),
                  "radius" if op == "fillet" else "length", positive=True)
    fn, kw = ("fillet", "radius") if op == "fillet" else ("chamfer", "length")
    body = [f"_edges = [_p.edges()[i] for i in {idx!r} if i < len(_p.edges())]",
            "if not _edges:",
            "    raise ValueError('mises-op {}: no such edges after regeneration')".format(op),
            f"_p = _m.{fn}(_edges, {kw}={amount!r})"]
    return body, {"edge_ids": target.get("edge_ids"), "edge_indices": idx, kw: amount}


def _shell(target: dict, params: dict, metrics: dict) -> tuple[list[str], dict]:
    idx = _face_indices(metrics, target.get("face_ids"))
    t = _num(params.get("thickness"), "thickness", positive=True)
    body = [f"_faces = [_p.faces()[i] for i in {idx!r} if i < len(_p.faces())]",
            "if not _faces:",
            "    raise ValueError('mises-op shell: no such open faces after regeneration')",
            f"_p = _m.offset(_p, amount={-t!r}, openings=_faces)"]
    return body, {"face_ids": target.get("face_ids"), "face_indices": idx, "thickness": t}


def _extrude(target: dict, params: dict, metrics: dict) -> tuple[list[str], dict]:
    f = _face(metrics, target.get("face_id"))
    c, n = f["center"], f["normal"]
    pts = params.get("profile")
    if not (isinstance(pts, list) and len(pts) >= 3):
        raise _bad("extrude needs a profile of >= 3 [x, y] points (in mm, on the face plane)",
                   "the sketch's closed outline is resolved into this profile")
    poly = [[_num(p[0], "profile.x"), _num(p[1], "profile.y")] for p in pts]
    dist = _num(params.get("distance"), "distance")
    mode = params.get("mode", "add")
    if mode not in ("add", "cut"):
        raise _bad("mode must be 'add' or 'cut'")
    pts_src = ", ".join(f"({x!r}, {y!r})" for x, y in poly)
    sign = "+" if mode == "add" else "-"
    body = [f"_pl = _m.Plane(origin={tuple(c)}, z_dir={tuple(n)})",
            f"_prof = _pl * _m.Polygon({pts_src}, align=None)",
            f"_p = _p {sign} _m.extrude(_prof, amount={dist!r})"]
    return body, {"face_id": target.get("face_id"), "center": c, "normal": n,
                  "profile": poly, "distance": dist, "mode": mode,
                  "sketch_id": params.get("sketch_id")}
