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


def active_project_id(store: Store, profile_active: str | None) -> str:
    """The build a project-less request belongs to: the profile's active build when it still
    exists, otherwise the Default build (created on demand)."""
    if profile_active:
        try:
            store.get_project(profile_active)
            return profile_active
        except KeyError:
            pass
    return store.ensure_default_project().id


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
    """Purge every trashed build. Its runs' workdirs are not removed here (the runs stay as
    history unless cleaned up); only the build rows go."""
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


def reconcile_parts(store: Store, home: Path, fallback_project_id: str) -> None:
    """Register a tree row for every built-on-disk part that has none yet, in the fallback
    build (the active build). Existing rows keep their build and placement."""
    root = _parts_root(home)
    if not root.is_dir():
        return
    known = {p.id for p in store.list_part_rows()}
    for d in sorted(root.iterdir(), key=lambda p: p.name):
        if not PART_ID_RE.fullmatch(d.name) or d.name in known:
            continue
        real = _contained(root, d)
        if real is None or not real.is_dir() or not (real / "vars.json").exists():
            continue
        store.register_part(d.name, fallback_project_id)


def _part_disk(home: Path, part_id: str) -> dict:
    """vars, metrics and mtime from the part's on-disk build, or empty defaults."""
    root = _parts_root(home)
    real = _contained(root, root / part_id) if root.is_dir() else None
    if real is None or not real.is_dir():
        return {"vars": {}, "metrics": None, "mtime": None}
    vars_ = _read_json(real / "vars.json")
    metrics = _read_json(real / "metrics.json")
    mtimes = [p.stat().st_mtime for p in real.iterdir() if p.is_file()]
    return {"vars": vars_ if isinstance(vars_, dict) else {},
            "metrics": metrics if isinstance(metrics, dict) else None,
            "mtime": float(max(mtimes)) if mtimes else real.stat().st_mtime}


def part_view(store: Store, home: Path, row: Part) -> dict:
    d = row.model_dump()
    # `part_id` is kept beside the contract's `id` so older UI code keeps working
    return {**d, "part_id": row.id, **_part_disk(home, row.id)}


def list_parts(store: Store, home: Path, project_id: str) -> list[dict]:
    reconcile_parts(store, home, project_id)
    return [part_view(store, home, r) for r in store.list_part_rows(project_id)]


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


def patch_part(store: Store, home: Path, part_id: str, *, label: str | None = None,
               color: str | None | object = ..., parent_id: str | None | object = ...) -> dict:
    """Rename, recolor or re-parent a part. `color`/`parent_id` left as the sentinel are
    unchanged; None clears them. Re-parenting that would form a cycle is rejected."""
    _part_or_404(store, part_id)
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
        if parent_id is not None and store.get_part(parent_id) is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown parent part: {parent_id}")
        _reject_cycle(store, part_id, parent_id)
        fields["parent_id"] = parent_id
    if fields:
        store.update_part(part_id, **fields)
    return part_view(store, home, _part_or_404(store, part_id))


def reorder_parts(store: Store, home: Path, project_id: str, parent_id: str | None,
                  ids: list[str]) -> list[dict]:
    _project_or_404(store, project_id)
    seen: set[str] = set()
    for pid in ids:
        if pid in seen:
            raise _bad(f"part {pid} listed twice in reorder")
        seen.add(pid)
        if store.get_part(pid) is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown part: {pid}")
        _reject_cycle(store, pid, parent_id)
    store.reorder_parts(project_id, parent_id, ids)
    return list_parts(store, home, project_id)


# ---- runs and agent management

def run_view(run: AgentRun) -> dict:
    d = run.model_dump(mode="json")
    d["parent_run_id"] = run.parent_run_id or run.parent_id
    d["last_activity_at"] = run.last_activity_at or run.updated_at
    d["cost"] = {"usd": run.usd, "tokens": run.input_tokens + run.output_tokens}
    return d


def delete_reason(store: Store, run: AgentRun) -> str | None:
    """Why a run cannot be deleted yet, or None when it is safe to delete."""
    if run.status in ("running", "queued"):
        return "running"
    if store.pending_approvals(run.id):
        return "pending_approval"
    return None


def cleanup_candidates(store: Store, *, project_id: str | None = None,
                       older_than_days: float | None = None,
                       status_in: list[str] | None = None) -> list[str]:
    """Ids of finished runs eligible for cleanup. Running, queued or approval-pending runs are
    never included; the app's own `ui` runs are skipped."""
    statuses = [s for s in (status_in or list(TERMINAL)) if s in TERMINAL]
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    out = []
    for r in store.list_runs(100_000, project_id=project_id):
        if r.provider == "ui" or r.status not in statuses:
            continue
        if store.pending_approvals(r.id):
            continue
        if cutoff is not None and (r.last_activity_at or r.updated_at) > cutoff:
            continue
        out.append(r.id)
    return out


def runs_tree(store: Store, project_id: str | None = None) -> list[dict]:
    """The orchestrator -> worker forest. Nesting follows parent_run_id, falling back to the
    legacy parent_id so runs the orchestrator already created still nest."""
    runs = [r for r in store.list_runs(100_000, project_id=project_id) if r.provider != "ui"]
    by_id = {r.id: run_view(r) for r in runs}
    for node in by_id.values():
        node["children"] = []
    roots = []
    for r in runs:
        parent = r.parent_run_id or r.parent_id
        if parent is not None and parent in by_id:
            by_id[parent]["children"].append(by_id[r.id])
        else:
            roots.append(by_id[r.id])
    return roots
