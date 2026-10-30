// Top-bar status (Plan 7 U2b, R13). One "Status" button replaces the row of CORE / ONSHAPE /
// AGENTS / PRINTER lights and the separate Onshape budget meter, which said the same things twice
// (the ONSHAPE light and the meter; the AGENTS light and the activity pill). The button shows three
// dots (core, Onshape, printer) and one headline — the thing that matters now: "Core down",
// "Connecting", "N needs you", else "Ready". Its card holds each light in full, the core restart,
// and the Onshape budget.
import { invoke, isTauri } from "@tauri-apps/api/core";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ShellCoreStatus } from "../../api/connection";
import type { StreamState } from "../../api/events";
import type { CoreStatusInfo, Printer, PrinterStatus } from "../../api/types";
import type { OnshapeBudget } from "../../api/directory";
import { budgetLevel, onshapeBudget } from "../../state/onshapeBudget";
import { printersRev } from "../../state/printers";
import { needsYou } from "../../state/selection";
import { actions, conn, coreStatus, runs, shellStatus } from "../../state/store";
import { useDismiss } from "../common/dismiss";
import { Led, type LedTone } from "../common/Led";
import { printerLight } from "../pages/print/printLogic";
import { usePoll } from "../pages/print/usePrint";
import { BudgetMeter } from "./BudgetMeter";

/** The top bar checks the first printer this often (the Print page polls faster while shown). */
export const PRINTER_LIGHT_MS = 15_000;

/** How long the stream may be down while the shell says ready before the core counts as lost. */
export const CORE_LOST_MS = 5000;

/** The shell says ready but the event stream can't reach the core (e.g. an attached core died). */
function streamDown(shell: ShellCoreStatus, stream: StreamState): boolean {
  return shell.state === "ready" && (stream === "reconnecting" || stream === "closed");
}

/** True once streamDown() has held for CORE_LOST_MS without a break. */
function useCoreLost(shell: ShellCoreStatus, stream: StreamState): boolean {
  const down = streamDown(shell, stream);
  const [lost, setLost] = useState(false);
  useEffect(() => {
    setLost(false);
    if (!down) return;
    const t = setTimeout(() => setLost(true), CORE_LOST_MS);
    return () => clearTimeout(t);
  }, [down]);
  return down && lost;
}

export function coreTone(shell: ShellCoreStatus, stream: StreamState, lost = false): LedTone {
  if (shell.state === "crashed" || shell.state === "stopped" || stream === "unauthorized" || lost) return "red";
  if (stream === "open") return "green";
  return "amber";
}

function coreTitle(shell: ShellCoreStatus, stream: StreamState, tone: LedTone, lost: boolean): string {
  if (shell.state === "crashed") return `core crashed (exit ${shell.code ?? "?"}, ${shell.restarts} restarts)\n${shell.stderrTail}\nclick to restart`;
  if (shell.state === "stopped") return "core stopped · click to restart";
  if (stream === "unauthorized") return "core rejected the token · click to restart";
  if (lost) return `core unreachable for ${CORE_LOST_MS / 1000}s (${stream}) · click to restart`;
  return tone === "green" ? "core connected" : `core ${stream}`;
}

/** `live`: the budget meter's figures (onshape_budget events). When the meter shows them, the light keeps
 *  only its tone and "connected" (the count is in the meter and in the light's title), to save top-bar room. */
export function onshapeTone(s: CoreStatusInfo | null, live: OnshapeBudget | null = null): { tone: LedTone; text: string; title?: string } {
  if (!s || s.onshape !== "connected") return { tone: "faint", text: "not set up", ...(s ? { title: "Onshape not set up: add your Onshape keys to connect it." } : {}) };
  if (live) {
    const level = budgetLevel(live);
    return { tone: level === "limit" ? "red" : level === "warn" ? "amber" : "green",
      text: "connected", title: `Onshape connected: ${live.used_year.toLocaleString("en-US")} of ${live.limit_year.toLocaleString("en-US")} calls used this year` };
  }
  const n = s.onshape_calls_year;
  const cap = s.onshape_cap_year;
  const tone: LedTone = cap > 0 && n >= cap ? "red" : cap > 0 && n >= cap * 0.8 ? "amber" : "green";
  return { tone, text: `connected · ${n.toLocaleString("en-US")}/${cap.toLocaleString("en-US")}` };
}

function restartCore(): void {
  if (isTauri()) void invoke("core_restart").catch(() => {});
}

/** The Status button's headline: the one thing that matters now. */
export function statusHeadline(core: LedTone, stream: StreamState, needs: number): { text: string; tone: LedTone } {
  if (core === "red") return { text: "Core down", tone: "red" };
  if (core === "amber") return { text: stream === "connecting" ? "Starting" : "Connecting", tone: "amber" };
  if (needs > 0) return { text: `${needs} needs you`, tone: "blue" };
  return { text: "Ready", tone: "faint" };
}

export function StatusLights(): JSX.Element {
  const shell = shellStatus.value;
  const stream = conn.value;
  const lost = useCoreLost(shell, stream);
  const tone = coreTone(shell, stream, lost);
  const restartable = tone === "red";
  const os = onshapeTone(coreStatus.value, onshapeBudget.value);
  let running = 0;
  for (const r of runs.value.values()) if (r.status === "running") running++;
  const needs = needsYou.value.count;
  const printer = usePrinterLight();
  const head = statusHeadline(tone, stream, needs);

  const [at, setAt] = useState<{ right: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const close = (refocus: boolean) => { setAt(null); if (refocus) btn.current?.focus(); };
  const toggle = () => {
    if (at) { close(false); return; }
    // the card is position: fixed (the top bar's right side clips overflow), right-aligned under the button
    const r = btn.current?.getBoundingClientRect();
    setAt({ right: r && r.width > 0 ? Math.max(8, window.innerWidth - r.right) : 8 });
  };
  useDismiss(cardRef, () => { if (at) close(false); }, [btn]);
  const label = `Status: ${head.text}. Core ${tone === "green" ? "connected" : tone === "red" ? "down" : "connecting"}, `
    + `Onshape ${os.text}, ${running} ${running === 1 ? "agent" : "agents"} running, printer ${printer.text}.`;

  return (
    // Escape closes the card from the button or from inside it, and focus returns to the button
    <div class="status" onKeyDown={(e) => { if (e.key === "Escape" && at) { e.preventDefault(); e.stopPropagation(); close(true); } }}>
      <button ref={btn} type="button" class="status-btn" data-status-tone={head.tone} aria-label={label} title="Status"
        aria-haspopup="dialog" aria-expanded={at !== null} aria-controls="status-card" onClick={toggle}>
        <span class="status-dots" aria-hidden="true">
          <span data-dot="core"><Led tone={tone} /></span>
          <span data-dot="onshape"><Led tone={os.tone} /></span>
          <span data-dot="printer"><Led tone={printer.tone} /></span>
        </span>
        <span class={head.tone === "blue" ? "status-head needs-you" : "status-head"} data-headline>{head.text}</span>
      </button>
      {/* always in the DOM (hidden when closed) so the lights keep their state and titles */}
      <div id="status-card" ref={cardRef} class="status-card" role="dialog" aria-label="Status" hidden={at === null}
        style={at ? { right: `${at.right}px` } : undefined}>
        <div class="lights">
          <span class={restartable ? "light light-click" : "light"} data-light="core" title={coreTitle(shell, stream, tone, lost)}
            role={restartable ? "button" : undefined} tabIndex={restartable ? 0 : undefined}
            onClick={restartable ? restartCore : undefined}
            onKeyDown={restartable ? (e) => { if (e.key === "Enter" || e.key === " ") restartCore(); } : undefined}>
            <Led tone={tone} /> Core <span class="light-val">{restartable ? "down · click to restart" : tone === "green" ? "connected" : stream}</span>
          </span>
          <span class="light" data-light="onshape" title={os.title}><Led tone={os.tone} /> Onshape <span class="light-val">{os.text}</span></span>
          <span class="light" data-light="agents" title={`${running} ${running === 1 ? "agent" : "agents"} running`}>
            <Led tone={running > 0 ? "blue" : "faint"} /> Agents{" "}
            <span class="light-val">{running} {running === 1 ? "agent" : "agents"}</span>
            {needs > 0 && <span class="light-need needs-you"> · {needs} needs you</span>}
          </span>
          <span class="light" data-light="printer" title={printer.title}><Led tone={printer.tone} /> Printer <span class="light-val">{printer.text}</span></span>
        </div>
        <BudgetMeter onOpen={() => close(false)} />
      </div>
    </div>
  );
}

/** PRINTER: GET /printers (re-read when the event stream (re)opens or the list changes), then the
 *  first printer's status. No printers (or no core): faint "no printer". */
function usePrinterLight(): { tone: LedTone; text: string; title: string } {
  const stream = conn.value;
  const rev = printersRev.value;
  const [list, setList] = useState<Printer[] | null>(null);
  useEffect(() => {
    if (stream !== "open") return;
    let live = true;
    try {
      actions.client().printers().then((p) => { if (live) setList(p); }, () => { if (live) setList(null); });
    } catch { /* no core bound yet */ }
    return () => { live = false; };
  }, [stream, rev]);
  const first = list?.[0] ?? null;
  const [st, setSt] = useState<{ id: string; status: PrinterStatus | null; stale: boolean } | null>(null);
  usePoll(first?.id ?? null, first !== null, () => PRINTER_LIGHT_MS, async () => {
    if (!first) return false;
    try {
      setSt({ id: first.id, status: await actions.client().printerStatus(first.id), stale: false });
    } catch {
      setSt({ id: first.id, status: null, stale: true });
    }
  });
  if (!first) return { tone: "faint", text: "no printer", title: "No printer" };
  const cur = st && st.id === first.id ? st : null;
  const l = printerLight(cur?.status ?? null, cur?.stale ?? false);
  const more = list && list.length > 1 ? ` +${list.length - 1}` : "";
  const short = cur?.status ? (cur.status.reachable ? (cur.status.state ?? "unknown").toLowerCase() : "unreachable") : "…";
  return { tone: l.tone, text: `${first.name}${more} · ${short}`, title: `${first.name}: ${l.text}` };
}
