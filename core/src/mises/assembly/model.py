"""Assembly data model and its store.

The store is deliberately ``projects``-agnostic: every row is keyed by a ``project_id`` string
(TEXT) and nothing here knows what a project is. It lives in its own SQLite file so it merges
cleanly with the ``projects`` table added elsewhere.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..cad.tools import PART_ID_RE

JointKind = Literal["fixed", "revolute", "slider", "cylindrical", "ball"]


def _identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def check_part_id(v: str) -> str:
    """The shared part-id rule: also keeps every parts_root/<part_id> path inside the tree."""
    if not isinstance(v, str) or not PART_ID_RE.fullmatch(v):
        raise ValueError(f"part_id {v!r} must match {PART_ID_RE.pattern}")
    return v


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Frame(BaseModel):
    """A frame in part coordinates: an explicit origin+axis, or a face id resolved to the face's
    centre and outward normal when geometry is available."""
    origin: list[float] | None = None
    axis: list[float] | None = None
    face_id: str | None = None

    @model_validator(mode="after")
    def _one_form(self) -> "Frame":
        if self.face_id is None and (self.origin is None or self.axis is None):
            raise ValueError("frame needs origin+axis or a face_id")
        for name, v in (("origin", self.origin), ("axis", self.axis)):
            if v is not None and len(v) != 3:
                raise ValueError(f"frame {name} must be 3 numbers")
        return self


class JointRef(BaseModel):
    instance_id: str
    frame: Frame


class Instance(BaseModel):
    id: str = Field(default_factory=_new_id)
    part_id: str
    name: str
    transform: list[list[float]] = Field(default_factory=_identity)
    fixed: bool = False

    @field_validator("part_id")
    @classmethod
    def _part_id(cls, v: str) -> str:
        return check_part_id(v)


class Joint(BaseModel):
    id: str = Field(default_factory=_new_id)
    kind: JointKind
    a: JointRef
    b: JointRef
    value: float | list[float] = 0.0
    limits: list[float] | None = None

    @model_validator(mode="after")
    def _check(self) -> "Joint":
        if self.limits is not None and len(self.limits) != 2:
            raise ValueError("limits must be [lo, hi]")
        if self.limits is not None and self.limits[0] > self.limits[1]:
            raise ValueError("limits lo must be <= hi")
        return self


class Assembly(BaseModel):
    instances: list[Instance] = Field(default_factory=list)
    joints: list[Joint] = Field(default_factory=list)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assembly_instances (
  project_id TEXT, id TEXT, part_id TEXT, name TEXT, transform TEXT, fixed INTEGER, ord INTEGER,
  PRIMARY KEY (project_id, id));
CREATE TABLE IF NOT EXISTS assembly_joints (
  project_id TEXT, id TEXT, kind TEXT, a TEXT, b TEXT, value TEXT, limits TEXT, ord INTEGER,
  PRIMARY KEY (project_id, id));
"""

_INSTANCE_FIELDS = {"part_id", "name", "transform", "fixed"}
_JOINT_FIELDS = {"kind", "a", "b", "value", "limits"}


class AssemblyStore:
    """SQLite-backed assembly data, keyed by a plain ``project_id`` string."""

    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def _next_ord(self, table: str, project_id: str) -> int:
        row = self._db.execute(f"SELECT COALESCE(MAX(ord), -1) + 1 FROM {table} WHERE project_id=?",
                               (project_id,)).fetchone()
        return int(row[0])

    def purge(self, project_id: str) -> None:
        """Drop a purged build's instances and joints (Empty Trash)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("DELETE FROM assembly_joints WHERE project_id=?", (project_id,))
                self._db.execute("DELETE FROM assembly_instances WHERE project_id=?",
                                 (project_id,))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def part_refs_outside(self, project_id: str) -> set[str]:
        """Part ids that some OTHER build's assembly places (their files must stay)."""
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT part_id FROM assembly_instances"
                                    " WHERE project_id<>?", (project_id,)).fetchall()
        return {r[0] for r in rows}

    # ---- reads
    def get(self, project_id: str) -> Assembly:
        with self._lock:
            irows = self._db.execute(
                "SELECT * FROM assembly_instances WHERE project_id=? ORDER BY ord, id",
                (project_id,)).fetchall()
            jrows = self._db.execute(
                "SELECT * FROM assembly_joints WHERE project_id=? ORDER BY ord, id",
                (project_id,)).fetchall()
        instances = [self._instance(r) for r in irows]
        joints = [self._joint(r) for r in jrows]
        return Assembly(instances=instances, joints=joints)

    def get_instance(self, project_id: str, iid: str) -> Instance:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM assembly_instances WHERE project_id=? AND id=?",
                (project_id, iid)).fetchone()
        if row is None:
            raise KeyError(iid)
        return self._instance(row)

    def get_joint(self, project_id: str, jid: str) -> Joint:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM assembly_joints WHERE project_id=? AND id=?",
                (project_id, jid)).fetchone()
        if row is None:
            raise KeyError(jid)
        return self._joint(row)

    @staticmethod
    def _instance(row: sqlite3.Row) -> Instance:
        return Instance(id=row["id"], part_id=row["part_id"], name=row["name"],
                        transform=json.loads(row["transform"]), fixed=bool(row["fixed"]))

    @staticmethod
    def _joint(row: sqlite3.Row) -> Joint:
        return Joint(id=row["id"], kind=row["kind"], a=JointRef(**json.loads(row["a"])),
                     b=JointRef(**json.loads(row["b"])), value=json.loads(row["value"]),
                     limits=json.loads(row["limits"]) if row["limits"] is not None else None)

    # ---- instance mutations
    def add_instance(self, project_id: str, inst: Instance) -> Instance:
        with self._lock:
            self._db.execute(
                "INSERT INTO assembly_instances (project_id, id, part_id, name, transform, fixed,"
                " ord) VALUES (?,?,?,?,?,?,?)",
                (project_id, inst.id, inst.part_id, inst.name, json.dumps(inst.transform),
                 int(inst.fixed), self._next_ord("assembly_instances", project_id)))
        return inst

    def update_instance(self, project_id: str, iid: str, **fields) -> Instance:
        bad = set(fields) - _INSTANCE_FIELDS
        if bad:
            raise ValueError(f"cannot update instance fields: {sorted(bad)}")
        cur = self.get_instance(project_id, iid)
        merged = Instance.model_validate({**cur.model_dump(), **fields})  # re-run validators
        with self._lock:
            self._db.execute(
                "UPDATE assembly_instances SET part_id=?, name=?, transform=?, fixed=?"
                " WHERE project_id=? AND id=?",
                (merged.part_id, merged.name, json.dumps(merged.transform), int(merged.fixed),
                 project_id, iid))
        return merged

    def delete_instance(self, project_id: str, iid: str) -> list[str]:
        """Delete the instance and every joint that referenced it; returns the removed joint ids."""
        removed: list[str] = []
        with self._lock:
            if self._db.execute("SELECT 1 FROM assembly_instances WHERE project_id=? AND id=?",
                                (project_id, iid)).fetchone() is None:
                raise KeyError(iid)
            for r in self._db.execute(
                    "SELECT id, a, b FROM assembly_joints WHERE project_id=?", (project_id,)):
                if json.loads(r["a"])["instance_id"] == iid or \
                        json.loads(r["b"])["instance_id"] == iid:
                    removed.append(r["id"])
            if removed:
                self._db.execute(
                    f"DELETE FROM assembly_joints WHERE project_id=? AND id IN "
                    f"({','.join('?' * len(removed))})", (project_id, *removed))
            self._db.execute("DELETE FROM assembly_instances WHERE project_id=? AND id=?",
                             (project_id, iid))
        return removed

    # ---- joint mutations
    def add_joint(self, project_id: str, joint: Joint) -> Joint:
        known = {i.id for i in self.get(project_id).instances}
        for ref in (joint.a, joint.b):
            if ref.instance_id not in known:
                raise KeyError(ref.instance_id)
        with self._lock:
            self._db.execute(
                "INSERT INTO assembly_joints (project_id, id, kind, a, b, value, limits, ord)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (project_id, joint.id, joint.kind, joint.a.model_dump_json(),
                 joint.b.model_dump_json(), json.dumps(joint.value),
                 json.dumps(joint.limits) if joint.limits is not None else None,
                 self._next_ord("assembly_joints", project_id)))
        return joint

    def update_joint(self, project_id: str, jid: str, **fields) -> Joint:
        bad = set(fields) - _JOINT_FIELDS
        if bad:
            raise ValueError(f"cannot update joint fields: {sorted(bad)}")
        cur = self.get_joint(project_id, jid)
        merged = cur.model_copy(update=fields)
        merged = Joint.model_validate(merged.model_dump())  # re-run validators
        with self._lock:
            self._db.execute(
                "UPDATE assembly_joints SET kind=?, a=?, b=?, value=?, limits=?"
                " WHERE project_id=? AND id=?",
                (merged.kind, merged.a.model_dump_json(), merged.b.model_dump_json(),
                 json.dumps(merged.value),
                 json.dumps(merged.limits) if merged.limits is not None else None,
                 project_id, jid))
        return merged

    def delete_joint(self, project_id: str, jid: str) -> None:
        with self._lock:
            cur = self._db.execute("DELETE FROM assembly_joints WHERE project_id=? AND id=?",
                                   (project_id, jid))
            if cur.rowcount == 0:
                raise KeyError(jid)
