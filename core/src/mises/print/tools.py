"""print_* tools: list printers, slice a local part, send a slice to a printer.

`print_send` is irreversible: it goes through the approval card, and it is refused (before the
card is shown, and again right before the upload) unless the printer reports an idle state.
Printer API keys live only in the Keychain (service `mises`, account `printer:<id>`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
import weakref
from pathlib import Path

from pydantic import BaseModel, Field

from ..cad.tools import _check_part_id
from ..platform import keychain_get
from ..profile import PrinterEntry, load_profile
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, ToolOutput
from . import slicer
from .prusalink import PrinterStatus, PrusaLinkClient

KEYCHAIN_SERVICE = "mises"
SLICE_ID_RE = re.compile(r"^slice_[0-9a-f]{10}$")
# print_send's upload timeout; a `sending_at` claim older than this is an unknown outcome
SEND_TIMEOUT_S = 300
# per-event-loop, per-slice send locks (weak on the loop so test loops don't accumulate)
_SEND_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]]" = \
    weakref.WeakKeyDictionary()


def keychain_account(printer_id: str) -> str:
    return f"printer:{printer_id}"


def new_slice_id() -> str:
    return "slice_" + secrets.token_hex(5)


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def home_of(ctx) -> Path:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    if rt is None:
        raise MisesError(ErrorCode.DENIED, "no AgentCAD app is attached to this run")
    return Path(rt.home)


def slice_dir(home: Path, slice_id: str) -> Path:
    if not SLICE_ID_RE.match(slice_id):
        raise _bad(f"bad slice id {slice_id!r}", f"slice ids match {SLICE_ID_RE.pattern}")
    return Path(home) / "slices" / slice_id


def read_slice(home: Path, slice_id: str) -> dict:
    try:
        return json.loads((slice_dir(home, slice_id) / "slice.json").read_text())
    except (OSError, ValueError):
        raise MisesError(ErrorCode.NOT_FOUND, f"no slice {slice_id!r}",
                         hint="create one with print_slice") from None


def write_slice(home: Path, slice_id: str, rec: dict) -> None:
    d = slice_dir(home, slice_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "slice.json.tmp"
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, d / "slice.json")


def find_printer(home: Path, printer_id: str) -> PrinterEntry:
    for p in load_profile(home).printers:
        if p.id == printer_id:
            return p
    raise MisesError(ErrorCode.NOT_FOUND, f"no printer {printer_id!r}",
                     hint="add one on the Print page, or use print_printers_list")


async def client_for(printer: PrinterEntry) -> PrusaLinkClient:
    key = await asyncio.to_thread(keychain_get, KEYCHAIN_SERVICE, keychain_account(printer.id))
    if not key:
        raise MisesError(ErrorCode.DENIED, f"no API key stored for printer {printer.name!r}",
                         hint="re-add the printer with its PrusaLink API key")
    return PrusaLinkClient(printer.host, key)


def refuse_unless_sendable(printer: PrinterEntry, st: PrinterStatus) -> None:
    if not st.sendable:
        raise MisesError(
            ErrorCode.DENIED,
            f"printer {printer.name!r} reports {st.state}; not sending a job",
            hint="clear the error or wait until the printer is idle, then try again")


async def check_sendable(printer: PrinterEntry) -> PrinterStatus:
    st = await (await client_for(printer)).status()
    refuse_unless_sendable(printer, st)
    return st


class NoArgs(BaseModel):
    pass


class SliceArgs(BaseModel):
    part_id: str
    printer_id: str
    material: str | None = Field(default=None, description="PLA, PETG, ABS, ASA or TPU; "
                                 "the profile's default material when omitted")


class SendArgs(BaseModel):
    slice_id: str = Field(pattern=SLICE_ID_RE.pattern)
    printer_id: str
    gcode_sha256: str = Field(pattern=r"^[0-9a-f]{64}$",
                              description="the slice's gcode_sha256 from print_slice; the approval "
                                          "is bound to exactly these bytes")
    file_name: str = Field(min_length=1, max_length=80,
                           description="the file name that will be uploaded (from print_slice)")
    host: str = Field(min_length=1, max_length=255,
                      description="the printer's host as the core filled it from the profile at "
                                  "card time; the send is refused if the profile's host differs")


def upload_name(part_id: str) -> str:
    return f"mises_{part_id}.gcode"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@tool("print_printers_list", safety=SafetyClass.READ,
      description="List the printers the user added (id, name, host, model) and whether "
                  "PrusaSlicer is installed. API keys are never shown.")
async def print_printers_list(ctx: ToolContext, args: NoArgs) -> ToolOutput:
    home = home_of(ctx)
    printers = [p.model_dump(mode="json") for p in load_profile(home).printers]
    return ToolOutput(summary=f"{len(printers)} printer(s)",
                      data={"printers": printers, "slicer": slicer.slicer_status()})


@tool("print_slice", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id", timeout_s=600,
      description="Slice a local part's STL with PrusaSlicer for a printer and material. "
                  "Returns a slice handle with time, filament grams and cost estimates.")
async def print_slice(ctx: ToolContext, args: SliceArgs) -> ToolOutput:
    home = home_of(ctx)
    _check_part_id(args.part_id)
    stl = Path(ctx.workdir) / "parts" / args.part_id / "model.stl"
    if not stl.is_file():
        raise MisesError(ErrorCode.NOT_FOUND, f"no built part {args.part_id!r}",
                         hint="create it with mirror_part_create")
    from ..catalog.geometry import read_meta
    if (read_meta(Path(ctx.workdir) / "parts" / args.part_id) or {}).get("kind") == "reference":
        raise MisesError(ErrorCode.INVALID_INPUT, f"{args.part_id!r} is a reference object",
                         hint="reference parts are the real thing the build fits; print only"
                              " the parts you design")
    printer = find_printer(home, args.printer_id)
    from .. import mass as _mass       # Plan 9 B2: the part's own material, else the default
    material = (args.material or _mass.part_material(getattr(ctx, "store", None), home,
                                                     args.part_id)).upper()
    mat = slicer.material_of(material)
    slicer.require_slicer()
    # a fresh id every time; only the app's own calls (trusted meta) may name it, so an agent
    # can never overwrite a slice record that an approval card already refers to
    sid = new_slice_id()
    rt = ctx.services["runtime"]
    call = getattr(rt, "ui_calls", {}).get(ctx.call_id)
    if call is not None and call.run_id == ctx.run_id and call.meta.get("slice_id"):
        sid = call.meta["slice_id"]
        if (slice_dir(home, sid) / "model.gcode").exists():
            raise _bad(f"slice {sid} already exists")
    rec = {"slice_id": sid, "status": "running", "part_id": args.part_id,
           "printer_id": printer.id, "material": material, "model": printer.model,
           "created_at": time.time(), "estimate": None, "preview": False, "error": None,
           "hint": None}
    write_slice(home, sid, rec)
    try:
        res = await slicer.run_slice(stl, slice_dir(home, sid), model=printer.model,
                                     material=material)
    except asyncio.CancelledError:
        write_slice(home, sid, {**rec, "status": "cancelled", "error": "cancelled"})
        raise
    except MisesError as e:
        write_slice(home, sid, {**rec, "status": "failed", "error": e.message, "hint": e.hint})
        raise
    except Exception as e:  # noqa: BLE001
        write_slice(home, sid, {**rec, "status": "failed", "error": f"{type(e).__name__}: {e}"})
        raise
    est = res.estimate.to_dict()
    digest = _sha256(res.gcode_path.read_bytes())
    write_slice(home, sid, {**rec, "status": "done", "estimate": est, "gcode_sha256": digest,
                            "file_name": upload_name(args.part_id),
                            "preview": res.preview_png is not None})
    return ToolOutput(
        summary=(f"{args.part_id} on {printer.name} ({material}): {est['time_text']}, "
                 f"{est['filament_g']:.1f} g, ${est['cost_usd']:.2f}"),
        data={"slice_id": sid, "part_id": args.part_id, "printer_id": printer.id,
              "material": material, "usd_per_kg": mat.usd_per_kg, "estimate": est,
              "preview": res.preview_png is not None, "gcode_sha256": digest,
              "file_name": upload_name(args.part_id)},
        handles=[str(res.gcode_path)])


def _send_target(home: Path, args: SendArgs) -> tuple[dict, PrinterEntry]:
    rec = read_slice(home, args.slice_id)
    if rec.get("status") != "done":
        raise MisesError(ErrorCode.INVALID_INPUT, f"slice {args.slice_id} is {rec.get('status')}",
                         hint="wait for the slice to finish or slice again")
    if rec.get("sent_at"):
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"slice {args.slice_id} was already sent to a printer",
                         hint="slice again to reprint")
    claimed = rec.get("sending_at")
    if claimed:
        if time.time() - float(claimed) > SEND_TIMEOUT_S:
            # a send started and never finished (crash mid-upload): it may or may not have printed
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"an earlier send of slice {args.slice_id} did not finish; it may "
                             "or may not have reached the printer",
                             hint="check the printer, then slice again to reprint")
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"slice {args.slice_id} is being sent to a printer",
                         hint="wait for that send to finish; slice again to reprint")
    printer = find_printer(home, args.printer_id)
    if args.host != printer.host:
        raise MisesError(ErrorCode.NEEDS_APPROVAL,
                         "the printer's host changed since this request was made; the approval "
                         "was bound to a different host", hint="review the printer and ask again")
    if rec.get("model") != printer.model:
        raise MisesError(ErrorCode.DENIED,
                         f"this slice is for a {rec.get('model')}, but {printer.name!r} is a "
                         f"{printer.model}", hint="slice again for this printer")
    if args.gcode_sha256 != rec.get("gcode_sha256") or args.file_name != rec.get("file_name"):
        raise MisesError(ErrorCode.NEEDS_APPROVAL,
                         "the slice changed since this request was made; the approval does not "
                         "cover the current G-code", hint="review the new slice and ask again")
    return rec, printer


async def _send_preview(ctx: ToolContext, args: SendArgs) -> dict:
    home = home_of(ctx)
    rec, printer = _send_target(home, args)
    gcode = slice_dir(home, args.slice_id) / "model.gcode"
    if not gcode.is_file() or _sha256(gcode.read_bytes()) != args.gcode_sha256:
        raise MisesError(ErrorCode.NEEDS_APPROVAL, "the slice's G-code file changed on disk",
                         hint="slice again")
    st = await check_sendable(printer)  # an errored or busy printer never gets an approval card
    est = rec["estimate"]
    return {"part_id": rec["part_id"], "material": rec["material"],
            "time": est["time_text"], "time_s": est["time_s"], "filament_g": est["filament_g"],
            "cost_usd": est["cost_usd"], "preview_available": bool(rec.get("preview")),
            "slice_model": rec["model"], "gcode_sha256_short": args.gcode_sha256[:12],
            "file_name": args.file_name,
            "printer": {"id": printer.id, "name": printer.name, "host": printer.host,
                        "model": printer.model, "state": st.state}}


def _send_lock(home: Path, slice_id: str) -> asyncio.Lock:
    """One lock per slice (per event loop): the sent/sending check and the upload never
    interleave for two approved sends of the same slice."""
    loop = asyncio.get_running_loop()
    locks = _SEND_LOCKS.setdefault(loop, {})
    return locks.setdefault(str(slice_dir(home, slice_id)), asyncio.Lock())


@tool("print_send", safety=SafetyClass.IRREVERSIBLE, preview=_send_preview, timeout_s=SEND_TIMEOUT_S,
      description="Send a finished slice to a printer and START PRINTING it. Needs the user's "
                  "approval, which is bound to the slice's gcode_sha256 and file_name (both "
                  "returned by print_slice). Refused while the printer reports an error, is "
                  "busy or is printing, or when the slice is for another printer model.")
async def print_send(ctx: ToolContext, args: SendArgs) -> ToolOutput:
    home = home_of(ctx)
    # held from the sent/sending check through the upload: a second approved send of the same
    # slice waits, then sees sent_at (or a live sending_at claim) and is refused
    async with _send_lock(home, args.slice_id):
        rec, printer = _send_target(home, args)
        gcode = slice_dir(home, args.slice_id) / "model.gcode"
        if not gcode.is_file():
            raise MisesError(ErrorCode.NOT_FOUND, "the slice's G-code is missing", hint="slice again")
        data = gcode.read_bytes()  # the bytes that get verified are the bytes that get uploaded
        if _sha256(data) != args.gcode_sha256:
            raise MisesError(ErrorCode.NEEDS_APPROVAL,
                             "the G-code changed after the approval card was shown; nothing was sent",
                             hint="review the new slice and ask again")
        client = await client_for(printer)
        refuse_unless_sendable(printer, await client.status())  # state may have changed since the card
        # claim the slice on disk (atomic replace) before any byte goes out; a crash mid-upload
        # leaves the claim, which turns into "check the printer" once older than SEND_TIMEOUT_S
        write_slice(home, args.slice_id, {**rec, "sending_at": time.time()})
        try:
            await client.upload_bytes(data, args.file_name, print_after=True)
        except Exception:
            write_slice(home, args.slice_id, rec)       # the upload failed: release the claim
            raise
        write_slice(home, args.slice_id, {**rec, "sent_to": printer.id, "sent_at": time.time()})
    return ToolOutput(summary=f"sent {args.file_name} to {printer.name}; the printer is starting the job",
                      data={"slice_id": args.slice_id, "printer_id": printer.id,
                            "file": args.file_name})


class CheckArgs(BaseModel):
    part_id: str | None = Field(default=None, description="one built part; omit to check every "
                                "printable part of the build (reference parts are skipped)")
    material: str | None = Field(default=None, description="PLA, PETG, ABS, ASA or TPU; the "
                                 "part's own material when omitted, else PLA")
    printer_model: str = Field(default="MK4", description="MK4 (default) or MINI; 0.4 mm nozzle, "
                               "0.2 mm layers")
    min_clearance_mm: float = Field(default=0.3, gt=0, le=5, description="the smallest gap "
                                    "allowed between separate bodies (print-in-place)")
    max_print_time_h: float | None = Field(default=None, gt=0, description="the user's print-time "
                                           "limit per part; the check fails above it")
    slice: bool = Field(default=True, description="false: mesh check only (fast, no times)")
    slice_candidates: int = Field(default=3, ge=1, le=6, description="how many of the best "
                                  "orientations (by mesh overhang) to slice, besides as-built")


MAX_CHECK_PARTS = 16


def print_study_type() -> dict:
    """The print simulation as a sim_types_list entry (it runs through print_check, not
    sim_run: it slices an STL, it does not mesh for FEA)."""
    st = slicer.slicer_status()
    return {"id": "print", "name": "Print simulation (slicer estimate)",
            "solver": "PrusaSlicer CLI (sandboxed) + direct mesh check",
            "fidelity_class": "slicer_estimate", "tool": "print_check",
            "doc": "FDM printability: print time, filament, supports, overhangs beyond 45 deg, "
                   "bed contact, recommended orientation, print-in-place clearance. Run it with "
                   "print_check (not sim_run).",
            "channels": [], "available": True,
            "detail": None if st["installed"] else
            f"PrusaSlicer missing ({slicer.INSTALL_COMMAND}): mesh check only",
            "schema": CheckArgs.model_json_schema()}


def _printable_parts(workdir: Path) -> list[str]:
    from ..catalog.geometry import read_meta
    out = []
    for d in sorted((workdir / "parts").glob("*")):
        if (d / "model.stl").is_file() and (read_meta(d) or {}).get("kind") != "reference":
            out.append(d.name)
    return out


def _part_summary(pid: str, r: dict) -> str:
    rec, s = r["recommended"], r["recommended"].get("slicer") or {}
    bits = [f"{pid}: {'printable' if r['printable'] else 'NOT printable'}"]
    if s:
        bits.append(f"{s['time_text']}, {s['filament_g']:.1f} g"
                    + (f", supports {s['support_g']:g} g" if s["needs_supports"] else ", no supports"))
    else:
        bits.append(f"overhang {rec['overhang_area_mm2']:.0f} mm2 (mesh)")
    if rec["orientation"] != "as_built":
        bits.append(f"print {rec['doc']}")
    c = r["clearance"]
    if c["bodies"] > 1:
        bits.append(f"min gap {c['min_gap_mm']} mm" if c["min_gap_mm"] is not None
                    else f"{c['bodies']} bodies, far apart")
    return " | ".join(bits)


@tool("print_check", safety=SafetyClass.LOCAL_MUTATE, timeout_s=900,
      description="Print simulation (slicer estimate) of a built part, or of every printable part "
                  "when part_id is omitted. Slices headless with PrusaSlicer in a sandbox and "
                  "checks the mesh: print time, filament mm/g, whether supports are needed and "
                  "how many grams, overhang area beyond 45 degrees, first-layer bed contact, a "
                  "recommended orientation among six axis-aligned poses (least support, then "
                  "most bed contact) with the trade-off, and a print-in-place clearance check "
                  "between separate bodies (min_clearance_mm, default 0.3). Default printer "
                  "Prusa MK4, 0.4 mm nozzle, 0.2 mm layers; material sets PLA/PETG/ASA/TPU "
                  "settings. Pass max_print_time_h when the user gave a print-time limit.")
async def print_check(ctx: ToolContext, args: CheckArgs) -> ToolOutput:
    from . import printability as pa
    workdir = Path(ctx.workdir)
    if args.part_id is not None:
        _check_part_id(args.part_id)
        if not (workdir / "parts" / args.part_id / "model.stl").is_file():
            raise MisesError(ErrorCode.NOT_FOUND, f"no built part {args.part_id!r}",
                             hint="build it first; print_check reads its model.stl")
        pids = [args.part_id]
    else:
        pids = _printable_parts(workdir)
        if not pids:
            raise MisesError(ErrorCode.NOT_FOUND, "no built parts to check",
                             hint="build a part first")
    skipped = pids[MAX_CHECK_PARTS:]
    pids = pids[:MAX_CHECK_PARTS]
    slicer.model_of(args.printer_model)
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    home = Path(rt.home) if rt is not None else None
    results: dict[str, dict] = {}
    for pid in pids:
        material = args.material
        if material is None:
            try:
                from .. import mass as _mass
                material = _mass.part_material(getattr(ctx, "store", None), home, pid)
            except Exception:  # noqa: BLE001 - no material known: the PLA default
                material = None
        material = str(material or "PLA").upper()
        if material not in slicer.MATERIALS:
            material = "PLA"
        opts = pa.CheckOptions(model=args.printer_model.upper(), material=material,
                               min_clearance_mm=args.min_clearance_mm,
                               max_print_time_h=args.max_print_time_h, slice=args.slice,
                               slice_candidates=args.slice_candidates)
        out_dir = workdir / "print_checks" / pid
        results[pid] = await pa.check_part(workdir / "parts" / pid / "model.stl", out_dir, opts,
                                           home=home)
    bad = [p for p, r in results.items() if not r["printable"]]
    summary = (f"print check ({pa.FIDELITY}): " + "; ".join(_part_summary(p, r)
                                                            for p, r in results.items()))
    if skipped:
        summary += f"; {len(skipped)} more part(s) not checked (limit {MAX_CHECK_PARTS})"
    return ToolOutput(summary=summary,
                      data={"fidelity": pa.FIDELITY, "parts": results, "not_printable": bad,
                            "skipped": skipped,
                            "total_time_s": sum(((r["recommended"].get("slicer") or {})
                                                 .get("time_s") or 0) for r in results.values()),
                            "total_filament_g": round(sum(((r["recommended"].get("slicer") or {})
                                                           .get("filament_g") or 0)
                                                          for r in results.values()), 2)})


def register_print_tools(registry: ToolRegistry) -> None:
    from ..drawing import register_drawing_tools
    for t in (print_printers_list, print_slice, print_send, print_check):
        registry.register(t)
    register_drawing_tools(registry)
