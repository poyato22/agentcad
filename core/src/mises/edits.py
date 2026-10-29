"""Versioned part edits shared by the user (UI calls) and agents.

A part carries a small integer `version`. Every successful edit — a user vars or source change,
an agent rebuild, or a feature op — bumps it. A caller whose `base_version` no longer matches
gets a conflict and must re-read: the user's edits and the agent's edits never silently clobber
each other, and they share one source of truth (part.py).

The version lives in ``<home>/part_versions/<part_id>.json``, outside the part dir: the sandboxed
build may write anywhere in the part dir, so a hostile script must not be able to reset it.

User edits are also appended to a global, append-only log (``home/edits.jsonl``) so an agent's
context can show the last few as data. Feature ops are recorded by appending a ``# mises-op``
block to part.py. Each block wraps the current ``build`` and re-finds its target in the CURRENT
shape at every regeneration from a signature recorded when the op was added (type, normal or
direction, centroid, area or length; see cad/worker.py `_mises_face` / `_mises_edge`), so an op
follows its target when a variable or the base script changes, and fails loudly (naming the op)
when the target is gone or ambiguous.
"""
from __future__ import annotations

import ast
import json
import re
import shlex
import time
from pathlib import Path
from typing import Any, Literal

from .types import ErrorCode, MisesError

VERSIONS_DIR = "part_versions"
EDIT_LOG = "edits.jsonl"
OP_MARK = "# mises-op"
MAX_LOG_LINES = 2000
RECENT_USER_EDITS = 10

OpKind = Literal["hole", "fillet", "chamfer", "shell", "extrude"]
OP_KINDS: tuple[str, ...] = ("hole", "fillet", "chamfer", "shell", "extrude")


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


# ---- version (per part, in a host-only meta dir the build sandbox cannot write)

META_DIR = ".mises-meta"     # <home>/parts/.mises-meta/<part_id>/ — never a valid part id
VERSION_NAME = "version.json"


def meta_dir(part_dir: Path) -> Path:
    """<home>/parts/.mises-meta/<id>: a sibling of the part dir, outside the sandbox's only
    writable path (the part dir itself), so a build script can neither write nor link it."""
    d = Path(part_dir)
    return d.parent.resolve() / META_DIR / d.name


def version_path(part_dir: Path) -> Path:
    return meta_dir(part_dir) / VERSION_NAME


def _parse_version(raw: bytes | None) -> int | None:
    try:
        v = json.loads(raw).get("version") if raw is not None else None
    except (ValueError, AttributeError):
        return None
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def read_version(part_dir: Path) -> int:
    """The part's stored version, or 0 when it has none yet (a brand-new part, or one built
    before versioning). The first successful edit bumps it to 1. Files from earlier layouts
    (<home>/part_versions/<id>.json, <part dir>/version.json) are migrated once."""
    from .cad import safe_io
    try:
        v = _parse_version(safe_io.read_bytes(meta_dir(part_dir), VERSION_NAME))
    except (OSError, safe_io.UnsafePath):
        v = None
    if v is not None:
        return v
    return _migrate_version(part_dir) or 0


def _migrate_version(part_dir: Path) -> int | None:
    """Move a version from the host-only <home>/part_versions/<id>.json (fix round 1) into the
    meta dir, once. The older <part dir>/version.json is NOT a source: the build sandbox can
    write it, so a script could choose its own version. It is simply ignored (versions of such
    parts restart at 0, which only ever makes a stale base_version conflict)."""
    from .cad import safe_io
    d = Path(part_dir)
    old = d.parent.resolve().parent / VERSIONS_DIR
    try:
        found = _parse_version(safe_io.read_bytes(old, f"{d.name}.json"))
    except (OSError, safe_io.UnsafePath):
        return None
    if found is None:
        return None
    try:
        write_version(d, found)
        safe_io.unlink(old, f"{d.name}.json")
    except (OSError, safe_io.UnsafePath):
        pass
    return found


def write_version(part_dir: Path, version: int) -> None:
    """Atomic (exclusive temp file + rename, links never followed): a torn write must never
    read back as an older version."""
    from .cad import safe_io
    md = meta_dir(part_dir)
    safe_io.ensure_dir(md)
    safe_io.write_text(md, VERSION_NAME, json.dumps({"version": int(version)}))


def check_base_version(part_dir: Path, base_version: int | None) -> int | None:
    """Return the current version if `base_version` is stale (a conflict), else None.
    `base_version` None means the caller makes no claim and no conflict is raised."""
    current = read_version(part_dir)
    if base_version is not None and int(base_version) != current:
        return current
    return None


# ---- global user-edit log (per home)

def log_user_edit(home: Path, part_id: str, *, version: int, summary: str,
                  run_id: str | None, project_id: str | None = None) -> None:
    """Append one line to the global user-edit log, trimming it when it grows large."""
    path = Path(home) / EDIT_LOG
    rec = {"ts": time.time(), "part_id": part_id, "project_id": project_id,
           "version": int(version), "by": "user", "summary": summary[:500], "run_id": run_id}
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


def recent_user_edits(home: Path, limit: int = RECENT_USER_EDITS, *, part_id: str | None = None,
                      project_id: str | None = None) -> list[dict]:
    """The most recent user edits (oldest first within the window), as data for an agent.
    Filtered to one part and/or one build when given; an edit logged without a build id (from
    before builds existed) is kept by the build filter."""
    path = Path(home) / EDIT_LOG
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("by") != "user":
            continue
        if part_id is not None and rec.get("part_id") != part_id:
            continue
        if project_id is not None and rec.get("project_id") not in (None, project_id):
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return list(reversed(out))


# ---- the vars schema: `# mises: min= max= step= unit= doc=` on VARS lines, else inferred

_MM_WORDS = ("thickness", "wall", "width", "height", "length", "diameter", "radius", "depth")
_ANNOT_RE = re.compile(r"#\s*mises:\s*(.*)$")
_ANNOT_KEYS = {"min": float, "max": float, "step": float, "unit": str, "doc": str}


def _annotations(source: str) -> dict[str, dict]:
    """{var name: annotation} from the module-level VARS dict literal, parsed with `ast` (the
    script is never executed here). A key's annotation is the trailing comment on its line."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    lines = source.splitlines()
    out: dict[str, dict] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "VARS" for t in node.targets)):
            continue
        for key in node.value.keys:
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            line = lines[key.lineno - 1] if 0 < key.lineno <= len(lines) else ""
            m = _ANNOT_RE.search(line)
            if not m:
                continue
            ann: dict[str, Any] = {}
            try:
                tokens = shlex.split(m.group(1))
            except ValueError:
                tokens = m.group(1).split()
            for tok in tokens:
                k, sep, v = tok.partition("=")
                if sep and k in _ANNOT_KEYS:
                    try:
                        ann[k] = _ANNOT_KEYS[k](v)
                    except ValueError:
                        continue
            out[key.value] = ann
    return out


def vars_schema(source: str, values: dict) -> list[dict]:
    """[{name, value, min, max, step?, unit?, doc?, inferred}] for the part's vars. Annotated
    vars use their annotation; others get a soft inferred range (value/4 .. value*4, mm for
    length-like names) flagged `inferred: true` so the UI can show it as a suggestion."""
    ann = _annotations(source)
    out = []
    for name, value in values.items():
        a = ann.get(name)
        if a is not None:
            row = {"name": name, "value": value, **a, "inferred": False}
            row.setdefault("min", None)
            row.setdefault("max", None)
        else:
            v = float(value)
            lo, hi = sorted((v / 4.0, v * 4.0)) if v != 0 else (0.0, 10.0)
            row = {"name": name, "value": value, "min": lo, "max": hi, "inferred": True}
            if any(w in name.lower() for w in _MM_WORDS):
                row["unit"] = "mm"
        out.append(row)
    return out


# ---- feature ops: append a `# mises-op` block to the source

def op_count(source: str) -> int:
    return sum(1 for ln in source.splitlines() if ln.startswith(OP_MARK))


def op_blocks(source: str) -> str:
    """Everything from the first `# mises-op` block on ("" when there are none)."""
    lines = source.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith(OP_MARK):
            return "\n".join(lines[i:]) + "\n"
    return ""


_RESOLVE_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<var>_f|_edges|_faces) = (?P<fn>_mises_face|_mises_edges|_mises_faces)"
    r"\(_p, (?P<sig>.+), '(?P<label>mises-op (?P<k>\d+) [a-z]+)'\)$")
_TYPE_RE = re.compile(r"^[a-z_]{1,32}$")


def _fin(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and abs(f) < 1e9 else None


def _vec3(v: Any) -> list[float] | None:
    if not (isinstance(v, list) and len(v) == 3):
        return None
    out = [_fin(c) for c in v]
    return None if any(c is None for c in out) else out


def _clean_sig(new: Any, old: dict, edge: bool) -> dict | None:
    """A refreshed signature from the (untrusted) worker, rebuilt from validated numbers only,
    or None. The old hint is kept; nothing else of the worker's data reaches the source."""
    if not isinstance(new, dict) or not isinstance(new.get("type"), str):
        return None
    if not _TYPE_RE.match(new["type"]) or new["type"] != old.get("type"):
        return None
    c = _vec3(new.get("center"))
    if c is None:
        return None
    if edge:
        dirn, length = _vec3(new.get("direction")), _fin(new.get("length"))
        adj = new.get("adjacent")
        if dirn is None or length is None or length < 0 or not isinstance(adj, list) or len(adj) > 8:
            return None
        adj_ok = [_vec3(n) for n in adj]
        if any(n is None for n in adj_ok):
            return None
        return {"type": new["type"], "direction": dirn, "center": c, "length": length,
                "adjacent": adj_ok, "hint": old.get("hint")}
    normal, area = _vec3(new.get("normal")), _fin(new.get("area"))
    if normal is None or area is None or area < 0:
        return None
    return {"type": new["type"], "normal": normal, "center": c, "area": area,
            "hint": old.get("hint")}


def refresh_op_refs(source: str, refs: Any) -> str:
    """Rewrite each op's recorded target signature(s) with what they resolved to in the last
    successful build, so the strict match is applied per edit and small steps don't add up to a
    failure. Only lines in the generated form are touched; anything malformed is left as is."""
    if not isinstance(refs, dict):
        return source
    lines = source.splitlines()
    headers = [i for i, ln in enumerate(lines) if ln.startswith(OP_MARK)]
    changed = False
    for i, ln in enumerate(lines):
        m = _RESOLVE_LINE.match(ln)
        if not m or m.group("label") not in refs:
            continue
        try:
            old = ast.literal_eval(m.group("sig"))
        except (ValueError, SyntaxError):
            continue
        new = refs[m.group("label")]
        plural = m.group("fn") != "_mises_face"
        olds = old if plural else [old]
        if not (isinstance(olds, list) and isinstance(new, list) and len(new) == len(olds)
                and all(isinstance(o, dict) for o in olds)):
            continue
        edge = m.group("fn") == "_mises_edges"
        cleaned = [_clean_sig(n, o, edge) for n, o in zip(new, olds)]
        if any(c is None for c in cleaned):
            continue
        sig = cleaned if plural else cleaned[0]
        lines[i] = (f"{m.group('indent')}{m.group('var')} = {m.group('fn')}(_p, {sig!r}, "
                    f"{m.group('label')!r})")
        k = int(m.group("k"))
        if k < len(headers):
            lines[headers[k]] = _rewrite_header(lines[headers[k]], sig, plural)
        changed = True
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "") if changed else source


def _rewrite_header(header: str, sig: Any, plural: bool) -> str:
    parts = header.split(" ", 3)            # "#", "mises-op", "<op>", "<json>"
    if len(parts) != 4:
        return header
    try:
        record = json.loads(parts[3])
    except ValueError:
        return header
    record["targets" if plural else "target"] = sig
    return " ".join(parts[:3]) + " " + json.dumps(record, sort_keys=True)


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
    for f in metrics.get("face_list") or []:
        if f.get("id") == face_id:
            if not f.get("normal"):
                raise _bad(f"face {face_id!r} has no usable normal", "pick a flat face")
            return f
    raise _bad(f"unknown face id {face_id!r}",
               hint="list faces with mirror_faces and use an id like 'f0'")


def _face_sig(f: dict) -> dict:
    return {"type": f.get("type"), "normal": f["normal"], "center": f["center"],
            "area": f.get("area"), "hint": f.get("id")}


def _edge_sig(e: dict) -> dict:
    if not e.get("direction") or not e.get("center"):
        raise _bad(f"edge {e.get('id')!r} has no usable direction",
                   "rebuild the part so its edges carry directions, then pick again")
    return {"type": e.get("type"), "direction": e["direction"], "center": e["center"],
            "length": e.get("length"), "adjacent": e.get("adjacent") or [], "hint": e.get("id")}


def _edges(metrics: dict, edge_ids: Any) -> list[dict]:
    by_id = {e.get("id"): e for e in metrics.get("edge_list") or []}
    if not isinstance(edge_ids, list) or not edge_ids:
        raise _bad("edge_ids must be a non-empty list of edge ids like 'e3'")
    missing = [eid for eid in edge_ids if eid not in by_id]
    if missing:
        raise _bad(f"unknown edge id {missing[0]!r}", "list edges with mirror_edges")
    return [_edge_sig(by_id[eid]) for eid in edge_ids]


def _faces(metrics: dict, face_ids: Any) -> list[dict]:
    if not isinstance(face_ids, list) or not face_ids:
        raise _bad("face_ids must be a non-empty list of face ids like 'f0'")
    return [_face_sig(_face(metrics, fid)) for fid in face_ids]


def _offset(params: dict, face: dict) -> list[float]:
    """The pick point as a WORLD-space vector from the face centroid, with its component along
    the face normal dropped. It is applied to the re-found face's centroid as the same world
    vector: exact while the face only moves or resizes; a face that rotates fails the 5 deg normal
    check instead. Defaults to the centroid."""
    if params.get("point") is None:
        return [0.0, 0.0, 0.0]
    p = _vec(params["point"], "point")
    c, n = face["center"], face["normal"]
    d = [p[i] - c[i] for i in range(3)]
    k = sum(d[i] * n[i] for i in range(3))
    return [round(d[i] - k * n[i], 6) + 0.0 for i in range(3)]


def build_op_block(source: str, op: str, target: dict, params: dict, metrics: dict) -> str:
    """Return the source with one `# mises-op` block appended for `op`. The target named by ids
    from the current metrics is recorded as a signature that is re-resolved at every
    regeneration, so the op stays on its face/edge when the part changes."""
    if op not in OP_KINDS:
        raise _bad(f"unknown op {op!r}", f"ops: {', '.join(OP_KINDS)}")
    target = target or {}
    params = params or {}
    k = op_count(source)
    label = f"mises-op {k} {op}"
    body, record = _op_body(op, target, params, metrics, label)
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


def _op_body(op: str, target: dict, params: dict, metrics: dict,
             label: str) -> tuple[list[str], dict]:
    if op == "hole":
        return _hole(target, params, metrics, label)
    if op in ("fillet", "chamfer"):
        return _round(op, target, params, metrics, label)
    if op == "shell":
        return _shell(target, params, metrics, label)
    return _extrude(target, params, metrics, label)


def _face_plane(sig: dict, offset: list[float], label: str) -> list[str]:
    """Code that finds the target face now and builds `_pl` on it at the recorded offset (a
    world vector from the centroid), with the x direction of the face's Plane and its outward
    normal as z. The pick point must still lie on that face."""
    return [f"_f = _mises_face(_p, {sig!r}, {label!r})",
            "_c = _f.center(_m.CenterOf.MASS)",
            f"_o = _c + _m.Vector{tuple(offset)!r}",
            f"_mises_on_face(_f, _o, {label!r})",
            "_pl = _m.Plane(origin=_o, x_dir=_m.Plane(_f).x_dir, z_dir=_f.normal_at(_c))"]


def _hole(target: dict, params: dict, metrics: dict, label: str) -> tuple[list[str], dict]:
    f = _face(metrics, target.get("face_id"))
    sig, off = _face_sig(f), _offset(params, f)
    d = _num(params.get("diameter"), "diameter", positive=True)
    through = bool(params.get("through", True))
    depth = None if through else _num(params.get("depth"), "depth", positive=True)
    body = _face_plane(sig, off, label)
    if through:
        # through stays through: sized from the CURRENT shape, centred on the face
        body.append(f"_p = _p - _pl * _m.Cylinder(radius={d / 2.0!r}, "
                    "height=2.0 * _p.bounding_box().diagonal, "
                    "align=(_m.Align.CENTER, _m.Align.CENTER, _m.Align.CENTER))")
    else:
        # blind: `depth` measured into the material from the face, wherever the face is now
        body.append(f"_p = _p - _pl * _m.Cylinder(radius={d / 2.0!r}, height={depth!r}, "
                    "align=(_m.Align.CENTER, _m.Align.CENTER, _m.Align.MAX))")
    record = {"target": sig, "offset": off, "diameter": d, "through": through, "depth": depth}
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


def _round(op: str, target: dict, params: dict, metrics: dict,
           label: str) -> tuple[list[str], dict]:
    sigs = _edges(metrics, target.get("edge_ids"))
    kw = "radius" if op == "fillet" else "length"
    amount = _num(params.get(kw), kw, positive=True)
    body = [f"_edges = _mises_edges(_p, {sigs!r}, {label!r})",
            f"_p = _m.{op}(_edges, {kw}={amount!r})"]
    return body, {"targets": sigs, kw: amount}


def _shell(target: dict, params: dict, metrics: dict, label: str) -> tuple[list[str], dict]:
    sigs = _faces(metrics, target.get("face_ids"))
    t = _num(params.get("thickness"), "thickness", positive=True)
    body = [f"_faces = _mises_faces(_p, {sigs!r}, {label!r})",
            f"_p = _m.offset(_p, amount={-t!r}, openings=_faces)"]
    return body, {"targets": sigs, "thickness": t}


def _extrude(target: dict, params: dict, metrics: dict, label: str) -> tuple[list[str], dict]:
    f = _face(metrics, target.get("face_id"))
    sig, off = _face_sig(f), _offset(params, f)
    pts = params.get("profile")
    if not (isinstance(pts, list) and len(pts) >= 3):
        raise _bad("extrude needs a profile of >= 3 [x, y] points (in mm, on the face plane)",
                   "the sketch's closed outline is resolved into this profile")
    poly = []
    for p in pts:
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise _bad("profile points must be [x, y] pairs")
        poly.append([_num(p[0], "profile.x"), _num(p[1], "profile.y")])
    dist = _num(params.get("distance"), "distance")
    mode = params.get("mode", "add")
    if mode not in ("add", "cut"):
        raise _bad("mode must be 'add' or 'cut'")
    pts_src = ", ".join(f"({x!r}, {y!r})" for x, y in poly)
    sign = "+" if mode == "add" else "-"
    body = _face_plane(sig, off, label) + [
        f"_prof = _pl * _m.Polygon({pts_src}, align=None)",
        f"_p = _p {sign} _m.extrude(_prof, amount={dist!r})"]
    return body, {"target": sig, "offset": off, "profile": poly, "distance": dist,
                  "mode": mode, "sketch_id": params.get("sketch_id")}
