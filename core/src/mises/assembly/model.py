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
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from ..cad.tools import PART_ID_RE

JointKind = Literal["fixed", "revolute", "slider", "cylindrical", "ball"]
#: Plan 10 shared contract (C2 <-> C3): a coupled joint follows another joint's value
CouplingKind = Literal["gear", "belt", "rack", "screw"]
#: how a loop-closing joint holds its two bodies: revolute (point + axis), ball (point only),
#: fixed (weld). C3 exports revolute/ball as MuJoCo `equality connect`, fixed as `weld`.
LoopType = Literal["revolute", "ball", "fixed"]


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


def _extras(joint) -> tuple:
    """The Plan 10 joint columns (coupling, design) as stored JSON (NULL when unset)."""
    return tuple(None if getattr(joint, k) is None
                 else json.dumps(getattr(joint, k).model_dump(mode="json", exclude_none=True))
                 for k in ("coupling", "design"))


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


class Coupling(BaseModel):
    """Plan 10 contract: this joint's value follows `partner_joint`'s:
    value = offset + ratio x partner value, in joint value units (deg per deg for gear and belt,
    mm per deg for rack and screw driven by a revolute, deg per mm for a revolute following a
    slider). An external gear pair has a negative ratio (-z1/z2). `offset` is the follower's
    value when the partner is at 0 (e.g. a planet's mesh phase). C3's MJCF export maps it to
    `equality joint polycoef = [offset, ratio]` converted to rad / m: see mises.mech.contract
    (the one statement of these units, with polycoef())."""
    kind: CouplingKind
    ratio: float = Field(allow_inf_nan=False)
    partner_joint: str = Field(min_length=1, max_length=64)
    offset: float = Field(default=0.0, allow_inf_nan=False)

    @field_validator("ratio")
    @classmethod
    def _nonzero(cls, v: float) -> float:
        if v == 0 or abs(v) > 1e6:
            raise ValueError("ratio must be non-zero and at most 1e6")
        return v


class JointDesign(BaseModel):
    """E27 design metadata of a joint (all optional): what carries it, how it is retained, how
    much play it has and what drives it. `mech_check` reads these."""
    bearing: str | None = Field(default=None, max_length=64,
                                description='catalog id ("bearing_608_deep_groove"), or '
                                            '"bushing" / "printed" / "none"')
    shaft: dict | None = Field(default=None, description='{"d_mm": 8, "material": "Steel 1018"}')
    retention: str | None = Field(default=None, max_length=64,
                                  description='"snap ring", "nut", "shoulder screw", "press", '
                                              '"set screw", "e-clip", "glue"')
    clearance_mm: float | None = Field(default=None, ge=0, le=10)
    actuator: dict | None = Field(default=None,
                                  description='{"item_id": "dynamixel_xl430_w250_t", "ratio": 1}')
    rom_deg: list[float] | None = Field(default=None, description="design range of motion "
                                        "[lo, hi] (deg or mm); may sit inside the hard limits")
    load_n: float | None = Field(default=None, ge=0, description="design radial load, N")
    fastener: str | None = Field(default=None, max_length=32, description='e.g. "M3"')


_Finite = Annotated[float, Field(allow_inf_nan=False)]


class Joint(BaseModel):
    id: str = Field(default_factory=_new_id)
    kind: JointKind
    a: JointRef
    b: JointRef
    value: _Finite | list[_Finite] = 0.0
    limits: list[float] | None = None
    coupling: Coupling | None = None
    design: JointDesign | None = None

    @model_validator(mode="after")
    def _check(self) -> "Joint":
        if self.limits is not None and len(self.limits) != 2:
            raise ValueError("limits must be [lo, hi]")
        if self.limits is not None and self.limits[0] > self.limits[1]:
            raise ValueError("limits lo must be <= hi")
        if self.coupling is not None:
            if self.kind not in ("revolute", "slider"):
                raise ValueError("only a revolute or slider joint can be coupled")
            if self.coupling.partner_joint == self.id:
                raise ValueError("a joint cannot be coupled to itself")
        return self

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        # the Plan 10 fields stay out of payloads that don't use them: the app's fixtures and
        # older readers see exactly the joints they did before
        d = handler(self)
        if isinstance(d, dict):
            for k in ("coupling", "design"):
                if d.get(k) is None:
                    d.pop(k, None)
        return d


class Loop(BaseModel):
    """Plan 10 contract: a loop-closure constraint `{kind: "loop", joint_a, joint_b, frame_a,
    frame_b}`. `joint_a` / `joint_b` are the INSTANCE ids of the two bodies the loop-closing
    joint connects (MuJoCo `connect` body1 / body2) and `frame_a` / `frame_b` that joint's frame
    on each (part coordinates); the two frames must coincide. The tree solve places every body
    through the ordinary joints; the loop solve then adjusts the non-driver joint values on the
    loop's path until the frames meet. `drivers`: joints held at their value (the mechanism's
    inputs); default the first movable joint on the path from the grounded side."""
    id: str = Field(default_factory=_new_id)
    kind: Literal["loop"] = "loop"
    joint_a: str
    joint_b: str
    frame_a: Frame
    frame_b: Frame
    type: LoopType = "revolute"
    drivers: list[str] | None = None


class Assembly(BaseModel):
    instances: list[Instance] = Field(default_factory=list)
    joints: list[Joint] = Field(default_factory=list)
    loops: list[Loop] = Field(default_factory=list)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assembly_instances (
  project_id TEXT, id TEXT, part_id TEXT, name TEXT, transform TEXT, fixed INTEGER, ord INTEGER,
  PRIMARY KEY (project_id, id));
CREATE TABLE IF NOT EXISTS assembly_joints (
  project_id TEXT, id TEXT, kind TEXT, a TEXT, b TEXT, value TEXT, limits TEXT, ord INTEGER,
  PRIMARY KEY (project_id, id));
CREATE TABLE IF NOT EXISTS assembly_auto (
  project_id TEXT, id TEXT, PRIMARY KEY (project_id, id));
CREATE TABLE IF NOT EXISTS assembly_loops (
  project_id TEXT, id TEXT, spec TEXT, ord INTEGER, PRIMARY KEY (project_id, id));
CREATE TABLE IF NOT EXISTS assembly_mechs (
  project_id TEXT, id TEXT, spec TEXT, ord INTEGER, PRIMARY KEY (project_id, id));
CREATE TABLE IF NOT EXISTS assembly_animations (
  project_id TEXT, id TEXT, name TEXT, spec TEXT, ord INTEGER,
  PRIMARY KEY (project_id, id), UNIQUE (project_id, name));
"""

#: saved animations kept per build (Plan 9 G-D)
MAX_ANIMATIONS = 50

_INSTANCE_FIELDS = {"part_id", "name", "transform", "fixed"}
_JOINT_FIELDS = {"kind", "a", "b", "value", "limits", "coupling", "design"}


class AssemblyStore:
    """SQLite-backed assembly data, keyed by a plain ``project_id`` string."""

    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(assembly_joints)")}
        for col in ("coupling", "design"):          # Plan 10: added to existing databases
            if col not in cols:
                self._db.execute(f"ALTER TABLE assembly_joints ADD COLUMN {col} TEXT")

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
                self._db.execute("DELETE FROM assembly_auto WHERE project_id=?", (project_id,))
                self._db.execute("DELETE FROM assembly_animations WHERE project_id=?",
                                 (project_id,))
                self._db.execute("DELETE FROM assembly_loops WHERE project_id=?", (project_id,))
                self._db.execute("DELETE FROM assembly_mechs WHERE project_id=?", (project_id,))
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
            lrows = self._db.execute(
                "SELECT * FROM assembly_loops WHERE project_id=? ORDER BY ord, id",
                (project_id,)).fetchall()
        instances = [self._instance(r) for r in irows]
        joints = [self._joint(r) for r in jrows]
        loops = [Loop.model_validate({**json.loads(r["spec"]), "id": r["id"]}) for r in lrows]
        return Assembly(instances=instances, joints=joints, loops=loops)

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
        keys = row.keys()
        extra = {k: json.loads(row[k]) for k in ("coupling", "design")
                 if k in keys and row[k] is not None}
        return Joint(id=row["id"], kind=row["kind"], a=JointRef(**json.loads(row["a"])),
                     b=JointRef(**json.loads(row["b"])), value=json.loads(row["value"]),
                     limits=json.loads(row["limits"]) if row["limits"] is not None else None,
                     **extra)

    # ---- instance mutations
    def add_instance(self, project_id: str, inst: Instance, ord: int | None = None) -> Instance:
        """`ord`: the place in the list (an undo puts an instance back where it was)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO assembly_instances (project_id, id, part_id, name, transform, fixed,"
                " ord) VALUES (?,?,?,?,?,?,?)",
                (project_id, inst.id, inst.part_id, inst.name, json.dumps(inst.transform),
                 int(inst.fixed),
                 self._next_ord("assembly_instances", project_id) if ord is None else int(ord)))
        return inst

    def orders(self, project_id: str) -> dict[str, dict[str, int]]:
        """{"instances": {id: ord}, "joints": {id: ord}}: where each sits in its list."""
        with self._lock:
            ins = self._db.execute("SELECT id, ord FROM assembly_instances WHERE project_id=?",
                                   (project_id,)).fetchall()
            js = self._db.execute("SELECT id, ord FROM assembly_joints WHERE project_id=?",
                                  (project_id,)).fetchall()
        return {"instances": {r[0]: int(r[1]) for r in ins}, "joints": {r[0]: int(r[1]) for r in js}}

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
            self._db.execute("DELETE FROM assembly_auto WHERE project_id=? AND id=?",
                             (project_id, iid))
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
            self._db.execute("DELETE FROM assembly_auto WHERE project_id=? AND id=?",
                             (project_id, iid))
            for r in self._db.execute("SELECT id, spec FROM assembly_loops WHERE project_id=?",
                                      (project_id,)).fetchall():
                spec = json.loads(r["spec"])
                if iid in (spec.get("joint_a"), spec.get("joint_b")):
                    self._db.execute("DELETE FROM assembly_loops WHERE project_id=? AND id=?",
                                     (project_id, r["id"]))
        return removed

    # ---- Plan 9 D10: instances the core placed by itself (assembly/autoplace.py), until an
    # agent joins them where they really go (assembly_add_part reuses one instead of a copy)
    def mark_auto(self, project_id: str, iid: str, on: bool = True) -> None:
        with self._lock:
            if on:
                self._db.execute("INSERT OR IGNORE INTO assembly_auto VALUES (?,?)",
                                 (project_id, iid))
            else:
                self._db.execute("DELETE FROM assembly_auto WHERE project_id=? AND id=?",
                                 (project_id, iid))

    def auto_ids(self, project_id: str) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT id FROM assembly_auto WHERE project_id=?",
                                    (project_id,)).fetchall()
        return {r[0] for r in rows}

    # ---- Plan 9 D29: take a part's instances out, and put them back exactly (Undo)
    def take_out(self, project_id: str, part_ids: set[str] | list[str]) -> dict:
        """Remove every instance of these parts (and every joint touching one). Returns what an
        undo needs: {"instances": [...], "joints": [...]}, each with its list place (`_ord`) and
        the instances' placed-by-core mark (`_auto`)."""
        with self._lock:
            asm = self.get(project_id)
            used = [i for i in asm.instances if i.part_id in set(part_ids)]
            ids = {i.id for i in used}
            joints = [j for j in asm.joints if j.a.instance_id in ids or j.b.instance_id in ids]
            loops = [lp for lp in asm.loops if lp.joint_a in ids or lp.joint_b in ids]
            orders = self.orders(project_id)
            auto = self.auto_ids(project_id)
            for i in used:
                self.delete_instance(project_id, i.id)
        return {"instances": [{**i.model_dump(mode="json"), "_ord": orders["instances"].get(i.id),
                               "_auto": i.id in auto} for i in used],
                "joints": [{**j.model_dump(mode="json"), "_ord": orders["joints"].get(j.id)}
                           for j in joints],
                **({"loops": [lp.model_dump(mode="json") for lp in loops]} if loops else {})}

    def put_back(self, project_id: str, data: dict) -> tuple[int, int]:
        """Undo of take_out: the instances (ids, places, marks), then the joints whose two ends
        exist. Pieces that can't come back (an id taken again) are skipped. (instances, joints)."""
        ni = nj = 0
        marked: list[str] = []
        with self._lock:
            marked.extend(self.auto_ids(project_id))     # a joint coming back never unmarks these
            for raw in data.get("instances") or []:
                try:
                    inst = Instance.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})
                    self.add_instance(project_id, inst, ord=raw.get("_ord"))
                    if raw.get("_auto"):
                        marked.append(inst.id)
                    ni += 1
                except Exception:  # noqa: BLE001 - one bad piece never stops the rest
                    continue
            for raw in data.get("joints") or []:
                try:
                    self.add_joint(project_id, Joint.model_validate(
                        {k: v for k, v in raw.items() if not k.startswith("_")}), ord=raw.get("_ord"))
                    nj += 1
                except Exception:  # noqa: BLE001 - its other end is gone now
                    continue
            for raw in data.get("loops") or []:
                try:
                    self.add_loop(project_id, Loop.model_validate(raw))
                except Exception:  # noqa: BLE001 - one of its bodies is gone now
                    continue
            for iid in marked:              # after the joints (a joint clears its b side's mark)
                self.mark_auto(project_id, iid)
        return ni, nj

    # ---- joint mutations
    def add_joint(self, project_id: str, joint: Joint, ord: int | None = None) -> Joint:
        known = {i.id for i in self.get(project_id).instances}
        for ref in (joint.a, joint.b):
            if ref.instance_id not in known:
                raise KeyError(ref.instance_id)
        with self._lock:
            self._db.execute(
                "INSERT INTO assembly_joints (project_id, id, kind, a, b, value, limits, ord,"
                " coupling, design) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project_id, joint.id, joint.kind, joint.a.model_dump_json(),
                 joint.b.model_dump_json(), json.dumps(joint.value),
                 json.dumps(joint.limits) if joint.limits is not None else None,
                 self._next_ord("assembly_joints", project_id) if ord is None else int(ord),
                 *_extras(joint)))
            # the joined (b) side is placed where it goes: no longer the core's own placement
            # (a part only joined TO keeps it: assembly_add_part may still ground it)
            self._db.execute("DELETE FROM assembly_auto WHERE project_id=? AND id=?",
                             (project_id, joint.b.instance_id))
        return joint

    def update_joint(self, project_id: str, jid: str, **fields) -> Joint:
        bad = set(fields) - _JOINT_FIELDS
        if bad:
            raise ValueError(f"cannot update joint fields: {sorted(bad)}")
        cur = self.get_joint(project_id, jid)
        merged = cur.model_copy(update=fields)
        merged = Joint.model_validate(merged.model_dump())  # re-run validators
        if merged.limits is not None and isinstance(merged.value, (int, float)):
            # QA A-1: tightening a limit below the current value kept the old value (45 with
            # limits [-90, 30]); the stored value now follows the pose the solver draws
            lo, hi = merged.limits
            merged = merged.model_copy(update={"value": min(max(float(merged.value), lo), hi)})
        with self._lock:
            self._db.execute(
                "UPDATE assembly_joints SET kind=?, a=?, b=?, value=?, limits=?, coupling=?,"
                " design=? WHERE project_id=? AND id=?",
                (merged.kind, merged.a.model_dump_json(), merged.b.model_dump_json(),
                 json.dumps(merged.value),
                 json.dumps(merged.limits) if merged.limits is not None else None,
                 *_extras(merged), project_id, jid))
        return merged

    def set_values(self, project_id: str, values: dict[str, object]) -> None:
        """Store solved joint values (loop-solved and coupled joints that follow a driver), so a
        reader solving the stored values without the loop solve still sees the closed pose."""
        with self._lock:
            for jid, v in values.items():
                self._db.execute("UPDATE assembly_joints SET value=? WHERE project_id=? AND id=?",
                                 (json.dumps(v), project_id, jid))

    def delete_joint(self, project_id: str, jid: str) -> None:
        with self._lock:
            cur = self._db.execute("DELETE FROM assembly_joints WHERE project_id=? AND id=?",
                                   (project_id, jid))
            if cur.rowcount == 0:
                raise KeyError(jid)
            # a joint coupled to the removed one no longer follows anything
            for r in self._db.execute("SELECT id, coupling FROM assembly_joints WHERE"
                                      " project_id=? AND coupling IS NOT NULL",
                                      (project_id,)).fetchall():
                if (json.loads(r["coupling"]) or {}).get("partner_joint") == jid:
                    self._db.execute("UPDATE assembly_joints SET coupling=NULL WHERE"
                                     " project_id=? AND id=?", (project_id, r["id"]))

    # ---- Plan 10: loop-closure constraints (closed chains: four-bar, Jansen, grippers)
    def add_loop(self, project_id: str, loop: Loop, ord: int | None = None) -> Loop:
        known = {i.id for i in self.get(project_id).instances}
        for iid in (loop.joint_a, loop.joint_b):
            if iid not in known:
                raise KeyError(iid)
        with self._lock:
            self._db.execute(
                "INSERT INTO assembly_loops (project_id, id, spec, ord) VALUES (?,?,?,?)",
                (project_id, loop.id, json.dumps(loop.model_dump(mode="json", exclude={"id"})),
                 self._next_ord("assembly_loops", project_id) if ord is None else int(ord)))
        return loop

    def delete_loop(self, project_id: str, lid: str) -> None:
        with self._lock:
            cur = self._db.execute("DELETE FROM assembly_loops WHERE project_id=? AND id=?",
                                   (project_id, lid))
            if cur.rowcount == 0:
                raise KeyError(lid)

    # ---- named animations (Plan 9 G-D): an agent's motion, kept so a view card can replay it
    def save_animation(self, project_id: str, name: str, spec: dict) -> dict:
        """Store `spec` ({steps, loops, duration_s}) under `name` in the build; saving the same
        name again replaces it (same id) and moves it to newest (E5: ordered by recency, so Play
        picks the last motion the agent saved). Returns {id, name, **spec}."""
        body = {k: v for k, v in spec.items() if k not in ("id", "name")}
        with self._lock:
            row = self._db.execute("SELECT id FROM assembly_animations WHERE project_id=? AND"
                                   " name=?", (project_id, name)).fetchone()
            aid = row["id"] if row else _new_id()
            if row:
                self._db.execute("UPDATE assembly_animations SET spec=?, ord=? WHERE project_id=?"
                                 " AND id=?", (json.dumps(body),
                                               self._next_ord("assembly_animations", project_id),
                                               project_id, aid))
            else:
                self._db.execute("INSERT INTO assembly_animations VALUES (?,?,?,?,?)",
                                 (project_id, aid, name, json.dumps(body),
                                  self._next_ord("assembly_animations", project_id)))
                # a build keeps its newest MAX_ANIMATIONS; the oldest go
                self._db.execute(
                    "DELETE FROM assembly_animations WHERE project_id=? AND id NOT IN (SELECT id"
                    " FROM assembly_animations WHERE project_id=? ORDER BY ord DESC LIMIT ?)",
                    (project_id, project_id, MAX_ANIMATIONS))
        return {"id": aid, "name": name, **body}

    def get_animation(self, project_id: str, ref: str) -> dict:
        """A saved animation of the build, by id first, else by name. KeyError if none."""
        with self._lock:
            row = self._db.execute("SELECT * FROM assembly_animations WHERE project_id=? AND"
                                   " (id=? OR name=?) ORDER BY id=? DESC LIMIT 1",
                                   (project_id, ref, ref, ref)).fetchone()
        if row is None:
            raise KeyError(ref)
        return {"id": row["id"], "name": row["name"], **json.loads(row["spec"])}

    def list_animations(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM assembly_animations WHERE project_id=?"
                                    " ORDER BY ord, id", (project_id,)).fetchall()
        return [{"id": r["id"], "name": r["name"], **json.loads(r["spec"])} for r in rows]

    # ---- Plan 10: library mechanisms inserted in a build (mech_insert): the card, the params
    # and which instances / joints / loops / parts each plan key became (mech_check reads it)
    def save_mech(self, project_id: str, gid: str, record: dict) -> dict:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO assembly_mechs (project_id, id, spec, ord) VALUES"
                " (?,?,?,COALESCE((SELECT ord FROM assembly_mechs WHERE project_id=? AND id=?),"
                " (SELECT COALESCE(MAX(ord), -1) + 1 FROM assembly_mechs WHERE project_id=?)))",
                (project_id, gid, json.dumps(record), project_id, gid, project_id))
        return {"id": gid, **record}

    def get_mech(self, project_id: str, gid: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM assembly_mechs WHERE project_id=? AND id=?",
                                   (project_id, gid)).fetchone()
        if row is None:
            raise KeyError(gid)
        return {"id": row["id"], **json.loads(row["spec"])}

    def list_mechs(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM assembly_mechs WHERE project_id=? ORDER BY"
                                    " ord, id", (project_id,)).fetchall()
        return [{"id": r["id"], **json.loads(r["spec"])} for r in rows]
