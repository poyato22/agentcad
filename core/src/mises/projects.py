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
import re
import time
from collections.abc import Callable
from pathlib import Path

from .cad.tools import PART_ID_RE
from .store import AgentRun, Part, Project, Store
from .types import ErrorCode, MisesError

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# runs whose cleanup is allowed: only ones that have finished one way or another
TERMINAL = ("done", "failed", "cancelled")


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


# ---- builds (projects)

def project_view(store: Store, p: Project) -> dict:
    """The GET /projects shape. onshape stays null until C5's import links a document."""
    return {"id": p.id, "name": p.name, "created_at": p.created_at, "updated_at": p.updated_at,
            "trashed_at": p.trashed_at, "part_count": store.project_part_count(p.id),
            "run_count": store.project_run_count(p.id), "onshape": None}


def list_projects(store: Store) -> list[dict]:
    return [project_view(store, p) for p in store.list_projects()]


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


def empty_trash(store: Store) -> dict:
    """Purge every trashed build and its part-tree rows. Part files on disk and the builds'
    runs stay (runs as history until cleaned up)."""
    purged = [p.id for p in store.list_projects() if p.trashed_at is not None]
    for pid in purged:
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
    vars_ = _read_json(real / "vars.json")
    metrics = _read_json(real / "metrics.json")
    mtimes = [p.stat().st_mtime for p in real.iterdir() if p.is_file()]
    return {"vars": vars_ if isinstance(vars_, dict) else {},
            "metrics": metrics if isinstance(metrics, dict) else None,
            "mtime": float(max(mtimes)) if mtimes else real.stat().st_mtime}


_NO_DISK = {"vars": {}, "metrics": None, "mtime": None}


def part_version(home: Path, row: Part) -> int:
    """The part's version. Source of truth: C6's host-only meta file
    (parts/.mises-meta/<id>/version.json), bumped by every successful edit. The parts.version
    column is only a mirror of it (Store.set_part_version); it answers for a part that has no
    meta version yet (built before versioning: read_version gives 0)."""
    from . import edits
    v = edits.read_version(_parts_root(home) / row.id)
    return v if v > 0 else row.version


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
               color: str | None | object = ..., parent_id: str | None | object = ...) -> dict:
    """Rename, recolor or re-parent a part. `color`/`parent_id` left as the sentinel are
    unchanged; None clears them. The parent must be in the same build, and a move that would
    form a cycle is rejected."""
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


# ---- runs and agent management

def run_view(run: AgentRun) -> dict:
    d = run.model_dump(mode="json")
    d["parent_run_id"] = run.parent_id     # the API name for the one parent_id column
    d["last_activity_at"] = run.last_activity_at or run.updated_at
    d["cost"] = {"usd": run.usd, "tokens": run.input_tokens + run.output_tokens}
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
