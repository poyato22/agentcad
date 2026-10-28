// Pure rules for the Print page: the printer's status light, when "Send to printer" is allowed,
// and how a finished print call reads. The core enforces the same rules again (state check before
// the card and right before upload); the UI only refuses earlier and says why.
import type { PrintCall, Printer, PrinterStatus, Slice } from "../../../api/types";
import { fmtElapsed } from "../../../lib/format";
import type { LedTone } from "../../common/Led";

/** States in which a printer needs the human (the core never sends then). */
const FAULT = new Set(["ERROR", "ATTENTION"]);
const HOST_RE = /^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$/;

/** Same rule as core profile.PrinterEntry.host: a hostname or IP, optionally :port; no scheme/path. */
export function validHost(h: string): boolean {
  const v = h.trim();
  if (!HOST_RE.test(v)) return false;
  return !v.includes(":") || Number(v.split(":").pop()) <= 65535;
}

export function printerLight(st: PrinterStatus | null): { tone: LedTone; text: string } {
  if (!st) return { tone: "faint", text: "checking…" };
  if (!st.reachable) return { tone: "red", text: `unreachable — ${st.error ?? "no answer"}` };
  const state = st.state ?? "UNKNOWN";
  const temps = [st.temp_nozzle !== null ? `nozzle ${Math.round(st.temp_nozzle)} °C` : null, st.temp_bed !== null ? `bed ${Math.round(st.temp_bed)} °C` : null]
    .filter(Boolean).join(" · ");
  if (st.sendable) return { tone: "green", text: [state.toLowerCase(), temps].filter(Boolean).join(" · ") };
  if (FAULT.has(state)) return { tone: "red", text: `${state.toLowerCase()} — clear it on the printer` };
  const job = st.job;
  if (state === "PRINTING" && job) {
    const bits = [typeof job.progress === "number" ? `printing ${Math.round(job.progress)}%` : "printing"];
    if (typeof job.time_remaining === "number") bits.push(`${fmtElapsed(job.time_remaining)} left`);
    return { tone: "amber", text: bits.join(" · ") };
  }
  return { tone: "amber", text: state.toLowerCase() };
}

export interface SendInput {
  slice: Slice | null; printer: Printer | null; status: PrinterStatus | null;
  partId: string | null; material: string; busy: boolean;
}

/** Why "Send to printer" is not available right now, or null when it is. */
export function sendBlock(i: SendInput): string | null {
  if (!i.printer) return "Add a printer first.";
  if (i.busy) return "A print request is waiting for your decision in the console.";
  if (!i.slice || i.slice.status !== "done") return "Slice first: the time, filament and cost must be shown before sending.";
  const s = i.slice;
  if (s.part_id !== i.partId || s.material !== i.material || s.printer_id !== i.printer.id) {
    return "The part, material or printer changed since this slice: slice again.";
  }
  if (s.model !== i.printer.model) return `This slice is for a ${s.model}; ${i.printer.name} is a ${i.printer.model}. Slice again.`;
  if (s.sent_to) return "This slice was already sent. Slice again to print another.";
  const st = i.status;
  if (!st) return "Still checking the printer…";
  if (!st.reachable) return `${i.printer.name} is not reachable. Check that it is on and on this network.`;
  if (!st.sendable) return `${i.printer.name} reports ${st.state ?? "an unknown state"}; nothing can be sent until it is idle.`;
  return null;
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

/** A finished print_send call as one line; null while it is still running. */
export function printOutcome(c: PrintCall): { tone: "green" | "red" | "dim"; text: string } | null {
  if (c.status === "running") return null;
  if (c.status === "cancelled") return { tone: "dim", text: "Cancelled; nothing was sent." };
  if (c.status === "done" && c.output?.ok !== false) return { tone: "green", text: text(c.output?.summary) ?? "Sent; the printer is starting the job." };
  const e = c.error ?? {};
  const msg = text(e.message) ?? text(c.output?.summary) ?? "the print request failed";
  const hint = text(e.hint);
  // a rejected card is the user's own choice, not a failure
  const tone = e.error === "denied" && /approval (rejected|expired)/.test(msg) ? "dim" : "red";
  return { tone, text: `Not sent: ${msg}${hint ? ` — ${hint}` : ""}` };
}
