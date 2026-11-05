"""calibration_* tools: fit materials to the user's own tests (see calibration.py).

- calibration_list / calibration_get: the saved records (per material and printer).
- calibration_fit: fit tests WITHOUT saving (a preview of the calibrated material).
- calibration_save: fit and save (or extend, append=true) the record of a material + printer;
  studies of that material on that printer then use it ("calibrated (n tests)").
- calibration_delete: remove a record.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, ToolOutput
from . import calibration as cal
from .studies import _home
from .types import SimError


class ListArgs(BaseModel):
    material: str | None = Field(None, max_length=40, description="only this material")


class IdArgs(BaseModel):
    id: str = Field(pattern=cal.CAL_ID_RE.pattern)


class SaveArgs(cal.CalibrationInput):
    append: bool = Field(False, description="add these tests to the record's existing tests "
                         "(else they replace them)")


def _need_home(ctx: ToolContext) -> Path:
    home = _home(ctx)
    if home is None:
        raise MisesError(ErrorCode.INVALID_INPUT, "no AgentCAD home for calibrations")
    return home


def _check(ctx: ToolContext):
    def check(rec: dict) -> None:
        pol = getattr(ctx, "policy", None)
        if pol is None:
            return
        allowed = set(pol.edit_scope) | set(pol.read_scope)
        if "*" not in allowed and rec.get("part_id") not in allowed:
            raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{rec.get('sim_id')} belongs to a part "
                             "outside this run's scope", hint="ask the user to widen the scope")
    return check


def _fit(ctx: ToolContext, inp: cal.CalibrationInput):
    try:
        return cal.fit_input(_home(ctx), Path(ctx.workdir) / "parts", inp, _check(ctx))
    except SimError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint) from None


@tool("calibration_list", safety=SafetyClass.READ,
      description="List the user's material calibrations (per material and printer profile): "
                  "fitted E, strength, knock-down, number of tests, scatter and confidence. A "
                  "study of that material on that printer uses the calibration automatically "
                  "(labelled 'calibrated (n tests)'); others are 'datasheet / estimate'.")
async def calibration_list(ctx: ToolContext, args: ListArgs) -> ToolOutput:
    recs = [r for r in cal.load_all(_home(ctx))
            if args.material is None or r["material"].lower() == args.material.lower()]
    s = "; ".join(cal.summary(r) for r in recs) or "no calibrations"
    return ToolOutput(summary=s, data={"calibrations": recs})


@tool("calibration_get", safety=SafetyClass.READ,
      description="One calibration record by id: its tests (the source), the fit and the "
                  "calibrated material.")
async def calibration_get(ctx: ToolContext, args: IdArgs) -> ToolOutput:
    rec = cal.get(_home(ctx), args.id)
    if rec is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no calibration {args.id}")
    return ToolOutput(summary=cal.summary(rec), data=rec)


@tool("calibration_fit", safety=SafetyClass.READ,
      description="Fit a material to test results WITHOUT saving: tensile bars (break force, "
                  "cross-section, optional elongation; orientation xy or z), three-point bends "
                  "(span, width, thickness, load, mid-span deflection) and part load tests "
                  "(sim_id of a static study, point, load, measured deflection). Returns the "
                  "fitted E / strength / knock-down with n, scatter and confidence.")
async def calibration_fit(ctx: ToolContext, args: cal.CalibrationInput) -> ToolOutput:
    fitted, base, m = _fit(ctx, args)
    rec = {"material": args.material, "printer": args.printer,
           "basis": cal.fit_label(fitted), "fit": fitted}
    return ToolOutput(summary="preview (not saved): " + cal.summary(rec),
                      data={"fit": fitted, "material": m.to_dict(),
                            "datasheet": base.to_dict(), "status": fitted["status"],
                            "reasons": fitted["reasons"], "outliers": fitted["outliers"],
                            "applicable": fitted["status"] == "ok", "saved": False})


APPROVAL_REASON = ("a saved calibration changes the material every later study of it on "
                   "this printer uses, in every build")


def _agent_call(ctx: ToolContext, args) -> str | None:
    """Saving is applying (S4 C-cal-5): an agent's save or delete is the user's trusted click
    on a card. The app's own Studies page calls the core routes, with their own confirm."""
    from ..catalog.tools import ui_call_meta
    return None if ui_call_meta(ctx) is not None else APPROVAL_REASON


def _props(rec: dict | None) -> dict:
    """The applied values of a record (or the data sheet's when there is none)."""
    f = (rec or {}).get("fit") or {}
    e = f.get("e") or {}
    return {"e_mpa": None if str(e.get("method", "")).startswith("secant") else e.get("value_mpa"),
            "strength_mpa": (f.get("strength") or {}).get("value_mpa"),
            "z_strength_mpa": (f.get("z_strength") or {}).get("value_mpa"),
            "knockdown": f.get("knockdown"), "n_tests": f.get("n_tests"),
            "confidence": f.get("confidence"), "status": f.get("status")}


_LABELS = (("e_mpa", "E", " MPa"), ("strength_mpa", "strength", " MPa"),
           ("z_strength_mpa", "Z strength", " MPa"), ("knockdown", "knock-down", ""),
           ("n_tests", "tests", ""), ("confidence", "confidence", ""), ("status", "status", ""))


def _fmt(v, unit: str) -> str:
    if v is None:
        return "data sheet" if unit else "—"
    return f"{v:.4g}{unit}" if isinstance(v, float) else f"{v}{unit}"


def _changes(before: dict, after: dict) -> list[str]:
    """"E 2300 MPa → 3220 MPa" lines for the card (only what changes)."""
    out = []
    for k, label, unit in _LABELS:
        if before.get(k) != after.get(k):
            out.append(f"{label}: {_fmt(before.get(k), unit)} → {_fmt(after.get(k), unit)}")
    return out


async def _save_preview(ctx: ToolContext, args: SaveArgs) -> dict:
    home = _need_home(ctx)
    inp = cal.CalibrationInput(**args.model_dump(exclude={"append"}))
    old = cal.find(home, inp.material, inp.printer)
    if args.append:
        try:
            inp = cal.merged(old, inp)
        except SimError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint) from None
    fitted, base, _m = _fit(ctx, inp)
    before, after = _props(old), _props({"fit": fitted})
    if old is None:
        before = {**before, "e_mpa": base.e_mpa, "strength_mpa": base.yield_mpa,
                  "knockdown": base.knockdown}
    return {"material": inp.material, "printer": inp.printer, "before": before, "after": after,
            "changes": _changes(before, after),
            "note": "Approving saves this calibration: every later study of this material on "
                    "this printer uses it." + (" It is REJECTED, so studies will not use it."
                                               if fitted.get("status") == cal.REJECTED else "")}


async def _delete_preview(ctx: ToolContext, args: IdArgs) -> dict:
    rec = cal.get(_home(ctx), args.id)
    if rec is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no calibration {args.id}")
    before = _props(rec)
    after = {k: None for k in before}
    return {"material": rec["material"], "printer": rec["printer"], "before": before,
            "after": after, "changes": _changes(before, after),
            "note": "Approving deletes this calibration: studies go back to the data sheet."}


@tool("calibration_save", safety=SafetyClass.LOCAL_MUTATE,
      needs_approval=_agent_call, preview=_save_preview,
      description="Fit and SAVE the calibration of a material on a printer profile from the "
                  "user's test results (same inputs as calibration_fit; append=true adds to the "
                  "saved tests). Studies of that material and printer then use it. Only record "
                  "numbers the user measured; never invent test results.")
async def calibration_save(ctx: ToolContext, args: SaveArgs) -> ToolOutput:
    home = _need_home(ctx)
    inp = cal.CalibrationInput(**args.model_dump(exclude={"append"}))
    try:
        if args.append:
            inp = cal.merged(cal.find(home, inp.material, inp.printer), inp)
        fitted, _, m = _fit(ctx, inp)
        rec = cal.save(home, inp, fitted)
    except SimError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint) from None
    rejected = rec["status"] == cal.REJECTED
    return ToolOutput(ok=not rejected,
                      summary=("saved but REJECTED (studies will not use it): " if rejected
                               else "saved ") + cal.summary(rec),
                      data={**rec, "material_preview": m.to_dict()})


@tool("calibration_delete", safety=SafetyClass.LOCAL_MUTATE,
      needs_approval=_agent_call, preview=_delete_preview,
      description="Delete a material calibration by id (studies go back to the data sheet).")
async def calibration_delete(ctx: ToolContext, args: IdArgs) -> ToolOutput:
    if not cal.delete(_need_home(ctx), args.id):
        raise MisesError(ErrorCode.NOT_FOUND, f"no calibration {args.id}")
    return ToolOutput(summary=f"deleted calibration {args.id}", data={"deleted": args.id})


CALIBRATION_TOOLS = (calibration_list, calibration_get, calibration_fit, calibration_save,
                     calibration_delete)


def register_calibration_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in CALIBRATION_TOOLS:
        if not registry.list([t.name]):
            registry.register(t)
