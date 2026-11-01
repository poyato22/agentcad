"""The engineering pipeline's record and its "done" gate (Plan 10 C3, E29/E30).

Every check tool (actuator_size, electronics_estimate, power_budget, dyn_torque,
dyn_stability, dyn_rom_sweep, dyn_to_fem, C1's assembly_health) writes its latest verdict here,
per build: <home>/engineering_checks/<project_id>.json = {check: {status, summary, margins,
result_id, signature, at}}. `signature` fingerprints the assembly (instances, poses, joints,
part geometry) the check saw, so a later change makes it stale.

- Skeleton stage (before skinning): actuator_size, electronics_estimate, power_budget.
- Before done, for a build with moving joints: dyn_torque (a representative motion),
  dyn_stability, dyn_rom_sweep, dyn_to_fem (the most loaded links) and assembly_health.

`report()` is what spec_get, engineering_checks and the lead's finish check read. The finish
check (`finish_warning`) appends a warning to a lead's turn that ends with checks missing,
failing or stale; it never blocks the turn.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

CHECKS_DIR = "engineering_checks"
SKELETON = ("actuator_size", "electronics_estimate", "power_budget")
DONE = ("dyn_torque", "dyn_stability", "dyn_rom_sweep", "dyn_to_fem", "assembly_health")
ALL = SKELETON + DONE
ORDER = {"fail": 4, "missing": 3, "stale": 2, "warn": 2, "pass": 1, "skipped": 0,
         "not_checked": 0}


def _path(home: Path, project_id: str) -> Path:
    safe = "".join(c for c in project_id if c.isalnum() or c in "_-")[:64] or "default"
    return Path(home) / CHECKS_DIR / f"{safe}.json"


def signature(asm, home: Path | None = None) -> str:
    """A fingerprint of what the checks measured: instances (part, pose), joints (kind,
    value, limits) and each part's built geometry (its metrics volume and bbox)."""
    h = hashlib.sha1()
    parts = set()
    for i in sorted(asm.instances, key=lambda i: i.id):
        h.update(json.dumps([i.id, i.part_id, [[round(x, 3) for x in r] for r in i.transform],
                             i.fixed]).encode())
        parts.add(i.part_id)
    for j in sorted(asm.joints, key=lambda j: j.id):
        h.update(json.dumps([j.id, j.kind, j.a.instance_id, j.b.instance_id, j.value,
                             j.limits], default=str).encode())
    if home is not None:
        for pid in sorted(parts):
            try:
                m = json.loads((Path(home) / "parts" / pid / "metrics.json").read_text())
                h.update(json.dumps([pid, round(float(m.get("volume_mm3") or 0), 2),
                                     (m.get("bbox") or {}).get("size")]).encode())
            except (OSError, ValueError, TypeError):
                h.update(pid.encode())
    return h.hexdigest()[:16]


def load(home: Path, project_id: str) -> dict:
    try:
        d = json.loads(_path(home, project_id).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record(home: Path, project_id: str, check: str, *, status: str, summary: str,
           margins=None, result_id: str | None = None, sig: str | None = None,
           extra: dict | None = None) -> dict:
    p = _path(home, project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = load(home, project_id)
    row = {"status": status, "summary": summary[:600], "margins": margins,
           "result_id": result_id, "signature": sig, "at": time.time(), **(extra or {})}
    d[check] = row
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, default=float))
    tmp.replace(p)
    return row


def moving(asm) -> bool:
    return any(j.kind != "fixed" for j in asm.joints)


def report(home: Path, project_id: str, asm, *, health_available: bool | None = None) -> dict:
    """Every pipeline check's latest state for the build: pass / warn / fail / missing / stale
    / skipped. Required: the skeleton checks for a build with joints or actuators, and the
    done checks for one with moving joints."""
    d = load(home, project_id)
    sig = signature(asm, home)
    need_done = moving(asm)
    rows = []
    for c in ALL:
        r = d.get(c)
        required = need_done or c in SKELETON and bool(asm.joints)
        if c == "assembly_health" and r is None and health_available is False:
            rows.append({"check": c, "status": "skipped", "required": False,
                         "summary": "assembly_health is not available in this Mises; run "
                                    "assembly_check (with sweep) instead"})
            continue
        if r is None:
            rows.append({"check": c, "status": "missing" if required else "not_checked",
                         "required": required, "summary": "not run yet"})
            continue
        st = r["status"]
        stale = bool(r.get("signature")) and r["signature"] != sig
        rows.append({"check": c, "status": "stale" if stale and st != "fail" else st,
                     "required": required, "summary": r.get("summary", ""),
                     "margins": r.get("margins"), "result_id": r.get("result_id"),
                     "at": r.get("at"), "stale": stale})
    req = [r for r in rows if r["required"]]
    missing = [r["check"] for r in req if r["status"] == "missing"]
    failing = [r["check"] for r in req if r["status"] == "fail"]
    stale = [r["check"] for r in req if r["status"] == "stale"]
    warn = [r["check"] for r in req if r["status"] == "warn"]
    worst = max((r["status"] for r in req), key=lambda s: ORDER.get(s, 0), default="pass")
    return {"rows": rows, "missing": missing, "failing": failing, "stale": stale,
            "warn": warn, "overall": worst if req else "not_checked",
            "moving_joints": need_done, "signature": sig}


def done_text(rep: dict) -> str | None:
    """The finish check's line, or None when the build is clear (or has nothing to check)."""
    bits = []
    if rep["failing"]:
        bits.append("failing: " + ", ".join(rep["failing"]))
    if rep["missing"]:
        bits.append("not run: " + ", ".join(rep["missing"]))
    if rep["stale"]:
        bits.append("stale (the assembly changed since): " + ", ".join(rep["stale"]))
    if not bits:
        return None
    return ("Engineering checks are not clear for this build — " + "; ".join(bits) +
            ". Run them (engineering_checks lists what is needed) and state the margins "
            "before reporting done.")


def finish_warning(runtime, run_id: str, final_text: str | None = None) -> str | None:
    """A lead's turn ended (end_turn): when its build has moving joints and the pipeline is
    not clear, append a VERIFY step with the warning (shown with the agent's final message).
    Returns the warning text, else None. Never raises."""
    try:
        from ..orchestrator.lead import LEAD
        from ..types import StepKind
        run = runtime.store.get_run(run_id)
        pid = getattr(run, "project_id", None)
        if getattr(run, "role", None) != LEAD or not pid:
            return None
        asm = runtime.assembly_store.get(pid)
        if not moving(asm):
            return None
        rep = report(runtime.home, pid, asm,
                     health_available="assembly_health" in _tool_names(runtime))
        text = done_text(rep)
        if text is None:
            return None
        runtime.store.append_step(run_id, StepKind.VERIFY, {
            "kind": "engineering_checks", "warning": text, "missing": rep["missing"],
            "failing": rep["failing"], "stale": rep["stale"], "project_id": pid})
        return text
    except Exception:  # noqa: BLE001 - a check never breaks a turn
        return None


def _tool_names(runtime) -> set[str]:
    try:
        return {t.name for t in runtime.registry.list()}
    except Exception:  # noqa: BLE001
        return set()
