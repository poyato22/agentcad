"""Builds (projects), the part tree and run/agent management (Plan 5 C1).

The store holds the rows; this module is the business logic the API and runtime call:
reconciling on-disk parts into tree rows, cycle-safe re-parenting, build part/run counts,
delete-safety, run cleanup and the orchestrator run tree.

Geometry, vars and metrics never live in the database. A part's build output stays on disk
under home/parts/<id>/ (vars.json, metrics.json, model.glb); the DB row carries only the tree
placement (label, parent, order, color, kind, version) the user and agents edit.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import budget
from .cad.tools import PART_ID_RE
from .store import AgentRun, Part, Project, Store
from .types import ErrorCode, MisesError

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
THUMBS_DIR = ".mises-thumbs"   # mirrors cad.thumbs.THUMBS_DIR (kept here: no cad import cycle)
# runs whose cleanup is allowed: only ones that have finished one way or another
TERMINAL = ("done", "failed", "cancelled")


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


# ---- builds (projects)

_EDIT_FILES = ("model.stl", "model.glb", "vars.json", "part.py")


def _part_edit_mtime(home: Path, part_id: str) -> float | None:
    """When the part's build output last changed (newest of its model/vars/script files),
    stat'ed without following links; None when it has no build on disk."""
    root = _parts_root(home)
    real = _contained(root, root / part_id) if root.is_dir() else None
    if real is None or not real.is_dir():
        return None
    best = None
    for name in _EDIT_FILES:
        try:
            st = os.lstat(real / name)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            best = st.st_mtime if best is None else max(best, st.st_mtime)
    return best


def last_edited_at(store: Store, p: Project, home: Path | None = None) -> float:
    """When the build was really last worked on: the newest of its own row change (rename,
    trash/restore), any of its parts' build output and any of its runs' activity."""
    ts = [p.updated_at, p.created_at]
    run = store.project_last_run_activity(p.id)
    if run is not None:
        ts.append(run)
    if home is not None:
        for row in store.list_part_rows(p.id):
            m = _part_edit_mtime(home, row.id)
            if m is not None:
                ts.append(m)
    return float(max(t for t in ts if t is not None))


def project_view(store: Store, p: Project, home: Path | None = None) -> dict:
    """The GET /projects shape. onshape stays null until C5's import links a document.
    `last_edited_at` is derived (see last_edited_at); `updated_at` keeps its row meaning."""
    return {"id": p.id, "name": p.name, "created_at": p.created_at, "updated_at": p.updated_at,
            "last_edited_at": last_edited_at(store, p, home),
            "trashed_at": p.trashed_at, "part_count": store.project_part_count(p.id),
            "run_count": store.project_run_count(p.id), "onshape": None}


def list_projects(store: Store, home: Path | None = None) -> list[dict]:
    return [project_view(store, p, home) for p in store.list_projects()]


def _live(store: Store, project_id: str | None) -> bool:
    if not project_id:
        return False
    try:
        return store.get_project(project_id).trashed_at is None
    except KeyError:
        return False


def active_project_id(store: Store, profile_active: str | None) -> str:
    """The build a project-less request belongs to: the profile's active build when it exists
    and is not in the Trash; else the Default build; else the oldest live build; else a fresh
    Default (the recorded Default was trashed or purged)."""
    if _live(store, profile_active):
        return profile_active  # type: ignore[return-value]
    default = store.default_project_id()
    if _live(store, default):
        return default  # type: ignore[return-value]
    for p in store.list_projects(include_trashed=False):
        return p.id
    if default is not None:  # every build is in the Trash: start a new Default beside it
        fresh = store.create_project("Default")
        store.meta_set("default_project_id", fresh.id)
        return fresh.id
    return store.ensure_default_project().id


def check_project(store: Store, project_id: str) -> str:
    """A caller-supplied build id: 404 when unknown, 400 when it is in the Trash."""
    p = _project_or_404(store, project_id)
    if p.trashed_at is not None:
        raise _bad(f"build {p.name!r} is in the Trash", hint="restore it first")
    return project_id


def create_project(store: Store, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise _bad("a build needs a name")
    if len(name) > 80:
        raise _bad("build name must be 80 characters or fewer")
    return project_view(store, store.create_project(name))


def _project_or_404(store: Store, project_id: str) -> Project:
    try:
        return store.get_project(project_id)
    except KeyError:
        raise MisesError(ErrorCode.NOT_FOUND, f"unknown build: {project_id}") from None


def rename_project(store: Store, project_id: str, name: str) -> dict:
    _project_or_404(store, project_id)
    name = (name or "").strip()
    if not name:
        raise _bad("a build needs a name")
    return project_view(store, store.update_project(project_id, name=name))


def trash_project(store: Store, project_id: str) -> dict:
    _project_or_404(store, project_id)
    return project_view(store, store.update_project(project_id, trashed_at=time.time()))


def restore_project(store: Store, project_id: str) -> dict:
    _project_or_404(store, project_id)
    return project_view(store, store.update_project(project_id, trashed_at=None))


class TrashBusy(MisesError):
    """Empty Trash refused: a trashed build still has live work (409 {reason, run_ids})."""

    def __init__(self, reason: str, run_ids: list[str], project_ids: list[str]):
        super().__init__(ErrorCode.INVALID_INPUT,
                         f"a build in the Trash still has {reason.replace('_', ' ')} runs",
                         hint="wait for them to finish, or cancel them, then empty the Trash")
        self.reason, self.run_ids, self.project_ids = reason, run_ids, project_ids

    def body(self) -> dict:
        return {"reason": self.reason, "run_ids": self.run_ids,
                "project_ids": self.project_ids, "message": self.message, "hint": self.hint}


def trash_blockers(store: Store, project_ids: list[str],
                   busy: BusyFn | None = None) -> tuple[str, list[str]] | None:
    """Why these builds cannot be purged yet, as (reason, run ids), or None. The same rule as
    run delete: a run that is running, queued, busy or awaiting approval blocks. The app's own
    per-build `ui` run is always "running" (long-lived), so it blocks only while one of its UI
    calls is in flight (`busy`) or it has a pending approval (e.g. a catalog-import card)."""
    running: list[str] = []
    pending: list[str] = []
    for pid in project_ids:
        for r in store.runs_in_project(pid):
            if r.provider == "ui":
                if store.pending_approvals(r.id):
                    pending.append(r.id)
                elif busy is not None and busy(r.id):
                    running.append(r.id)
                continue
            why = _blocked(store, r, busy)
            if why == "running":
                running.append(r.id)
            elif why is not None:
                pending.append(r.id)
    if running:
        return "running", running + pending
    if pending:
        return "pending_approval", pending
    return None


def _rm_tree(parent: Path, name: str) -> None:
    """Remove parent/name without following links: a link in its place is unlinked itself,
    a directory goes through rmtree (fd-based on macOS: never follows a link inside)."""
    from .cad import safe_io
    try:
        root = safe_io.real_dir(parent / name)
        st = os.lstat(root)
    except OSError:
        return
    try:
        if stat.S_ISDIR(st.st_mode):
            shutil.rmtree(root, ignore_errors=True)
        else:
            os.unlink(root)
    except OSError:
        pass


def _purge_files(store: Store, home: Path, project_id: str, part_ids: list[str],
                 study_ids: list[str], assembly: Any) -> list[str]:
    """Delete what the purged build owned on disk: its parts' dirs and host-only meta (unless
    another build still references the part from its assembly), and its studies' scratch."""
    from . import edits
    root = _parts_root(home)
    shared: set[str] = set()
    if assembly is not None:
        shared = assembly.part_refs_outside(project_id)
    removed = []
    for pid in part_ids:
        if not PART_ID_RE.fullmatch(pid) or pid in shared:
            continue
        _rm_tree(root, pid)
        _rm_tree(root / edits.META_DIR, pid)
        _rm_tree(root / THUMBS_DIR, pid)            # a reused id must never get these back
        _rm_tree(Path(home) / edits.VERSIONS_DIR, f"{pid}.json")
        removed.append(pid)
    _rm_tree(root / THUMBS_DIR, f"project-{project_id}")
    for sid in study_ids:
        if re.fullmatch(r"study_[0-9a-f]{1,32}", sid):
            _rm_tree(Path(home) / "studies", sid)
    # the build's reference images and drawing imports (the user's original files)
    for sub, record in (("references", "meta.json"), ("imports", "import.json")):
        for name in _owned_by(Path(home) / sub, record, project_id):
            _rm_tree(Path(home) / sub, name)
    return removed


def delete_run_renders(home: Path, run_id: str) -> int:
    """Remove a deleted run's renders (`<home>/renders/<prefix>_<run8>_<hex10>_<view>.png`,
    tagged by cad.views._tag): GET /renders/{id} stops serving them. Links are unlinked, never
    followed. Returns how many went."""
    run8 = re.sub(r"[^A-Za-z0-9]", "", run_id)[:8]
    if not run8:
        return 0
    pat = re.compile(rf"^[A-Za-z0-9]+_{re.escape(run8)}_[0-9a-f]{{10}}(_[A-Za-z0-9_-]*)?\.png$")
    d = Path(home) / "renders"
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    n = 0
    for name in names:
        if pat.match(name):
            try:
                os.unlink(d / name)
                n += 1
            except OSError:
                pass
    return n


def _owned_by(parent: Path, record: str, project_id: str) -> list[str]:
    """Names of parent's entries whose JSON `record` says they belong to `project_id` (read
    without following links)."""
    from .cad import safe_io
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return []
    out = []
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or os.path.islink(parent / name):
            continue
        try:
            meta = safe_io.read_json(parent / name, record)
        except (OSError, ValueError, safe_io.UnsafePath):
            continue
        if isinstance(meta, dict) and meta.get("project_id") == project_id:
            out.append(name)
    return out


def empty_trash(store: Store, home: Path | None = None, *, assembly: Any = None,
                busy: BusyFn | None = None) -> dict:
    """Purge every trashed build and everything it owns: part-tree rows, spec, studies,
    build-scoped catalog items, assembly rows and (with `home`) its parts' files, meta and
    study scratch. Refused as a whole (TrashBusy -> 409) while any trashed build has a run
    that is running, queued, busy or awaiting approval. Runs stay as history until cleaned up."""
    purged = [p.id for p in store.list_projects() if p.trashed_at is not None]
    blocked = trash_blockers(store, purged, busy)
    if blocked is not None:
        raise TrashBusy(blocked[0], blocked[1], purged)
    removed: list[str] = []
    for pid in purged:
        part_ids = [r.id for r in store.list_part_rows(pid)]
        study_ids = [s.get("id") for s in store.list_studies(pid) if isinstance(s.get("id"), str)]
        if home is not None:
            removed += _purge_files(store, home, pid, part_ids, study_ids, assembly)
        if assembly is not None:
            assembly.purge(pid)
        store.purge_project(pid)
    return {"purged": purged, "count": len(purged)}


# ---- part tree

def _parts_root(home: Path) -> Path:
    return Path(home) / "parts"


def _contained(root: Path, p: Path) -> Path | None:
    try:
        real = p.resolve(strict=True)
        if real.is_relative_to(root.resolve(strict=True)):
            return real
    except (OSError, RuntimeError):
        pass
    return None


def _safe_json(d: Path, name: str):
    from .cad import safe_io
    try:
        return safe_io.read_json(d, name)
    except (OSError, ValueError, safe_io.UnsafePath):
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def register_legacy_parts(store: Store, home: Path) -> int:
    """Once per database (meta flag): give every part built before builds existed a tree row
    in the Default build. Parts built later get their row when they are first built, in the
    creating run's build (register_built_part), so nothing is ever claimed lazily."""
    if store.meta_get("legacy_parts_registered"):
        return 0
    n = 0
    root = _parts_root(home)
    if root.is_dir():
        known = {p.id for p in store.list_part_rows()}
        for d in sorted(root.iterdir(), key=lambda p: p.name):
            if not PART_ID_RE.fullmatch(d.name) or d.name in known:
                continue
            real = _contained(root, d)
            if real is None or not real.is_dir() or not (real / "vars.json").exists():
                continue
            store.register_part(d.name, store.ensure_default_project().id)
            n += 1
    store.meta_set("legacy_parts_registered", "1")
    return n


def register_built_part(store: Store, run_id: str, part_id: str) -> None:
    """Called when a part is first built: its tree row goes in the creating run's build.
    A rebuild keeps the existing row (and its build)."""
    if store.get_part(part_id) is not None:
        return
    try:
        project_id = store.get_run(run_id).project_id
    except KeyError:
        project_id = None
    store.register_part(part_id, project_id or store.ensure_default_project().id)


def _part_disk(home: Path, part_id: str) -> dict | None:
    """vars, metrics and mtime from the part's on-disk build; None when it has no build."""
    root = _parts_root(home)
    real = _contained(root, root / part_id) if root.is_dir() else None
    if real is None or not real.is_dir():
        return None
    # the build sandbox can write the part dir: read through safe_io (a link left there by a
    # racing build is refused, never followed to e.g. server.json), and stat without following
    vars_ = _safe_json(real, "vars.json")
    metrics = _safe_json(real, "metrics.json")
    mtimes = []
    for p in real.iterdir():
        try:
            st = p.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            mtimes.append(st.st_mtime)
    return {"vars": vars_ if isinstance(vars_, dict) else {},
            "metrics": metrics if isinstance(metrics, dict) else None,
            "mtime": float(max(mtimes)) if mtimes else real.stat().st_mtime}


_NO_DISK = {"vars": {}, "metrics": None, "mtime": None}


def part_version(home: Path, row: Part) -> int:
    """The part's version. Source of truth: C6's host-only meta file
    (parts/.mises-meta/<id>/version.json), bumped by every successful edit. A part built before
    versioning has no meta version (read_version gives 0) while the parts.version column (and the
    UI) show row.version (>= 1): migrate the column value into the meta once, so the edit guard
    checks the same number the UI listed and the first user edit is not a spurious 409."""
    from . import edits
    root = _parts_root(home)
    v = edits.read_version(root / row.id)
    if v == 0 and row.version > 0:
        real = _contained(root, root / row.id)
        if real is not None and real.is_dir():
            try:
                edits.write_version(real, row.version)
                return row.version
            except OSError:
                pass
        return row.version
    return v


def part_view(store: Store, home: Path, row: Part) -> dict:
    d = row.model_dump()
    d["version"] = part_version(home, row)
    # `part_id` is kept beside the contract's `id` so older UI code keeps working
    return {**d, "part_id": row.id, **(_part_disk(home, row.id) or _NO_DISK)}


def list_parts(store: Store, home: Path, project_id: str) -> list[dict]:
    """The build's tree rows. A `part` row whose on-disk build is gone is left out (groups
    and catalog parts have no build directory of their own)."""
    out = []
    for r in store.list_part_rows(project_id):
        disk = _part_disk(home, r.id)
        if disk is None and r.kind == "part":
            continue
        out.append({**r.model_dump(), "version": part_version(home, r), "part_id": r.id,
                    **(disk or _NO_DISK)})
    return out


def _part_or_404(store: Store, part_id: str) -> Part:
    row = store.get_part(part_id)
    if row is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"unknown part: {part_id}")
    return row


def _reject_cycle(store: Store, part_id: str, parent_id: str | None) -> None:
    if parent_id is None:
        return
    if parent_id == part_id:
        raise _bad("a part cannot be its own parent")
    cur, seen = parent_id, set()
    while cur is not None and cur not in seen:
        if cur == part_id:
            raise _bad("that move would create a cycle in the part tree")
        seen.add(cur)
        nxt = store.get_part(cur)
        cur = nxt.parent_id if nxt else None


def _parent_in(store: Store, parent_id: str, project_id: str | None) -> None:
    parent = store.get_part(parent_id)
    if parent is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"unknown parent part: {parent_id}")
    if parent.project_id != project_id:
        raise _bad("the parent part belongs to another build")


def patch_part(store: Store, home: Path, part_id: str, *, label: str | None = None,
               color: str | None | object = ..., parent_id: str | None | object = ...,
               appearance: dict | None | object = ...) -> dict:
    """Rename, recolor, re-material or re-parent a part. `color`/`parent_id`/`appearance` left
    as the sentinel are unchanged; None clears them. The parent must be in the same build, and
    a move that would form a cycle is rejected. The row's color and appearance.color stay equal:
    a new appearance with a color sets the color, a new color recolors the appearance."""
    from . import appearance as _appearance
    row = _part_or_404(store, part_id)
    fields: dict = {}
    if label is not None:
        label = label.strip()
        if not label:
            raise _bad("a part label must not be empty")
        fields["label"] = label
    if color is not ...:
        if color is not None and not _HEX_RE.match(str(color)):
            raise _bad("color must be #rrggbb")
        fields["color"] = color
        if row.appearance and appearance is ...:
            fields["appearance"] = {**_appearance.normalize(row.appearance), "color": color}
    if appearance is not ...:
        if appearance is None:
            fields["appearance"] = None
        else:
            a = _appearance.normalize(appearance)
            # an untinted preset (plastics) left without a color keeps the part's own color
            if a["color"] is None and isinstance(appearance, dict) and "color" not in appearance:
                a["color"] = fields.get("color", row.color)
            fields["appearance"] = a
            if a["color"] is not None:
                fields["color"] = a["color"]
    if parent_id is not ...:
        if parent_id is not None:
            _parent_in(store, str(parent_id), row.project_id)
        _reject_cycle(store, part_id, parent_id)  # type: ignore[arg-type]
        fields["parent_id"] = parent_id
    if fields:
        store.update_part(part_id, **fields)
    return part_view(store, home, _part_or_404(store, part_id))


def reorder_parts(store: Store, home: Path, project_id: str, parent_id: str | None,
                  ids: list[str]) -> list[dict]:
    """Order (and re-parent) siblings. Every id and the parent must belong to project_id."""
    _project_or_404(store, project_id)
    if parent_id is not None:
        _parent_in(store, parent_id, project_id)
    seen: set[str] = set()
    for pid in ids:
        if pid in seen:
            raise _bad(f"part {pid} listed twice in reorder")
        seen.add(pid)
        row = store.get_part(pid)
        if row is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown part: {pid}")
        if row.project_id != project_id:
            raise _bad(f"part {pid} belongs to another build")
        _reject_cycle(store, pid, parent_id)
    store.reorder_parts(project_id, parent_id, ids)
    return list_parts(store, home, project_id)


# ---- the parts manager's menu (Plan 7 U8): duplicate, delete, export

def display_name(row: Part) -> str:
    """The name the app shows (app lib/names.partName): the label, else the id with spaces."""
    label = (row.label or "").strip()
    if label and label != row.id:
        return label
    return re.sub(r"[_-]+", " ", row.id).strip()


def _free_part_id(store: Store, home: Path, base: str) -> str:
    root = _parts_root(home)
    stem = base[:36]
    for n in range(1, 1000):
        cand = f"{stem}_copy" if n == 1 else f"{stem}_copy{n}"
        if PART_ID_RE.fullmatch(cand) and store.get_part(cand) is None \
                and not os.path.lexists(root / cand):
            return cand
    raise _bad(f"too many copies of {base}")


def duplicate_part(store: Store, home: Path, part_id: str) -> dict:
    """A new part with the same build files (script, vars, metrics, exports) and look, named
    "<label> copy", placed right after the original. Only the part dir's own regular files are
    copied (read through safe_io: a link left there is never followed)."""
    from .cad import safe_io
    row = _part_or_404(store, part_id)
    root = _parts_root(home)
    src = _contained(root, root / part_id) if root.is_dir() else None
    if row.kind == "group" or src is None or not src.is_dir():
        raise _bad(f"{part_id} has no build to copy", hint="only built parts can be duplicated")
    new_id = _free_part_id(store, home, part_id)
    dst = root / new_id
    safe_io.ensure_dir(dst)
    for f in sorted(src.iterdir(), key=lambda p: p.name):
        try:
            st = f.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        data = safe_io.read_bytes(src, f.name)
        if data is not None:
            safe_io.write_bytes(dst, f.name, data)
    # the skeleton dependency (host-only meta): the copy was built against the same skeleton
    # version, so a later skeleton_set regenerates it or marks it stale like the original
    from . import edits
    from .cad import skeleton as skeleton_mod
    try:
        refs = safe_io.read_bytes(edits.meta_dir(src), skeleton_mod.REFS_NAME)
    except (OSError, safe_io.UnsafePath):
        refs = None
    if refs is not None:
        md = edits.meta_dir(dst)
        safe_io.ensure_dir(md.parent)
        safe_io.ensure_dir(md)
        safe_io.write_bytes(md, skeleton_mod.REFS_NAME, refs)
    store.register_part(new_id, row.project_id or store.ensure_default_project().id,
                        label=f"{display_name(row)} copy", kind=row.kind)
    store.update_part(new_id, color=row.color, appearance=row.appearance, parent_id=row.parent_id)
    sibs = [p.id for p in store.list_part_rows(row.project_id)
            if p.parent_id == row.parent_id and p.id != new_id]
    at = sibs.index(part_id) + 1 if part_id in sibs else len(sibs)
    sibs.insert(at, new_id)
    store.reorder_parts(row.project_id, row.parent_id, sibs)
    return part_view(store, home, _part_or_404(store, new_id))


class PartBusy(MisesError):
    """Delete refused: an agent run may be building or editing the part (409 {reason, run_ids})."""

    def __init__(self, part_id: str, run_ids: list[str]):
        super().__init__(ErrorCode.INVALID_INPUT,
                         f"an agent is working on {part_id}",
                         hint="wait for it to finish, or stop it, then delete the part")
        self.reason, self.run_ids = "running", run_ids

    def body(self) -> dict:
        return {"error": "invalid_input", "reason": self.reason, "run_ids": self.run_ids,
                "message": self.message, "hint": self.hint, "retryable": False}


def part_blockers(store: Store, row: Part, busy: BusyFn | None = None) -> list[str]:
    """Runs of the part's build that are live (running, queued or busy, the build-delete rule)
    and may edit it: their edit scope is "*" or names the part. A run without a policy counts.
    The app's own `ui` run blocks only while one of its calls is in flight."""
    out: list[str] = []
    if not row.project_id:
        return out
    for r in store.runs_in_project(row.project_id):
        if r.provider == "ui":
            if busy is not None and busy(r.id):
                out.append(r.id)
            continue
        if _blocked(store, r, busy) != "running":
            continue
        try:
            scope = set(store.current_policy(r.id).edit_scope)
        except KeyError:
            scope = {"*"}
        if "*" in scope or row.id in scope:
            out.append(r.id)
    return out


_UNDO_RE = re.compile(r"^([a-z][a-z0-9_]{1,40})\.(\d{1,25})$")


def delete_part(store: Store, home: Path, part_id: str, *, assembly: Any = None,
                busy: BusyFn | None = None, cascade: bool = False,
                ignore_runs: tuple[str, ...] | list[str] = ()) -> dict:
    """Remove a part from its build. Its files move to home/trash/parts/<id>.<time> (nothing is
    destroyed); its children move up to its parent. While the build's assembly uses it the
    delete is refused, unless `cascade` (Plan 9 D29): then its instances (and their joints) go
    too. Refused (PartBusy, 409) while an agent run that may edit it is live (`ignore_runs`:
    the deleting run itself). Returns {deleted, removed_instances, removed_joints, undo}: `undo`
    names the trash entry restore_part puts back (files, tree row, instances and joints)."""
    from .cad import safe_io
    row = _part_or_404(store, part_id)
    blockers = [r for r in part_blockers(store, row, busy) if r not in ignore_runs]
    if blockers:
        raise PartBusy(part_id, blockers)
    used: list = []
    joints: list = []
    if assembly is not None and row.project_id:
        try:
            asm = assembly.get(row.project_id)
        except KeyError:
            asm = None
        used = [i for i in (asm.instances if asm else []) if i.part_id == part_id]
        if used and not cascade:
            raise _bad(f"{part_id} is in the assembly ({', '.join(i.name for i in used[:3])})",
                       hint="remove it from the assembly first")
        ids = {i.id for i in used}
        joints = [j for j in (asm.joints if asm else [])
                  if j.a.instance_id in ids or j.b.instance_id in ids]
    # another build's assembly may place this part too (a "*"-scoped run can): the same
    # guard as Empty Trash, so that build never loses a placed part
    if assembly is not None and part_id in assembly.part_refs_outside(row.project_id or ""):
        raise _bad(f"{part_id} is in another build's assembly",
                   hint="remove it from that build's assembly first")
    root = _parts_root(home)
    stamp = f"{part_id}.{time.time_ns()}"
    trash = Path(home) / "trash" / "parts"
    moved_files = False
    for src in (root / part_id, root / ".mises-meta" / part_id, root / THUMBS_DIR / part_id):
        real = _contained(root, src) if root.is_dir() else None
        if real is None or not os.path.isdir(real) or os.path.islink(src):
            continue
        moved_files = moved_files or src.parent == root
        dest = trash / stamp if src.parent == root else trash / src.parent.name / stamp
        # each level made (or checked) top-down through safe_io: a link planted at home/trash
        # or below is refused, never followed
        for level in (Path(home) / "trash", trash, dest.parent):
            safe_io.ensure_dir(level)
        os.rename(real, safe_io.real_dir(dest.parent) / dest.name)
    # where each removed piece sat, and which the core had placed itself (Undo puts both back)
    orders = assembly.orders(row.project_id) if used else {"instances": {}, "joints": {}}
    auto = assembly.auto_ids(row.project_id) if used else set()
    for inst in used:                      # D29: the cascade, once the files are in the trash
        try:
            assembly.delete_instance(row.project_id, inst.id)
        except KeyError:
            pass
    children = [c.id for c in store.list_part_rows(row.project_id) if c.parent_id == part_id]
    for child in children:
        store.update_part(child, parent_id=row.parent_id)
    # what Undo needs: the tree row, its children and the assembly pieces (host-only trash)
    safe_io.ensure_dir(Path(home) / "trash")
    safe_io.ensure_dir(trash)
    safe_io.write_text(trash, f"{stamp}.json", json.dumps({
        "part": row.model_dump(mode="json"), "children": children,
        "instances": [{**i.model_dump(mode="json"), "_ord": orders["instances"].get(i.id),
                       "_auto": i.id in auto} for i in used],
        "joints": [{**j.model_dump(mode="json"), "_ord": orders["joints"].get(j.id)} for j in joints],
        "had_files": moved_files}))
    store.delete_part_row(part_id)
    return {"deleted": part_id, "removed_instances": [i.id for i in used],
            "removed_joints": [j.id for j in joints], "undo": stamp}


def restore_part(store: Store, home: Path, part_id: str, undo: str, *,
                 assembly: Any = None) -> dict:
    """Undo a delete_part (Plan 9 D29): its files come back from the trash, then its tree row
    (label, look, parent, children), then the assembly instances and joints the delete removed.
    Refused when the trash entry is gone or a part of that id exists again."""
    from .assembly.model import Instance, Joint
    from .cad import safe_io
    m = _UNDO_RE.fullmatch(undo or "")
    if m is None or m.group(1) != part_id:
        raise _bad(f"{undo!r} is not an undo of {part_id}")
    trash = Path(home) / "trash" / "parts"
    try:
        manifest = safe_io.read_json(trash, f"{undo}.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        manifest = None
    if not isinstance(manifest, dict):
        raise MisesError(ErrorCode.NOT_FOUND, f"nothing to undo for {part_id}",
                         hint="the trash was emptied")
    if store.get_part(part_id) is not None:
        raise _bad(f"a part {part_id} exists again", hint="rename or delete it first")
    if manifest.get("had_files") and not os.path.isdir(trash / undo):
        raise MisesError(ErrorCode.NOT_FOUND, f"the files of {part_id} are no longer in the trash",
                         hint="the trash was emptied")
    root = _parts_root(home)
    for src, dest in ((trash / undo, root / part_id),
                      (trash / ".mises-meta" / undo, root / ".mises-meta" / part_id),
                      (trash / THUMBS_DIR / undo, root / THUMBS_DIR / part_id)):
        if os.path.islink(src) or not os.path.isdir(src) or os.path.lexists(dest):
            continue
        safe_io.ensure_dir(root)
        safe_io.ensure_dir(dest.parent)
        os.rename(safe_io.real_dir(src.parent) / src.name, safe_io.real_dir(dest.parent) / dest.name)
    old = Part.model_validate(manifest["part"])
    project = old.project_id or store.ensure_default_project().id
    store.register_part(part_id, project, label=old.label, kind=old.kind)
    parent = old.parent_id if old.parent_id and store.get_part(old.parent_id) else None
    store.update_part(part_id, color=old.color, appearance=old.appearance, parent_id=parent,
                      ord=old.order, version=old.version)
    for child in manifest.get("children") or []:
        c = store.get_part(child)
        if c is not None and c.project_id == project and c.parent_id == old.parent_id:
            store.update_part(child, parent_id=part_id)
    restored = 0
    if assembly is not None:
        for raw in manifest.get("instances") or []:
            try:
                inst = Instance.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})
                assembly.add_instance(project, inst, ord=raw.get("_ord"))
                if raw.get("_auto"):
                    assembly.mark_auto(project, inst.id)
                restored += 1
            except Exception:  # noqa: BLE001 - an instance id taken again: skip that one
                continue
        for raw in manifest.get("joints") or []:
            try:
                assembly.add_joint(project, Joint.model_validate({k: v for k, v in raw.items()
                                                                  if not k.startswith("_")}),
                                   ord=raw.get("_ord"))
            except Exception:  # noqa: BLE001 - its other end is gone now
                continue
    safe_io.unlink(trash, f"{undo}.json")
    return {**part_view(store, home, _part_or_404(store, part_id)),
            "restored_instances": restored}


EXPORT_FORMATS = ("step", "stl")


def export_dir() -> Path:
    """Where exports land: MISES_EXPORT_DIR, else ~/Downloads."""
    env = os.environ.get("MISES_EXPORT_DIR")
    return Path(env) if env else Path.home() / "Downloads"


def _file_stem(label: str) -> str:
    s = re.sub(r"[^\w .()-]+", " ", label, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:80] or "part"


def export_part(store: Store, home: Path, part_id: str, fmt: str) -> dict:
    """Copy the part's model.<fmt> to the export folder as "<label>.<fmt>" (never overwriting:
    "<label> (2).<fmt>", ...). Returns {path, name, format}."""
    from .cad import safe_io
    if fmt not in EXPORT_FORMATS:
        raise _bad(f"export format must be one of {', '.join(EXPORT_FORMATS)}")
    row = _part_or_404(store, part_id)
    root = _parts_root(home)
    src = _contained(root, root / part_id) if root.is_dir() else None
    data = safe_io.read_bytes(src, f"model.{fmt}") if src is not None else None
    if data is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"{part_id} has no {fmt.upper()} export",
                         hint="rebuild the part")
    out = export_dir()
    out.mkdir(parents=True, exist_ok=True)
    stem = _file_stem(display_name(row))
    for n in range(1, 1000):
        name = f"{stem}.{fmt}" if n == 1 else f"{stem} ({n}).{fmt}"
        try:
            fd = os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return {"path": str(out / name), "name": name, "format": fmt}
    raise _bad("too many exports with that name")


# ---- runs and agent management

def run_view(run: AgentRun) -> dict:
    d = run.model_dump(mode="json")
    d["parent_run_id"] = run.parent_id     # the API name for the one parent_id column
    d["last_activity_at"] = run.last_activity_at or run.updated_at
    d["cost"] = {"usd": run.usd, "tokens": run.input_tokens + run.output_tokens}
    if run.provider not in budget.COST_REPORTING and run.provider != "ui":
        d["cost"]["usd_note"] = budget.UNKNOWN_NOTE
    return d


BusyFn = Callable[[str], bool]


def _blocked(store: Store, run: AgentRun, busy: BusyFn | None) -> str | None:
    if run.status in ("running", "queued") or (busy is not None and busy(run.id)):
        return "running"
    if store.pending_approvals(run.id):
        return "pending_approval"
    return None


def delete_reason(store: Store, run: AgentRun,
                  busy: BusyFn | None = None) -> tuple[str, list[str]] | None:
    """Why a run cannot be deleted yet, as (reason, blocking descendant ids), or None when it
    is safe. `busy(run_id)` reports live work the status column does not show yet (a Claude
    Code turn or a follow-up message still starting). Any descendant that is running, queued,
    busy or awaiting approval blocks the delete with reason "running"."""
    children = []
    for cid in store.descendants(run.id):
        try:
            child = store.get_run(cid)
        except KeyError:
            continue
        if _blocked(store, child, busy) is not None:
            children.append(cid)
    own = _blocked(store, run, busy)
    if children:
        return "running", children
    if own is not None:
        return own, []
    return None


def cleanup_candidates(store: Store, *, project_id: str | None = None,
                       older_than_days: float | None = None,
                       status_in: list[str] | None = None,
                       busy: BusyFn | None = None) -> list[str]:
    """Ids of finished runs eligible for cleanup: never a run that is running, busy or awaiting
    approval, nor one with such a descendant; the app's own `ui` runs are skipped. An empty
    status_in selects nothing; None means every finished status."""
    statuses = list(TERMINAL) if status_in is None else [s for s in status_in if s in TERMINAL]
    if not statuses:
        return []
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    out = []
    for r in store.list_runs(100_000, project_id=project_id):
        if r.provider == "ui" or r.status not in statuses:
            continue
        if cutoff is not None and (r.last_activity_at or r.updated_at) > cutoff:
            continue
        if delete_reason(store, r, busy) is not None:
            continue
        out.append(r.id)
    return out


def runs_tree(store: Store, project_id: str | None = None) -> list[dict]:
    """The orchestrator -> worker forest, nested by parent_id (exposed as parent_run_id)."""
    runs = [r for r in store.list_runs(100_000, project_id=project_id) if r.provider != "ui"]
    by_id = {r.id: run_view(r) for r in runs}
    for node in by_id.values():
        node["children"] = []
    roots = []
    for r in runs:
        parent = r.parent_id
        if parent is not None and parent in by_id:
            by_id[parent]["children"].append(by_id[r.id])
        else:
            roots.append(by_id[r.id])
    return roots
