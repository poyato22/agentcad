"""SQLite store: runs, policies, hash-chained steps, approvals, ledger, tool cache."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, computed_field, field_validator

from .types import Caps, SafetyClass, StepKind, canonical_json, sha256_hex


class AgentRun(BaseModel):
    id: str
    parent_id: str | None = None
    agent: str
    provider: str
    model: str
    task: str
    # "queued": waiting for a parallel-agents slot (runs.status is plain TEXT: no migration)
    status: Literal["queued", "running", "done", "failed", "cancelled"] = "running"
    branch: str | None = None
    onshape_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    created_at: float
    updated_at: float
    owner_pid: int | None = None  # process that created (drives) the run
    external: bool = False  # driven by an outside agent (Runtime.create_external_run), not a loop
    session_ref: str | None = None  # the outside agent's own session id (Claude Code --resume)
    # ---- builds / agent-management columns (Plan 5 C1)
    project_id: str | None = None  # the build this run belongs to (NULL until migrated/assigned)
    title: str | None = None       # a user-renamable label; falls back to the task in the UI
    pinned: bool = False
    archived: bool = False
    last_activity_at: float | None = None  # bumped as the run works; falls back to updated_at
    # ---- Plan 7 U3c: the build's lead and its sub-agents, and how the router chose the agent
    role: Literal["lead", "sub"] | None = None
    routed: dict | None = None     # {agent, reason, confidence}; stored as JSON text
    # Plan 8 G-R6 routing hint: {score, level, signals, vision_recommended}; NULL until estimated
    shape_complexity: dict | None = None
    # Plan 9 G-B (D27): {score, cls, per_part, reasons, source}; NULL until classified
    design_class: dict | None = None
    # a build's lead: when the current user message began, and the family's dollars then
    # (budget.py counts the lead's per-run caps from here; NULL = the run's whole life)
    turn_started_at: float | None = None
    turn_usd0: float | None = None

    @field_validator("routed", "shape_complexity", "design_class", mode="before")
    @classmethod
    def _json_fields(cls, v: Any) -> Any:
        return json.loads(v) if isinstance(v, str) else v


class AgentPolicy(BaseModel):
    run_id: str
    version: int
    edit_scope: list[str]
    read_scope: list[str]
    caps: Caps
    allowed_classes: list[SafetyClass]
    created_at: float


class Step(BaseModel):
    id: int
    run_id: str
    ts: float
    kind: StepKind
    payload: dict
    evidence_hash: str
    prev_hash: str | None = None
    collapsed: bool = False

    @computed_field
    @property
    def stream(self) -> str:
        """text / sim / view (Plan 9 D4): sim_run steps and sim tools' steps are "sim"."""
        from .events import stream_of
        return stream_of(str(self.kind), self.payload)


class Approval(BaseModel):
    id: str
    run_id: str
    action: str
    payload: dict
    payload_hash: str
    microversion: str | None = None
    deltas: dict = {}
    status: Literal["pending", "approved", "rejected", "expired", "executed"] = "pending"
    expires_at: float
    decided_by: str | None = None
    decided_at: float | None = None
    edited_payload: dict | None = None


class LedgerEntry(BaseModel):
    id: int
    user: str
    run_id: str | None = None
    endpoint: str
    estimate: int
    idempotency_key: str
    status_code: int | None = None
    counted: bool = False
    state: Literal["reserved", "settled", "released"] = "reserved"
    ts: float


class Project(BaseModel):
    id: str
    name: str
    created_at: float
    updated_at: float
    trashed_at: float | None = None


class Part(BaseModel):
    """Part-tree metadata. Geometry, vars and metrics stay on disk under home/parts/<id>/;
    this row carries only the tree placement the user and agents edit."""
    id: str
    project_id: str | None = None
    label: str
    parent_id: str | None = None
    order: int = 0
    color: str | None = None  # "#rrggbb"
    kind: Literal["part", "group", "catalog", "skeleton", "reference"] = "part"  # skeleton: Plan 8 G3
    version: int = 1
    # Plan 7 U8: {preset, color, metalness, roughness, opacity} (appearance.normalize), or None
    appearance: dict | None = None
    # Plan 9 B2: the part's material for its mass (a materials-table name), None = the
    # build default (the profile's default_material)
    material: str | None = None
    # DRONE demo: a bought or placeholder part's datasheet mass {mass_g, source}; it wins over
    # volume x density (a LiPo, a VTX), None = weighed from the build
    stated_mass: dict | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, provider TEXT, model TEXT, task TEXT,
  status TEXT, branch TEXT, onshape_calls INTEGER, input_tokens INTEGER, output_tokens INTEGER,
  usd REAL, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT, created_at REAL, updated_at REAL, trashed_at REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS parts (
  id TEXT PRIMARY KEY, project_id TEXT, label TEXT, parent_id TEXT, ord INTEGER,
  color TEXT, kind TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS policies (
  run_id TEXT, version INTEGER, edit_scope TEXT, read_scope TEXT, caps TEXT,
  allowed_classes TEXT, created_at REAL, PRIMARY KEY (run_id, version));
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, kind TEXT, payload TEXT,
  evidence_hash TEXT, prev_hash TEXT, collapsed INTEGER);
CREATE INDEX IF NOT EXISTS steps_run ON steps(run_id, id);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, run_id TEXT, action TEXT, payload TEXT, payload_hash TEXT,
  microversion TEXT, deltas TEXT, status TEXT, expires_at REAL, decided_by TEXT,
  decided_at REAL, edited_payload TEXT);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, run_id TEXT, endpoint TEXT, estimate INTEGER,
  idempotency_key TEXT, status_code INTEGER, counted INTEGER, state TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS tool_cache (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS specs (
  project_id TEXT PRIMARY KEY, data TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS studies (
  id TEXT PRIMARY KEY, project_id TEXT, data TEXT, status TEXT, created_at REAL,
  updated_at REAL);
CREATE INDEX IF NOT EXISTS studies_project ON studies(project_id, id);
"""

_RUN_FIELDS = {"status", "branch", "agent", "provider", "model", "task", "parent_id",
               "session_ref", "owner_pid", "project_id", "title", "pinned",
               "archived", "last_activity_at", "role", "routed", "shape_complexity"}
_APPROVAL_JSON = {"payload", "deltas", "edited_payload"}


def _dumps(v: Any) -> str | None:
    return None if v is None else canonical_json(v)


def _step_hash(run_id: str, kind: str, payload: dict, prev_hash: str | None) -> str:
    return sha256_hex(canonical_json(
        {"run_id": run_id, "kind": str(kind), "payload": payload, "prev_hash": prev_hash}))


def _json_or_none(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)            # G3: the profile beside it (budget.caps_enabled)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(runs)")}
        if "owner_pid" not in cols:  # databases from before owner tracking
            self._db.execute("ALTER TABLE runs ADD COLUMN owner_pid INTEGER")
        if "external" not in cols:  # databases from before the external flag
            self._db.execute("ALTER TABLE runs ADD COLUMN external INTEGER NOT NULL DEFAULT 0")
        if "session_ref" not in cols:  # databases from before resumable external sessions
            self._db.execute("ALTER TABLE runs ADD COLUMN session_ref TEXT")
        # Plan 5 C1: builds and agent-management columns, added to existing databases
        # (the orchestrator parent is the existing parent_id column; the API exposes it as
        # parent_run_id, so there is no second column for it)
        for col, decl in (("project_id", "TEXT"), ("title", "TEXT"),
                          ("pinned", "INTEGER NOT NULL DEFAULT 0"),
                          ("archived", "INTEGER NOT NULL DEFAULT 0"),
                          ("last_activity_at", "REAL"),
                          ("role", "TEXT"), ("routed", "TEXT"),   # Plan 7 U3c
                          ("shape_complexity", "TEXT"),           # Plan 8 G1
                          ("design_class", "TEXT"),               # Plan 9 G-B
                          ("turn_started_at", "REAL"), ("turn_usd0", "REAL")):  # per-turn caps
            if col not in cols:
                self._db.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")
        # Plan 7 U8: the part's material (JSON), added to existing databases
        part_cols = {r[1] for r in self._db.execute("PRAGMA table_info(parts)")}
        if "appearance" not in part_cols:
            self._db.execute("ALTER TABLE parts ADD COLUMN appearance TEXT")
        if "material" not in part_cols:                       # Plan 9 B2
            self._db.execute("ALTER TABLE parts ADD COLUMN material TEXT")
        if "stated_mass" not in part_cols:
            self._db.execute("ALTER TABLE parts ADD COLUMN stated_mass TEXT")
        self._migrate_default_project()

    @contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def _one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ---- runs
    def create_run(self, agent: str, provider: str, model: str, task: str,
                   parent_id: str | None = None, external: bool = False,
                   session_ref: str | None = None, project_id: str | None = None) -> AgentRun:
        now = time.time()
        rid = uuid.uuid4().hex[:12]
        with self._tx() as db:
            db.execute("INSERT INTO runs (id, parent_id, agent, provider, model, task, status,"
                       " branch, onshape_calls, input_tokens, output_tokens, usd, created_at,"
                       " updated_at, owner_pid, external, session_ref, project_id, last_activity_at)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rid, parent_id, agent, provider, model, task, "running", None,
                        0, 0, 0, 0.0, now, now, os.getpid(), int(external), session_ref,
                        project_id, now))
        return self.get_run(rid)

    def get_run(self, run_id: str) -> AgentRun:
        row = self._one("SELECT * FROM runs WHERE id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        return AgentRun(**dict(row))

    def update_run(self, run_id: str, **fields: Any) -> AgentRun:
        bad = set(fields) - _RUN_FIELDS
        if bad:
            raise ValueError(f"cannot update run fields: {sorted(bad)}")
        if isinstance(fields.get("routed"), dict):
            fields["routed"] = canonical_json(fields["routed"])
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._tx() as db:
                db.execute(f"UPDATE runs SET {sets}, updated_at=? WHERE id=?",
                           (*fields.values(), time.time(), run_id))
        return self.get_run(run_id)

    def set_shape_complexity(self, run_id: str, data: dict) -> None:
        """Store the G-R6 routing hint on the run row (JSON); a deleted run is a no-op."""
        with self._tx() as db:
            db.execute("UPDATE runs SET shape_complexity=? WHERE id=?",
                       (canonical_json(data), run_id))

    def set_design_class(self, run_id: str, data: dict) -> None:
        """Store the D27 design-intensity class on the run row (JSON); a deleted run is a
        no-op."""
        with self._tx() as db:
            db.execute("UPDATE runs SET design_class=? WHERE id=?",
                       (canonical_json(data), run_id))

    def start_turn(self, run_id: str, ts: float, family_usd: float) -> None:
        """A new user message to a lead: its caps count from `ts` (Onshape ledger rows) and
        from `family_usd` (dollars), and its family's web-fetch budget starts again."""
        with self._tx() as db:
            db.execute("UPDATE runs SET turn_started_at=?, turn_usd0=? WHERE id=?",
                       (ts, family_usd, run_id))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND"
                          " name='browser_budget'").fetchone() is not None:
                db.execute("DELETE FROM browser_budget WHERE run_id=?", (run_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND"
                          " name='browser_domain_fetches'").fetchone() is not None:
                db.execute("DELETE FROM browser_domain_fetches WHERE run_id=?", (run_id,))

    def touch_run_activity(self, run_id: str, ts: float) -> None:
        """Bump last_activity_at only (not updated_at); a deleted run is a no-op."""
        with self._tx() as db:
            db.execute("UPDATE runs SET last_activity_at=? WHERE id=?", (ts, run_id))

    def list_runs(self, limit: int = 50, *, project_id: str | None = None,
                  archived: bool | None = None) -> list[AgentRun]:
        sql = "SELECT * FROM runs"
        clauses, args = [], []
        if project_id is not None:
            clauses.append("project_id=?")
            args.append(project_id)
        if archived is not None:
            clauses.append("archived=?")
            args.append(int(archived))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        return [AgentRun(**dict(r)) for r in self._all(sql, tuple(args))]

    def run_family(self, run_id: str) -> tuple[str, list[str]]:
        """(root run id, ids of the root and all its descendants) for a run's parent_id tree."""
        root, seen = run_id, {run_id}
        while True:
            row = self._one("SELECT parent_id FROM runs WHERE id=?", (root,))
            parent = row[0] if row else None
            if parent is None or parent in seen:
                break
            seen.add(parent)
            root = parent
        family, frontier = [root], [root]
        while frontier:
            rows = self._all(f"SELECT id FROM runs WHERE parent_id IN ({','.join('?' * len(frontier))})",
                             tuple(frontier))
            frontier = [r[0] for r in rows if r[0] not in family]
            family += frontier
        return root, family

    def runs_usd(self, run_ids: list[str]) -> float:
        """Dollars spent by these runs together (the dollar family cap, budget.py)."""
        if not run_ids:
            return 0.0
        q = ",".join("?" * len(run_ids))
        row = self._one(f"SELECT COALESCE(SUM(usd), 0) FROM runs WHERE id IN ({q})",
                        tuple(run_ids))
        return float(row[0] or 0.0)

    def runs_created_since(self, run_ids: list[str], since: float) -> int:
        """How many of these runs were created at or after `since` (spawns this turn)."""
        if not run_ids:
            return 0
        q = ",".join("?" * len(run_ids))
        row = self._one(f"SELECT COUNT(*) FROM runs WHERE id IN ({q}) AND created_at>=?",
                        (*run_ids, since))
        return int(row[0] or 0)

    def running_runs(self, *, include_queued: bool = False) -> list[AgentRun]:
        sql = ("SELECT * FROM runs WHERE status IN ('running','queued') ORDER BY created_at"
               if include_queued else
               "SELECT * FROM runs WHERE status='running' ORDER BY created_at")
        rows = self._all(sql)
        return [AgentRun(**dict(r)) for r in rows]

    def set_part_version(self, part_id: str, version: int) -> bool:
        """Best-effort mirror of a part's (filesystem) version into the parts table, when that
        table and column exist (C1 owns them). A no-op returning False before that migration
        lands, so live editing works both before and after it, with no guessing of the schema."""
        try:
            with self._tx() as db:
                db.execute("UPDATE parts SET version=? WHERE id=?", (int(version), part_id))
            return True
        except sqlite3.OperationalError:
            return False

    def add_usage(self, run_id: str, *, input_tokens: int = 0, output_tokens: int = 0,
                  usd: float = 0.0, onshape_calls: int = 0) -> None:
        with self._tx() as db:
            db.execute("UPDATE runs SET input_tokens=input_tokens+?, output_tokens=output_tokens+?,"
                       " usd=usd+?, onshape_calls=onshape_calls+?, updated_at=? WHERE id=?",
                       (input_tokens, output_tokens, usd, onshape_calls, time.time(), run_id))

    def delete_run(self, run_id: str) -> None:
        """Remove a run and everything the Mises home holds for it (steps, approvals, policies).
        The caller removes the workdir; ledger rows stay as budget history. Parts are never
        touched (they are shared across runs)."""
        with self._tx() as db:
            db.execute("DELETE FROM steps WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM approvals WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM policies WHERE run_id=?", (run_id,))
            for table in ("browser_snapshots", "browser_budget", "browser_domain_fetches",
                          "part_cards"):  # U4c
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (table,)).fetchone() is not None:
                    db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM runs WHERE id=?", (run_id,))

    def runs_in_project(self, project_id: str) -> list[AgentRun]:
        rows = self._all("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC, rowid DESC",
                         (project_id,))
        return [AgentRun(**dict(r)) for r in rows]

    # ---- projects (builds)
    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(**dict(row))

    # ---- meta (one-shot migration flags, the Default build's id)
    def meta_get(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def meta_delete(self, key: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM meta WHERE key=?", (key,))

    # Plan 9 G-B: the user's design-class override on a build (engineering|mixed|design), kept
    # in meta so the project payload is unchanged; None = let the classifier decide
    def project_design_class(self, project_id: str) -> str | None:
        return self.meta_get(f"design_class:{project_id}")

    def set_project_design_class(self, project_id: str, cls: str | None) -> None:
        with self._tx() as db:
            if cls is None:
                db.execute("DELETE FROM meta WHERE key=?", (f"design_class:{project_id}",))
            else:
                db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                           (f"design_class:{project_id}", cls))

    def _ensure_default_in(self, db) -> str:
        """The Default build, found by the id recorded in meta (never by name: the user may
        rename it, or name another build "Default"). Recreated if it was purged."""
        row = db.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone()
        if row is not None and db.execute("SELECT 1 FROM projects WHERE id=?",
                                          (row[0],)).fetchone() is not None:
            return row[0]
        pid = uuid.uuid4().hex[:12]
        now = time.time()
        db.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (pid, "Default", now, now, None))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('default_project_id', ?)", (pid,))
        return pid

    def _migrate_default_project(self) -> None:
        """Once per database (meta flag): assign every pre-existing run and part (project_id
        NULL) to the Default build. A fresh database has nothing to assign; later runs always
        get a build at creation (Runtime), so this never sweeps them."""
        with self._tx() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='builds_migrated'").fetchone():
                return
            orphan_runs = db.execute(
                "SELECT COUNT(*) FROM runs WHERE project_id IS NULL").fetchone()[0]
            orphan_parts = db.execute(
                "SELECT COUNT(*) FROM parts WHERE project_id IS NULL").fetchone()[0]
            if orphan_runs or orphan_parts:
                pid = self._ensure_default_in(db)
                db.execute("UPDATE runs SET project_id=? WHERE project_id IS NULL", (pid,))
                db.execute("UPDATE parts SET project_id=? WHERE project_id IS NULL", (pid,))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('builds_migrated', '1')")

    def ensure_default_project(self) -> Project:
        with self._tx() as db:
            pid = self._ensure_default_in(db)
        return self.get_project(pid)

    def default_project_id(self) -> str | None:
        """The recorded Default build's id, if it still exists (no creation)."""
        pid = self.meta_get("default_project_id")
        if pid is None or self._one("SELECT 1 FROM projects WHERE id=?", (pid,)) is None:
            return None
        return pid

    def descendants(self, run_id: str) -> list[str]:
        """Ids of every run below this one, following parent_id."""
        out: list[str] = []
        seen, frontier = {run_id}, [run_id]
        while frontier:
            q = ",".join("?" * len(frontier))
            rows = self._all(f"SELECT id FROM runs WHERE parent_id IN ({q})", tuple(frontier))
            frontier = [r[0] for r in rows if r[0] not in seen]
            seen.update(frontier)
            out += frontier
        return out

    def create_project(self, name: str) -> Project:
        pid = uuid.uuid4().hex[:12]
        now = time.time()
        with self._tx() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (pid, name, now, now, None))
        return self.get_project(pid)

    def get_project(self, project_id: str) -> Project:
        row = self._one("SELECT * FROM projects WHERE id=?", (project_id,))
        if row is None:
            raise KeyError(project_id)
        return self._project(row)

    def list_projects(self, *, include_trashed: bool = True) -> list[Project]:
        sql = "SELECT * FROM projects"
        if not include_trashed:
            sql += " WHERE trashed_at IS NULL"
        sql += " ORDER BY created_at, rowid"
        return [self._project(r) for r in self._all(sql)]

    def update_project(self, project_id: str, **fields: Any) -> Project:
        allowed = {"name", "trashed_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update project fields: {sorted(bad)}")
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._tx() as db:
                db.execute(f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
                           (*fields.values(), time.time(), project_id))
        return self.get_project(project_id)

    def purge_project(self, project_id: str) -> None:
        """Delete the build and every row it owns: part tree, spec, studies and build-scoped
        catalog items (projects.empty_trash removes its files and assembly). Runs keep their
        now-dangling project_id as history until cleaned up."""
        with self._tx() as db:
            db.execute("DELETE FROM parts WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM specs WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM studies WHERE project_id=?", (project_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND"
                          " name='catalog_items'").fetchone() is not None:
                db.execute("DELETE FROM catalog_items WHERE project_id=?", (project_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND"
                          " name='bom_items'").fetchone() is not None:        # U4c
                db.execute("DELETE FROM bom_items WHERE project_id=?", (project_id,))
            # G-A concept rows (the user's marks included): the tables exist once a ConceptStore
            # has run, so guard each (FW-core M-5)
            for t in ("concepts", "concept_sketches", "concept_pitches", "concept_feedback"):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (t,)).fetchone() is not None:
                    db.execute(f"DELETE FROM {t} WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def project_part_count(self, project_id: str) -> int:
        return int(self._one("SELECT COUNT(*) FROM parts WHERE project_id=?", (project_id,))[0])

    def project_run_count(self, project_id: str) -> int:
        return int(self._one("SELECT COUNT(*) FROM runs WHERE project_id=?", (project_id,))[0])

    def project_last_run_activity(self, project_id: str) -> float | None:
        """The newest run activity in the build (last_activity_at, else updated_at); None if
        it has no runs."""
        row = self._one("SELECT MAX(COALESCE(last_activity_at, updated_at)) FROM runs"
                        " WHERE project_id=?", (project_id,))
        return float(row[0]) if row is not None and row[0] is not None else None

    # ---- parts (tree metadata; geometry/vars live on disk)
    @staticmethod
    def _part(row: sqlite3.Row) -> Part:
        d = dict(row)
        return Part(id=d["id"], project_id=d["project_id"], label=d["label"],
                    parent_id=d["parent_id"], order=d["ord"] or 0, color=d["color"],
                    kind=d["kind"] or "part", version=d["version"] or 1,
                    appearance=_json_or_none(d.get("appearance")),
                    material=d.get("material"),
                    stated_mass=_json_or_none(d.get("stated_mass")))

    def get_part(self, part_id: str) -> Part | None:
        row = self._one("SELECT * FROM parts WHERE id=?", (part_id,))
        return self._part(row) if row is not None else None

    def list_part_rows(self, project_id: str | None = None) -> list[Part]:
        if project_id is None:
            rows = self._all("SELECT * FROM parts ORDER BY ord, rowid")
        else:
            rows = self._all("SELECT * FROM parts WHERE project_id=? ORDER BY ord, rowid",
                             (project_id,))
        return [self._part(r) for r in rows]

    def register_part(self, part_id: str, project_id: str, *, label: str | None = None,
                      kind: str = "part") -> Part:
        """Insert a tree row for a part that does not have one yet (disk reconcile, or a new
        catalog/group part). Existing rows are left untouched."""
        with self._tx() as db:
            row = db.execute("SELECT id FROM parts WHERE id=?", (part_id,)).fetchone()
            if row is None:
                nxt = db.execute("SELECT COALESCE(MAX(ord),-1)+1 FROM parts WHERE project_id=?"
                                 " AND parent_id IS NULL", (project_id,)).fetchone()[0]
                db.execute("INSERT INTO parts (id, project_id, label, parent_id, ord, color, kind,"
                           " version) VALUES (?,?,?,?,?,?,?,?)",
                           (part_id, project_id, label or part_id, None, nxt, None, kind, 1))
        return self.get_part(part_id)

    def update_part(self, part_id: str, **fields: Any) -> Part:
        allowed = {"label", "color", "parent_id", "project_id", "kind", "version", "ord",
                   "appearance", "material", "stated_mass"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update part fields: {sorted(bad)}")
        with self._tx() as db:
            if db.execute("SELECT 1 FROM parts WHERE id=?", (part_id,)).fetchone() is None:
                raise KeyError(part_id)
            for k in ("appearance", "stated_mass"):
                if fields.get(k) is not None:
                    fields = {**fields, k: json.dumps(fields[k])}
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                db.execute(f"UPDATE parts SET {sets} WHERE id=?", (*fields.values(), part_id))
        return self.get_part(part_id)

    def bump_part_version(self, part_id: str) -> int:
        """Increment and return the part's version (used by C6's regenerate). An unknown part
        raises KeyError: a version bump must never silently do nothing."""
        with self._tx() as db:
            cur = db.execute("UPDATE parts SET version=COALESCE(version,1)+1 WHERE id=?",
                             (part_id,))
            if cur.rowcount != 1:
                raise KeyError(part_id)
            row = db.execute("SELECT version FROM parts WHERE id=?", (part_id,)).fetchone()
        return int(row[0])

    def reorder_parts(self, project_id: str, parent_id: str | None, ids: list[str]) -> None:
        """The listed siblings take ord 0..n-1 under parent_id; siblings left out keep their
        relative order after them, so ords never collide. Callers validate the ids belong to
        project_id (projects.reorder_parts)."""
        with self._tx() as db:
            for i, pid in enumerate(ids):
                db.execute("UPDATE parts SET ord=?, parent_id=? WHERE id=?", (i, parent_id, pid))
            if ids:
                q = ",".join("?" * len(ids))
                rest = db.execute(
                    f"SELECT id FROM parts WHERE project_id=? AND parent_id IS ? AND id NOT IN ({q})"
                    " ORDER BY ord, rowid", (project_id, parent_id, *ids)).fetchall()
                for j, r in enumerate(rest):
                    db.execute("UPDATE parts SET ord=? WHERE id=?", (len(ids) + j, r[0]))

    def delete_part_row(self, part_id: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM parts WHERE id=?", (part_id,))

    # ---- policies
    def create_policy(self, run_id: str, edit_scope: list[str], read_scope: list[str],
                      caps: Caps, allowed_classes: list[SafetyClass]) -> AgentPolicy:
        with self._tx() as db:
            row = db.execute("SELECT COALESCE(MAX(version),0) FROM policies WHERE run_id=?",
                             (run_id,)).fetchone()
            version = row[0] + 1
            db.execute("INSERT INTO policies VALUES (?,?,?,?,?,?,?)",
                       (run_id, version, json.dumps(edit_scope), json.dumps(read_scope),
                        caps.model_dump_json(), json.dumps([str(c) for c in allowed_classes]),
                        time.time()))
        return self.current_policy(run_id)

    def current_policy(self, run_id: str) -> AgentPolicy:
        row = self._one("SELECT * FROM policies WHERE run_id=? ORDER BY version DESC LIMIT 1",
                        (run_id,))
        if row is None:
            raise KeyError(run_id)
        d = dict(row)
        return AgentPolicy(run_id=d["run_id"], version=d["version"],
                           edit_scope=json.loads(d["edit_scope"]),
                           read_scope=json.loads(d["read_scope"]),
                           caps=Caps.model_validate_json(d["caps"]),
                           allowed_classes=json.loads(d["allowed_classes"]),
                           created_at=d["created_at"])

    # ---- steps
    @staticmethod
    def _step(row: sqlite3.Row) -> Step:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        d["collapsed"] = bool(d["collapsed"])
        return Step(**d)

    def append_step(self, run_id: str, kind: StepKind, payload: dict,
                    collapsed: bool = False) -> Step:
        with self._tx() as db:
            prev = db.execute("SELECT evidence_hash FROM steps WHERE run_id=? ORDER BY id DESC LIMIT 1",
                              (run_id,)).fetchone()
            prev_hash = prev[0] if prev else None
            h = _step_hash(run_id, kind, payload, prev_hash)
            cur = db.execute("INSERT INTO steps (run_id, ts, kind, payload, evidence_hash, prev_hash,"
                             " collapsed) VALUES (?,?,?,?,?,?,?)",
                             (run_id, time.time(), str(kind), canonical_json(payload), h,
                              prev_hash, int(collapsed)))
            step_id = cur.lastrowid
        return self.get_step(step_id)

    def steps(self, run_id: str, kinds: list[StepKind] | None = None,
              after_id: int = 0) -> list[Step]:
        sql, args = "SELECT * FROM steps WHERE run_id=? AND id>?", [run_id, after_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += [str(k) for k in kinds]
        return [self._step(r) for r in self._all(sql + " ORDER BY id", tuple(args))]

    def joint_refs_from_history(self, joint_ids: list[str]) -> dict[str, str]:
        """{joint id: the instance it moved (its `b`)} for joints the build has since removed or
        re-made, from the Step that added each (an old study's joint ids stay nameable)."""
        out: dict[str, str] = {}
        for jid in joint_ids[:64]:
            if not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", jid or ""):
                continue
            rows = self._all("SELECT payload FROM steps WHERE kind=? AND payload LIKE ? "
                             "ORDER BY id DESC LIMIT 5", (str(StepKind.DIFF), f'%"{jid}"%'))
            for r in rows:
                try:
                    joint = json.loads(r["payload"])["output"]["data"]["joint"]
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(joint, dict) and joint.get("id") == jid:
                    b = (joint.get("b") or {}).get("instance_id")
                    if isinstance(b, str):
                        out[jid] = b
                        break
        return out

    def get_step(self, step_id: int) -> Step:
        row = self._one("SELECT * FROM steps WHERE id=?", (step_id,))
        if row is None:
            raise KeyError(step_id)
        return self._step(row)

    def verify_chain(self, run_id: str) -> bool:
        prev: str | None = None
        for s in self.steps(run_id):
            if s.prev_hash != prev or s.evidence_hash != _step_hash(run_id, s.kind, s.payload, prev):
                return False
            prev = s.evidence_hash
        return True

    # ---- approvals
    @staticmethod
    def _approval(row: sqlite3.Row) -> Approval:
        d = dict(row)
        for k in _APPROVAL_JSON:
            d[k] = json.loads(d[k]) if d[k] is not None else None
        return Approval(**d)

    def insert_approval(self, a: Approval) -> Approval:
        with self._tx() as db:
            db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (a.id, a.run_id, a.action, canonical_json(a.payload), a.payload_hash,
                        a.microversion, canonical_json(a.deltas), a.status, a.expires_at,
                        a.decided_by, a.decided_at, _dumps(a.edited_payload)))
        return self.get_approval(a.id)

    def get_approval(self, approval_id: str) -> Approval:
        row = self._one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None:
            raise KeyError(approval_id)
        return self._approval(row)

    def pending_approvals(self, run_id: str | None = None) -> list[Approval]:
        if run_id is None:
            rows = self._all("SELECT * FROM approvals WHERE status='pending' ORDER BY rowid")
        else:
            rows = self._all("SELECT * FROM approvals WHERE status='pending' AND run_id=?"
                             " ORDER BY rowid", (run_id,))
        return [self._approval(r) for r in rows]

    def cas_approval(self, approval_id: str, *, from_status: str, to_status: str,
                     **fields: Any) -> bool:
        allowed = {"decided_by", "decided_at", "edited_payload", "expires_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot set approval fields: {sorted(bad)}")
        fields = {k: (_dumps(v) if k == "edited_payload" else v) for k, v in fields.items()}
        sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
        with self._tx() as db:
            cur = db.execute(f"UPDATE approvals SET {sets} WHERE id=? AND status=?",
                             (to_status, *fields.values(), approval_id, from_status))
            return cur.rowcount == 1

    # ---- ledger
    @staticmethod
    def _ledger(row: sqlite3.Row) -> LedgerEntry:
        d = dict(row)
        d["counted"] = bool(d["counted"])
        return LedgerEntry(**d)

    def _get_ledger(self, entry_id: int) -> LedgerEntry:
        row = self._one("SELECT * FROM ledger WHERE id=?", (entry_id,))
        if row is None:
            raise KeyError(entry_id)
        return self._ledger(row)

    def ledger_reserve(self, user: str, run_id: str | None, endpoint: str, estimate: int,
                       idempotency_key: str) -> LedgerEntry:
        with self._tx() as db:
            cur = db.execute("INSERT INTO ledger (user, run_id, endpoint, estimate, idempotency_key,"
                             " status_code, counted, state, ts) VALUES (?,?,?,?,?,NULL,0,'reserved',?)",
                             (user, run_id, endpoint, estimate, idempotency_key, time.time()))
            entry_id = cur.lastrowid
        return self._get_ledger(entry_id)

    def ledger_settle(self, entry_id: int, status_code: int) -> LedgerEntry:
        counted = int(200 <= status_code < 400)
        with self._tx() as db:
            db.execute("UPDATE ledger SET status_code=?, counted=?, state='settled' WHERE id=?",
                       (status_code, counted, entry_id))
        return self._get_ledger(entry_id)

    def ledger_release(self, entry_id: int) -> None:
        with self._tx() as db:
            db.execute("UPDATE ledger SET state='released' WHERE id=? AND state='reserved'",
                       (entry_id,))

    def release_run_reservations(self, run_id: str) -> int:
        with self._tx() as db:
            cur = db.execute("UPDATE ledger SET state='released' WHERE run_id=? AND state='reserved'",
                             (run_id,))
            return cur.rowcount

    def ledger_used(self, user: str, *, since: float, run_id: str | None = None,
                    run_ids: list[str] | None = None) -> int:
        """Counted + reserved calls; optionally only for one run or a set of runs."""
        sql = ("SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN estimate ELSE 1 END),0) FROM ledger"
               " WHERE user=? AND ts>=? AND (state='reserved' OR (state='settled' AND counted=1))")
        args: list[Any] = [user, since]
        if run_id is not None:
            sql += " AND run_id=?"
            args.append(run_id)
        if run_ids is not None:
            if not run_ids:
                return 0
            sql += f" AND run_id IN ({','.join('?' * len(run_ids))})"
            args += list(run_ids)
        row = self._one(sql, tuple(args))
        return int(row[0])

    # ---- tool cache
    def cache_get(self, key: str) -> dict | None:
        row = self._one("SELECT value FROM tool_cache WHERE key=?", (key,))
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value: dict) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO tool_cache VALUES (?,?)", (key, canonical_json(value)))

    # ---- engineering specs and studies (keyed by project_id TEXT; see Task C3)
    def get_spec(self, project_id: str) -> dict | None:
        row = self._one("SELECT data FROM specs WHERE project_id=?", (project_id,))
        return json.loads(row[0]) if row else None

    def get_spec_updated_at(self, project_id: str) -> float | None:
        """When the spec was last written (None: never). PUT /spec's optimistic check."""
        row = self._one("SELECT updated_at FROM specs WHERE project_id=?", (project_id,))
        return float(row[0]) if row and row[0] is not None else None

    def put_spec(self, project_id: str, data: dict) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO specs (project_id, data, updated_at) VALUES (?,?,?)",
                       (project_id, canonical_json(data), time.time()))

    def create_study(self, study_id: str, project_id: str, data: dict, status: str) -> None:
        now = time.time()
        with self._tx() as db:
            db.execute("INSERT INTO studies (id, project_id, data, status, created_at, updated_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (study_id, project_id, canonical_json(data), status, now, now))

    def update_study(self, study_id: str, data: dict, status: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE studies SET data=?, status=?, updated_at=? WHERE id=?",
                       (canonical_json(data), status, time.time(), study_id))

    def get_study(self, study_id: str) -> dict | None:
        row = self._one("SELECT data FROM studies WHERE id=?", (study_id,))
        return json.loads(row[0]) if row else None

    def list_studies(self, project_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM studies WHERE project_id=? ORDER BY created_at DESC,"
                         " rowid DESC", (project_id,))
        return [json.loads(r[0]) for r in rows]
