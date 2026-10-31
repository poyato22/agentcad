"""The concept stage of a build (Plan 9 G-A: D15 concept sketch, D25 pitch options).

Before a design-heavy build gets parts, its lead shows the user what it will look like:
- `pitch_options` (design class): 2-3 directions, each a massing variant shown as front/side
  silhouettes plus a sketch, mood words, CMF and trade-offs; one is the honest-engineering
  `baseline`. The user picks one or mixes them on the card.
- `concept_sketch`: a rough massing model built in the sandboxed build worker and rendered as a
  line sketch (3/4 + side, a ground disc and a human-scale figure for scale). Every call is a new
  version of the build's sketch; a revision links `parent_sketch_id`.
- The user circles (circle / lasso) and pins areas of a sketch image with comments. Marks are
  stored in normalised image coordinates (x right, y down, 0..1) with that image's camera, so
  they map back to 3D. Sending them posts a message to the build's lead with the annotated
  images attached; the lead answers with a revised sketch.
- "Approve sketch" or "Skip sketch" set the build's concept gate. For a build whose design
  class is `design`, `mirror_part_create` of a new non-massing part is refused ("needs concept
  approval") until then. Mixed class: recommended, not enforced. Engineering: off.

The record (wave-3 shared contract): `{status: none|pitching|sketching|approved|skipped,
sketch_ids[], chosen_option}` per build, plus the sketch versions, pitches and feedback rounds.
Everything the user typed (comments) and the agent wrote (notes, callouts, option names) is
text: the app shows it as plain text and agents read it back only inside a data block.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..store import Store
from ..types import ErrorCode, MisesError

STATUSES = ("none", "pitching", "sketching", "approved", "skipped")
OPEN = ("approved", "skipped")            # the gate is open: parts may be built
SKETCH_ID_RE = re.compile(r"^cks_[0-9a-f]{12}$")
PITCH_ID_RE = re.compile(r"^ckp_[0-9a-f]{12}$")
FEEDBACK_ID_RE = re.compile(r"^ckf_[0-9a-f]{12}$")
MAX_SKETCHES = 200                        # per build
MAX_MARKS = 40                            # per feedback round
COMMENT_CHARS = 1000
GENERAL_CHARS = 4000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts (
  project_id TEXT PRIMARY KEY, data TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS concept_sketches (
  id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, data TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS concept_pitches (
  id TEXT PRIMARY KEY, project_id TEXT, data TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS concept_feedback (
  id TEXT PRIMARY KEY, project_id TEXT, sketch_id TEXT, data TEXT, created_at REAL);
"""

Comment = Annotated[str, StringConstraints(max_length=COMMENT_CHARS)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]


# ---- user marks (normalised image coordinates: x right, y down, 0..1 of the image)

class _Mark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: int = Field(0, ge=0, le=7, description="index into the sketch's images[]")
    comment: Comment = ""


class CircleMark(_Mark):
    kind: Literal["circle"] = "circle"
    cx: Unit
    cy: Unit
    r: Annotated[float, Field(gt=0.0, le=1.0)]


class LassoMark(_Mark):
    kind: Literal["lasso"] = "lasso"
    points: list[Annotated[list[Unit], Field(min_length=2, max_length=2)]] = Field(
        ..., min_length=3, max_length=400)


class PinMark(_Mark):
    kind: Literal["pin"] = "pin"
    x: Unit
    y: Unit


Mark = Annotated[Union[CircleMark, LassoMark, PinMark], Field(discriminator="kind")]


class FeedbackIn(BaseModel):
    """POST /concept/sketches/{id}/feedback: the marks on the sketch's images and a general
    comment. At least one of them."""
    model_config = ConfigDict(extra="forbid")
    marks: list[Mark] = Field(default_factory=list, max_length=MAX_MARKS)
    comment: Annotated[str, StringConstraints(max_length=GENERAL_CHARS)] = ""


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sketch_id: Annotated[str, StringConstraints(pattern=SKETCH_ID_RE.pattern)] | None = None


class MixPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    take: Annotated[str, StringConstraints(max_length=200)] = Field(
        "", description="what to take from this option, e.g. 'torso' or 'legs'")
    comment: Comment = ""


class ChooseIn(BaseModel):
    """POST /concept/pitches/{id}/choose: pick one option, or mix several (each with what to
    take from it), plus a comment."""
    model_config = ConfigDict(extra="forbid")
    option: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    mix: list[MixPart] = Field(default_factory=list, max_length=6)
    comment: Annotated[str, StringConstraints(max_length=GENERAL_CHARS)] = ""


def bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def empty_record(project_id: str) -> dict:
    return {"project_id": project_id, "status": "none", "sketch_ids": [],
            "chosen_option": None, "pitch_ids": [], "approved_sketch_id": None,
            "updated_at": None}


class ConceptStore:
    """The concept records, sketch versions, pitches and feedback rounds, in the core's SQLite
    database (own tables, like the BOM's)."""

    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store._db.executescript(_SCHEMA)

    # ---- the build's record
    def get(self, project_id: str) -> dict:
        row = self.store._one("SELECT data FROM concepts WHERE project_id=?", (project_id,))
        rec = empty_record(project_id)
        if row:
            rec.update(json.loads(row[0]))
        return rec

    def _put(self, db, rec: dict) -> dict:
        rec = {**rec, "updated_at": time.time()}
        db.execute("INSERT OR REPLACE INTO concepts VALUES (?,?,?)",
                   (rec["project_id"], json.dumps(rec), rec["updated_at"]))
        return rec

    def update(self, project_id: str, **fields) -> dict:
        status = fields.get("status")
        if status is not None and status not in STATUSES:
            raise bad(f"unknown concept status {status!r}")
        with self.store._tx() as db:
            row = db.execute("SELECT data FROM concepts WHERE project_id=?",
                             (project_id,)).fetchone()
            rec = empty_record(project_id)
            if row:
                rec.update(json.loads(row[0]))
            rec.update(fields)
            return self._put(db, rec)

    # ---- sketch versions
    def add_sketch(self, project_id: str, data: dict) -> dict:
        """Store a new sketch version (version = the build's count + 1). The record's status
        moves to "sketching" unless the gate is already open (a later re-sketch never closes
        it again)."""
        with self.store._tx() as db:
            n = db.execute("SELECT COUNT(*) FROM concept_sketches WHERE project_id=?",
                           (project_id,)).fetchone()[0]
            if n >= MAX_SKETCHES:
                raise bad(f"a build holds at most {MAX_SKETCHES} concept sketches")
            now = time.time()
            sk = {**data, "project_id": project_id, "build_id": project_id,
                  "version": n + 1, "created_at": now}
            db.execute("INSERT INTO concept_sketches VALUES (?,?,?,?,?)",
                       (sk["sketch_id"], project_id, sk["version"], json.dumps(sk), now))
            row = db.execute("SELECT data FROM concepts WHERE project_id=?",
                             (project_id,)).fetchone()
            rec = empty_record(project_id)
            if row:
                rec.update(json.loads(row[0]))
            rec["sketch_ids"] = [*rec["sketch_ids"], sk["sketch_id"]]
            if rec["status"] not in OPEN:
                rec["status"] = "sketching"
            self._put(db, rec)
        return sk

    def get_sketch(self, sketch_id: str) -> dict:
        if not SKETCH_ID_RE.match(sketch_id or ""):
            raise MisesError(ErrorCode.NOT_FOUND, f"no concept sketch {sketch_id!r}")
        row = self.store._one("SELECT data FROM concept_sketches WHERE id=?", (sketch_id,))
        if row is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no concept sketch {sketch_id!r}")
        return json.loads(row[0])

    def sketches(self, project_id: str) -> list[dict]:
        rows = self.store._all("SELECT data FROM concept_sketches WHERE project_id=? ORDER BY"
                               " version", (project_id,))
        return [json.loads(r[0]) for r in rows]

    def latest_sketch(self, project_id: str) -> dict | None:
        row = self.store._one("SELECT data FROM concept_sketches WHERE project_id=? ORDER BY"
                              " version DESC LIMIT 1", (project_id,))
        return json.loads(row[0]) if row else None

    # ---- pitches
    def add_pitch(self, project_id: str, data: dict) -> dict:
        with self.store._tx() as db:
            now = time.time()
            p = {**data, "project_id": project_id, "build_id": project_id, "created_at": now}
            db.execute("INSERT INTO concept_pitches VALUES (?,?,?,?)",
                       (p["pitch_id"], project_id, json.dumps(p), now))
            row = db.execute("SELECT data FROM concepts WHERE project_id=?",
                             (project_id,)).fetchone()
            rec = empty_record(project_id)
            if row:
                rec.update(json.loads(row[0]))
            rec["pitch_ids"] = [*rec.get("pitch_ids", []), p["pitch_id"]]
            if rec["status"] not in OPEN:
                rec["status"] = "pitching"
            self._put(db, rec)
        return p

    def get_pitch(self, pitch_id: str) -> dict:
        if not PITCH_ID_RE.match(pitch_id or ""):
            raise MisesError(ErrorCode.NOT_FOUND, f"no pitch {pitch_id!r}")
        row = self.store._one("SELECT data FROM concept_pitches WHERE id=?", (pitch_id,))
        if row is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no pitch {pitch_id!r}")
        return json.loads(row[0])

    def put_pitch(self, pitch: dict) -> dict:
        with self.store._tx() as db:
            db.execute("UPDATE concept_pitches SET data=? WHERE id=?",
                       (json.dumps(pitch), pitch["pitch_id"]))
        return pitch

    def pitches(self, project_id: str) -> list[dict]:
        rows = self.store._all("SELECT data FROM concept_pitches WHERE project_id=? ORDER BY"
                               " created_at, rowid", (project_id,))
        return [json.loads(r[0]) for r in rows]

    # ---- feedback rounds
    def add_feedback(self, project_id: str, sketch_id: str, data: dict) -> dict:
        with self.store._tx() as db:
            now = time.time()
            fb = {**data, "project_id": project_id, "sketch_id": sketch_id, "created_at": now}
            db.execute("INSERT INTO concept_feedback VALUES (?,?,?,?,?)",
                       (fb["feedback_id"], project_id, sketch_id, json.dumps(fb), now))
        return fb

    def feedback(self, project_id: str, sketch_id: str | None = None) -> list[dict]:
        if sketch_id is None:
            rows = self.store._all("SELECT data FROM concept_feedback WHERE project_id=? ORDER"
                                   " BY created_at, rowid", (project_id,))
        else:
            rows = self.store._all("SELECT data FROM concept_feedback WHERE project_id=? AND"
                                   " sketch_id=? ORDER BY created_at, rowid",
                                   (project_id, sketch_id))
        return [json.loads(r[0]) for r in rows]

    def delete_project(self, project_id: str) -> None:
        with self.store._tx() as db:
            for t in ("concepts", "concept_sketches", "concept_pitches", "concept_feedback"):
                db.execute(f"DELETE FROM {t} WHERE project_id=?", (project_id,))

    def view(self, project_id: str, design_class: dict | None = None) -> dict:
        """GET /projects/{id}/concept: the record, every sketch version (each with its feedback
        rounds), the pitches, and the build's design class."""
        fb: dict[str, list[dict]] = {}
        for f in self.feedback(project_id):
            fb.setdefault(f["sketch_id"], []).append(f)
        return {"concept": self.get(project_id),
                "design_class": design_class,
                "sketches": [{**s, "feedback": fb.get(s["sketch_id"], [])}
                             for s in self.sketches(project_id)],
                "pitches": self.pitches(project_id)}
