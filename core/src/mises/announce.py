"""Announcements (Plan 7 R16): the core tells the user when an agent creates a part, spawns an
agent, adds to the assembly or the BOM, shows a part or imports a drawing.

They are made from tool RESULTS (the Gate calls `on_tool_result` after every successful call,
and agent_spawn calls `announce` when a child starts), so they never depend on what the model
says. Each one is an `agent_announce` event

    {run_id, kind, title, detail, ref}

published on the acting run, plus a copy on its lead (the root of its run tree) so the lead's
thread shows what its sub-agents did; `run_id` in the data is always the acting run. Each is
also stored as a DECISION step ({"agent_announce": {...}}) so a reloaded thread can rebuild the
cards. Titles carry core-validated ids (part ids, instance ids), except agent_spawned, whose
title includes the model's task title (UI-escaped like all card text); never page text.
The signature of `announce(events, store, run_id, kind, title, detail, ref)` is a shared
contract (Plan 8 G1 reuses it): keep it stable.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .events import Event
from .types import StepKind, ToolOutput

log = logging.getLogger(__name__)

KINDS = ("part_created", "agent_spawned", "assembly_added", "bom_added", "part_shown",
         "drawing_imported",
         # Plan 8 G1 (geometry quality): its view and critique cards go through announce() too,
         # their fields in `ref`, so they are stored as steps the same way
         "view_presented", "critique_round",
         # Plan 9 G-A: the concept stage's sketch and pitch cards (View stream)
         "concept_sketch", "concept_pitch")
TITLE_CHARS = 120
DETAIL_CHARS = 300

Announcement = dict[str, Any]
Mapper = Callable[[dict, dict], "Announcement | None"]


def _clip(s: Any, n: int) -> str:
    s = "" if s is None else " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def make(run_id: str, kind: str, title: str, detail: str = "", ref: dict | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown announcement kind {kind!r}")
    return {"run_id": run_id, "kind": kind, "title": _clip(title, TITLE_CHARS),
            "detail": _clip(detail, DETAIL_CHARS), "ref": dict(ref or {})}


def _root(store, run_id: str) -> str:
    """The top of the run's tree (its lead, for a sub-agent); the run itself if it has none."""
    seen, rid = {run_id}, run_id
    while True:
        try:
            parent = store.get_run(rid).parent_id
        except KeyError:
            return rid
        if parent is None or parent in seen:
            return rid
        seen.add(parent)
        rid = parent


def announce(events, store, run_id: str, kind: str, title: str, detail: str = "",
             ref: dict | None = None) -> dict:
    """Publish (and store) one announcement on the acting run and on its lead."""
    data = make(run_id, kind, title, detail, ref)
    targets = [run_id]
    if store is not None:
        root = _root(store, run_id)
        if root != run_id:
            targets.append(root)
    for target in targets:
        if store is not None:
            try:
                store.append_step(target, StepKind.DECISION, {"agent_announce": data})
            except Exception:  # noqa: BLE001 - bookkeeping never breaks the tool call
                log.debug("could not store the announcement on %s", target, exc_info=True)
        if events is not None:
            events.publish(Event(type="agent_announce", run_id=target, data=dict(data)))
    return data


# ---- tool result -> announcement
def _part_created(args: dict, data: dict) -> Announcement | None:
    pid = data.get("part_id") or args.get("part_id")
    if not pid or data.get("existing") or (data.get("version") or 1) > 1:
        return None                      # a rebuild or a re-import is not a new part
    # Plan 9 D10: the card shows the part's render (GET /parts/{id}/thumb.png, a core route)
    return {"kind": "part_created", "title": f"Created part {pid}",
            "ref": {"part_id": pid, "thumb_url": thumb_url(pid)}}


def thumb_url(part_id: str) -> str:
    return f"/parts/{part_id}/thumb.png"


def _auto_placed(args: dict, data: dict) -> Announcement | None:
    """Plan 9 D10: mirror_part_create placed the new part into the assembly itself."""
    placed = data.get("placed") or {}
    inst = placed.get("instance") or {}
    if not inst.get("id"):
        return None
    pid = inst.get("part_id") or data.get("part_id")
    return {"kind": "assembly_added", "title": f"Added {pid} to the assembly",
            "detail": "placed by itself" + (", grounded" if inst.get("fixed") else ""),
            "ref": {"project_id": placed.get("project_id"), "instance_id": inst["id"],
                    "part_id": pid}}


def _catalog_part(args: dict, data: dict) -> Announcement | None:
    pid = data.get("part_id")
    if not pid or data.get("existing"):
        return None
    return {"kind": "part_created", "title": f"Added bought part {pid}",
            "detail": "from the catalog", "ref": {"part_id": pid,
                                                  "catalog_id": args.get("item_id")}}


def _onshape_part(args: dict, data: dict) -> Announcement | None:
    pid = data.get("part_id")
    if not pid or data.get("existing"):
        return None
    return {"kind": "part_created", "title": f"Imported part {pid}", "detail": "from Onshape",
            "ref": {"part_id": pid}}


def _assembly_added(args: dict, data: dict) -> Announcement | None:
    inst = data.get("instance") or {}
    pid = inst.get("part_id") or args.get("part_id")
    if not pid:
        return None
    joint = data.get("joint") or {}
    detail = (f"{joint.get('kind')} joint to {joint.get('target')}" if joint.get("target")
              else "grounded" if inst.get("fixed") else "")
    return {"kind": "assembly_added", "title": f"Added {pid} to the assembly", "detail": detail,
            "ref": {"project_id": data.get("project_id") or args.get("project_id"),
                    "instance_id": inst.get("id"), "part_id": pid,
                    **({"joint_id": joint["id"]} if joint.get("id") else {})}}


def _bom_added(args: dict, data: dict) -> Announcement | None:
    # U4c's bom_add: the row it wrote is data.item (or data.row); names are untrusted page text,
    # so the title carries the quantity and the kind only, the name goes in detail (UI-escaped)
    row = data.get("item") or data.get("row") or {}
    qty = row.get("qty") or args.get("qty") or 1
    kind = row.get("kind") or args.get("kind") or "bought"
    return {"kind": "bom_added", "title": f"Added {qty} × {kind} part to the parts list",
            "detail": _clip(row.get("name") or args.get("name"), 80),
            "ref": {"bom_id": row.get("id"), "project_id": row.get("project_id")
                    or args.get("project_id")}}


def _part_shown(args: dict, data: dict) -> Announcement | None:
    # part_show's data.card carries ids only: a page-derived name is untrusted page text
    # (it lives in untrusted_page_data, and the part card shows it from the card store), so
    # the detail is only the name the agent passed itself, if any
    card = data.get("card") or data
    return {"kind": "part_shown", "title": "Showing a part for you to look at",
            "detail": _clip(args.get("name"), 80),
            "ref": {k: card.get(k) or args.get(k) for k in ("snapshot_id", "catalog_id", "card_id")
                    if card.get(k) or args.get(k)}}


#: tool name -> how its successful result becomes an announcement (None: nothing to say).
#: drawing_imported has no mapper: imports/drawing_import.py announces it itself, once, when
#: the import finishes (no tool result marks that moment).
MAPPERS: dict[str, Mapper] = {
    "mirror_part_create": _part_created,
    # Plan 9 D10 (FW-core I-5): accepting a generative-design variant as a NEW part delivers a
    # part, so it gets a part_created card (mode=replace bumps the version, so _part_created
    # returns None there) and, like mirror_part_create, an auto-placed follow-up.
    "topopt_accept": _part_created,
    "catalog_insert": _catalog_part,
    "onshape_import_element": _onshape_part,
    "assembly_add_instance": _assembly_added,
    "assembly_add_part": _assembly_added,
    "bom_add": _bom_added,
    "part_show": _part_shown,
}


#: tool name -> further announcements one result can imply (after MAPPERS' one)
FOLLOW_UPS: dict[str, tuple[Mapper, ...]] = {
    "mirror_part_create": (_auto_placed,),
    "topopt_accept": (_auto_placed,),
}


def from_tool_result(name: str, args: dict, out: ToolOutput) -> Announcement | None:
    """The announcement a successful tool result implies, or None."""
    if not out.ok:
        return None
    mapper = MAPPERS.get(name.split("mcp__mises__", 1)[-1])
    if mapper is None:
        return None
    return mapper(args or {}, out.data or {})


def all_from_tool_result(name: str, args: dict, out: ToolOutput) -> list[Announcement]:
    """Every announcement a successful result implies, in order (from_tool_result's first)."""
    first = from_tool_result(name, args, out)
    if not out.ok:
        return []
    more = [m(args or {}, out.data or {})
            for m in FOLLOW_UPS.get(name.split("mcp__mises__", 1)[-1], ())]
    return [a for a in (first, *more) if a is not None]


def on_tool_result(gate, run_id: str, name: str, args: dict, out: ToolOutput) -> None:
    """The Gate's hook. Calls the app's own UI started (its "ui" runs) are the user's doing,
    not an agent's: they are not announced."""
    if run_id in (gate.services.get("ui_run_ids") or ()):
        return
    try:
        for a in all_from_tool_result(name, args, out):
            announce(gate.events, gate.store, run_id, a["kind"], a["title"],
                     a.get("detail", ""), a.get("ref"))
    except Exception:  # noqa: BLE001 - an announcement never fails the call it describes
        log.warning("announcement for %s failed", name, exc_info=True)
