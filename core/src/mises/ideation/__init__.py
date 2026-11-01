"""Plan 11 F8b: divergent ideation for open-ended design challenges.

The user's three egg-drop builds all came back as a cage: each was classified `engineering`
(the concept stage was off), the lead built the first idea it had, and nothing asked it for
alternatives. This stage makes the lead think wide before it pitches:

- `challenge_signal(text)`: is the request an open-ended challenge (an egg drop, a
  competition, "make it interesting / creative / the best possible design")? Kept per build in
  meta `challenge:<build>`; a follow-up can raise it ("something really interesting") or turn it
  off ("just build it").
- `ideate_concepts` (ideation/tools.py): >= 8 concepts on >= 8 distinct physical principles
  (and >= 4 families), each scored on feasibility, novelty and rule compliance; the tool
  shortlists the 3 most different strong ones (one per family, never the conventional answer
  unless the user asked for it) for `pitch_options`.
- the build's tried list (`tried` on the concept record): concepts pitched or chosen in this
  build; a new ideation that repeats them gets them flagged and left off the shortlist.
- ideation/gate.py: in a challenge build, `pitch_options` and new parts are refused until an
  ideation exists, and new parts wait for the user's pick (or a reply) after the pitch.

Everything the agent wrote (names, mechanisms) is text the app shows as plain text.
"""
from __future__ import annotations

import json
import re
import time


from ..concept import new_id

CHALLENGE_META = "challenge:{}"
MIN_CONCEPTS = 8
MIN_PRINCIPLES = 8
MIN_FAMILIES = 4
SHORTLIST = 3
MAX_IDEATIONS = 30                     # per build

# physical-principle families: the shortlist takes one concept per family, so the three
# pitched directions really differ in how they work, not just in shape
FAMILIES = ("crush", "elastic_suspension", "aero_drag", "aero_rotation", "pneumatic",
            "fluid_granular", "geometric_structure", "rotational_rolling", "staged_sacrificial",
            "containment_shell", "other")
FAMILY_HELP = {
    "crush": "plastic deformation: crumple zones, foam, honeycomb, lattices",
    "elastic_suspension": "springs, bands, tensegrity, flexures holding the payload free",
    "aero_drag": "parachutes, drogues, streamers, drag plates",
    "aero_rotation": "autorotation: samara / maple seed, helicopter blades, spinning wings",
    "pneumatic": "airbags, inflatable or trapped-air bladders, bellows",
    "fluid_granular": "liquid or granular damping: sand, beads, water, viscous dampers",
    "geometric_structure": "origami, kirigami, bistable shells, buckling columns, linkages",
    "rotational_rolling": "rolling or spinning to shed energy sideways (balls, wheels, gyros)",
    "staged_sacrificial": "staged or sacrificial parts: legs that snap, layers that shed",
    "containment_shell": "a protective shell or cage around a padded payload",
    "other": "anything else (say which principle)",
}
# the obvious answer for a payload-protection challenge: allowed, never the default
CONVENTIONAL_RX = re.compile(r"\b(cage|box|crate|padded (shell|box|case)|bubble ?wrap)\b", re.I)

_EGG = re.compile(r"\begg[\s-]?drop\b|\bdrop(ping)?\b.{0,40}\beggs?\b|\beggs?\b.{0,40}\bdrop",
                  re.I)
_COMPETITION = re.compile(
    r"\b(competition|contest|olympiad|science fair|hackathon|tournament|regionals?|nationals|"
    r"rube goldberg|mousetrap car|bridge (building )?challenge|tower challenge|"
    r"rocketry challenge|robot(ics)? (challenge|competition)|team america rocketry|"
    r"(design|engineering|build) challenge|challenge)\b", re.I)
_CREATIVE = re.compile(
    r"\b(interesting|creative|unique|novel|original|wild|crazy|unconventional|inventive|"
    r"out of the box|outside the box|surprise me|something different|not boring|boring|"
    r"best possible|no limits|think (hard|big|outside))\b", re.I)
_MAKE = re.compile(r"\b(design|build|make|create|come up with|invent|house|protect|think of)\b",
                   re.I)
_OFF = re.compile(r"\b(just build( it)?|skip (the )?(ideas|ideation|pitch|brainstorm)|"
                  r"no (pitch|ideas|brainstorm)|keep it (simple|plain|conventional)|"
                  r"(the )?cage is fine)\b", re.I)


def challenge_signal(text: str) -> dict | None:
    """{kind, creative, reasons} when the text asks for an open-ended challenge design, else
    None. `kind`: egg_drop | competition | creative. "just build it" style text returns
    {"off": True}."""
    t = text or ""
    if _OFF.search(t):
        return {"off": True, "reasons": [f"the user said {_OFF.search(t).group(0)!r}"]}
    reasons, kind = [], None
    if _EGG.search(t):
        kind = "egg_drop"
        reasons.append("an egg-drop challenge")
    elif (m := _COMPETITION.search(t)) and _MAKE.search(t):
        kind = "competition"
        reasons.append(f"a challenge / competition ({m.group(0)!r})")
    creative = _CREATIVE.search(t)
    if creative:
        reasons.append(f"asks for something {creative.group(0)!r}")
        if kind is None and _MAKE.search(t):
            kind = "creative"
    if kind is None:
        return None
    return {"kind": kind, "creative": bool(creative), "reasons": reasons}


def build_challenge(store, project_id: str | None) -> dict | None:
    """The build's stored challenge flag (None: not a challenge build, or turned off)."""
    if not project_id:
        return None
    try:
        raw = store.meta_get(CHALLENGE_META.format(project_id))
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    return None if rec.get("off") else rec


def note_challenge(store, project_id: str | None, text: str) -> dict | None:
    """Raise (or turn off) the build's challenge flag from the user's own words; returns the
    flag now. A plain follow-up ("make the wall thicker") never clears it."""
    if not project_id:
        return None
    sig = challenge_signal(text)
    cur = build_challenge(store, project_id)
    if sig is None and cur is not None and not cur.get("creative") and _CREATIVE.search(text):
        sig = {"kind": cur["kind"], "creative": True,
               "reasons": [*cur.get("reasons", []), "asks for something more interesting"]}
    if sig is None:
        return cur
    if sig.get("off"):
        if cur is not None:
            store.meta_set(CHALLENGE_META.format(project_id),
                           json.dumps({**cur, "off": True, "off_reasons": sig["reasons"]}))
        return None
    if cur is not None and cur.get("creative") and not sig["creative"]:
        return cur
    rec = {**(cur or {}), **sig, "at": time.time()}
    if cur is not None and cur.get("kind") != "creative":
        rec["kind"] = cur["kind"]                # an egg drop stays an egg drop
    store.meta_set(CHALLENGE_META.format(project_id), json.dumps(rec))
    return rec


# ---- concepts: normalising, scoring, the shortlist

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"a", "an", "the", "of", "and", "or", "with", "for", "to", "by", "on", "in",
                   "via", "using", "based"})


def norm(text: str) -> str:
    """Lowercase words without stop words: "Crumple-zone foam" == "crumple zone foam"."""
    return " ".join(w for w in _WORD.findall((text or "").lower()) if w not in _STOP)


def score(c: dict) -> float:
    """Feasibility and novelty weigh equally; rule compliance is a smaller term (a concept
    under COMPLIANCE_MIN is never shortlisted at all)."""
    return round(0.4 * c["feasibility"] + 0.4 * c["novelty"] + 0.2 * c["rule_compliance"], 4)


COMPLIANCE_MIN = 0.5
FEASIBILITY_MIN = 0.3


def conventional(c: dict) -> bool:
    return bool(c.get("conventional")) or bool(
        CONVENTIONAL_RX.search(f"{c.get('name', '')} {c.get('principle', '')}"))


def shortlist(concepts: list[dict], *, include_conventional: bool = False,
              n: int = SHORTLIST) -> list[str]:
    """The n most different strong concepts: highest score first, at most one per family,
    skipping already-tried, non-compliant, infeasible and (unless asked) conventional ones.
    When fewer families qualify, the rest are filled by score."""
    ok = [c for c in concepts
          if not c.get("already_tried") and c["rule_compliance"] >= COMPLIANCE_MIN
          and c["feasibility"] >= FEASIBILITY_MIN
          and (include_conventional or not conventional(c))]
    ok.sort(key=lambda c: (-c["score"], c["name"]))
    picked, families = [], set()
    for c in ok:
        if c["family"] not in families or c["family"] == "other":
            picked.append(c)
            families.add(c["family"])
        if len(picked) == n:
            break
    for c in ok:
        if len(picked) == n:
            break
        if c not in picked:
            picked.append(c)
    return [c["name"] for c in picked]


def tried_keys(tried: list[dict]) -> set[str]:
    return {k for t in tried for k in (norm(t.get("name", "")), norm(t.get("principle", "")))
            if k}


# ---- the per-build store: ideations in their own table, the tried list on the concept record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS concept_ideations (
  id TEXT PRIMARY KEY, project_id TEXT, data TEXT, created_at REAL);
"""


class IdeationStore:
    def __init__(self, concepts):
        self.concepts = concepts               # the ConceptStore (its record holds `tried`)
        self.store = concepts.store
        with self.store._lock:
            self.store._db.executescript(_SCHEMA)

    def add(self, project_id: str, data: dict) -> dict:
        now = time.time()
        rec = {**data, "ideation_id": data.get("ideation_id") or new_id("cki"),
               "project_id": project_id, "created_at": now}
        with self.store._tx() as db:
            n = db.execute("SELECT COUNT(*) FROM concept_ideations WHERE project_id=?",
                           (project_id,)).fetchone()[0]
            if n >= MAX_IDEATIONS:
                from ..types import ErrorCode, MisesError
                raise MisesError(ErrorCode.INVALID_INPUT,
                                 f"this build already has {MAX_IDEATIONS} ideation rounds")
            db.execute("INSERT INTO concept_ideations VALUES (?,?,?,?)",
                       (rec["ideation_id"], project_id, json.dumps(rec), now))
        return rec

    def list(self, project_id: str) -> list[dict]:
        rows = self.store._all("SELECT data FROM concept_ideations WHERE project_id=? ORDER BY"
                               " created_at, rowid", (project_id,))
        return [json.loads(r[0]) for r in rows]

    def latest(self, project_id: str) -> dict | None:
        rows = self.list(project_id)
        return rows[-1] if rows else None

    def others(self, project_id: str, kind: str | None) -> list[dict]:
        """Concepts pitched or chosen in OTHER builds of the same challenge kind (the user's
        earlier egg drops): shown so a new build does not repeat them by accident."""
        if not kind:
            return []
        out = []
        for (pid, raw) in self.store._all("SELECT project_id, data FROM concepts"):
            if pid == project_id:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            for t in rec.get("tried") or []:
                if t.get("kind") == kind:
                    out.append({**t, "build_id": pid})
        return out[-20:]

    def tried(self, project_id: str) -> list[dict]:
        return list(self.concepts.get(project_id).get("tried") or [])

    def mark_tried(self, project_id: str, names: list[str], status: str) -> list[dict]:
        """Record concepts of the build's latest ideation (by name; an unknown name is kept
        as given) as pitched / chosen. A later status only upgrades (pitched -> chosen)."""
        latest = self.latest(project_id) or {"concepts": []}
        by = {norm(c["name"]): c for c in latest.get("concepts", [])}
        kind = latest.get("kind") or (build_challenge(self.store, project_id) or {}).get("kind")
        tried = self.tried(project_id)
        index = {norm(t["name"]): t for t in tried}
        for name in names:
            key = norm(name)
            if not key:
                continue
            c = by.get(key, {"name": name, "principle": "", "family": "other"})
            t = index.get(key)
            if t is None:
                t = {"name": c["name"], "principle": c.get("principle", ""),
                     "family": c.get("family", "other"), "kind": kind, "status": status,
                     "at": time.time()}
                tried.append(t)
                index[key] = t
            elif status == "chosen":
                t["status"] = "chosen"
        self.concepts.update(project_id, tried=tried)
        return tried


#: the lead's rules for open-ended challenges (orchestrator.lead.lead_definition appends it)
LEAD_IDEATION = """## Open-ended challenges: think wide before you pitch
An egg drop, a competition entry, or any request to make something interesting, creative or \
the best possible is an open-ended challenge. There the first idea is almost never the best \
one (a padded cage is the default everyone builds), so:
- Rules first. For a competition, fetch its current rules (target, limits, payload, \
materials) with the web tools before you design; they change every year. Otherwise take the \
limits from the user's message, and ask if a key one is missing.
- Diverge. Call `ideate_concepts` with at least 8 genuinely different concepts on distinct \
physical principles, e.g. for an egg drop: crumple / foam / printed-lattice crush, \
suspension or tensegrity, aerodynamic braking, autorotation (a samara / maple-seed wing), a \
parachute or drogue, an airbag or trapped-air bladder, origami / kirigami crush structures, \
liquid or granular damping, rolling or spinning to shed energy, sacrificial legs. Score each \
on feasibility, novelty and rule compliance honestly; mark the obvious answer `conventional`.
- Pitch the 3 the tool shortlists with `pitch_options` (one massing each, `concept` set) and \
wait for the user's pick. Never default to a cage unless the user chooses it. In this build, \
`pitch_options` and new parts wait for the ideation, and new parts wait for the user's answer.
- Remember. `concepts_tried` lists what this build (and the user's other builds of the same \
challenge) already tried: do not pitch those again.
- Verify. Once it is built, predict how it performs: for an egg payload `egg_drop_estimate` \
(impact speed, peak egg deceleration and force against the egg-break threshold, with its \
source), plus the drop study, dynamics or rocket tools where they apply. State the predicted \
peak egg acceleration, the threshold and its source, and the margin, as an estimate.
"""
