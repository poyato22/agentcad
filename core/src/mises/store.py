"""SQLite store: runs, policies, hash-chained steps, approvals, ledger, tool cache."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, provider TEXT, model TEXT, task TEXT,
  status TEXT, branch TEXT, onshape_calls INTEGER, input_tokens INTEGER, output_tokens INTEGER,
  usd REAL, created_at REAL, updated_at REAL);
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
"""

_RUN_FIELDS = {"status", "branch", "agent", "provider", "model", "task", "parent_id",
               "session_ref", "owner_pid"}
_APPROVAL_JSON = {"payload", "deltas", "edited_payload"}


def _dumps(v: Any) -> str | None:
    return None if v is None else canonical_json(v)


def _step_hash(run_id: str, kind: str, payload: dict, prev_hash: str | None) -> str:
    return sha256_hex(canonical_json(
        {"run_id": run_id, "kind": str(kind), "payload": payload, "prev_hash": prev_hash}))


class Store:
    def __init__(self, path: Path | str):
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
                   session_ref: str | None = None) -> AgentRun:
        now = time.time()
        rid = uuid.uuid4().hex[:12]
        with self._tx() as db:
            db.execute("INSERT INTO runs (id, parent_id, agent, provider, model, task, status,"
                       " branch, onshape_calls, input_tokens, output_tokens, usd, created_at,"
                       " updated_at, owner_pid, external, session_ref)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rid, parent_id, agent, provider, model, task, "running", None,
                        0, 0, 0, 0.0, now, now, os.getpid(), int(external), session_ref))
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
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._tx() as db:
                db.execute(f"UPDATE runs SET {sets}, updated_at=? WHERE id=?",
                           (*fields.values(), time.time(), run_id))
        return self.get_run(run_id)

    def list_runs(self, limit: int = 50) -> list[AgentRun]:
        rows = self._all("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
        return [AgentRun(**dict(r)) for r in rows]

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
