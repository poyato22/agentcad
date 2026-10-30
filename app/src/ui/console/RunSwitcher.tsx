// Top of the conversation view: one dropdown for the run shown ("● designer · stiffen upright ▾",
// status dot), a blue "▲ N needs you" button whenever another run waits for you (one click
// shows it), Stop for a working run, "+ New" (inline composer) and the "Full log" toggle.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { debug } from "../../lib/log";
import { providerLabel, runLabel } from "../../lib/names";
import { effectiveRun, needsYou } from "../../state/selection";
import { actions, selectedRunId } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import { activityByRun } from "../../state/activity";
import { openAgentManager } from "../agents/manager";
import { buildRuns, effectiveRunId, labelOf, offListRuns, runLabels } from "./derived";
import { shownPending } from "./queued";
import { composerOpen, setConsoleView } from "./view";

const TONE: Record<AgentRun["status"], LedTone> = { queued: "faint", running: "blue", done: "green", failed: "red", cancelled: "faint" };

function tone(r: AgentRun, needs: ReadonlySet<string>): LedTone {
  return needs.has(r.id) ? "blue" : TONE[r.status];
}

function cancelRun(id: string): void {
  try {
    actions.client().cancel(id).catch((e: unknown) => debug("cancel failed", id, e));
  } catch (e) {
    debug("cancel failed", id, e);
  }
}

export function RunSwitcher(): JSX.Element {
  const list = buildRuns.value;   // the active build's runs (A3)
  const offList = offListRuns.value;
  const shownId = effectiveRunId.value;
  // the shown run may be off-list (the app's own "ui" run, whose print / slice cards sit here)
  const current = effectiveRun.value ?? shownPending.value ?? (shownId ? offList.get(shownId) ?? null : null);
  const { runIds: needs } = needsYou.value;
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  // the first fallback (newest running run) becomes an explicit choice once (as in the roster)
  useEffect(() => {
    if (shownId !== null && selectedRunId.peek() === null) actions.selectRun(shownId);
  }, [shownId]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  // the needs-you list: runs in the roster plus off-list runs (the ui run), minus the shown one
  const listIds = new Set(list.map((r) => r.id));
  const otherIds = [
    ...list.filter((r) => r.id !== shownId && needs.has(r.id)).map((r) => r.id),
    ...[...needs].filter((id) => id !== shownId && !listIds.has(id)),
  ];
  const label = current ? runLabels.value.get(current.id) ?? runLabel(current, [current])
    : shownId ? (offList.has(shownId) ? labelOf(shownId) : "starting…") : "no runs yet";
  const pick = (id: string) => { actions.selectRun(id); setOpen(false); };

  return (
    <div class="cv-top" ref={root} data-run-switcher>
      <div class="cv-top-row">
        <button type="button" class="cv-switch" aria-haspopup="listbox" aria-expanded={open} disabled={list.length === 0}
          aria-label={`Run: ${label}${current ? ` · ${current.status}` : ""}`} onClick={() => setOpen(!open)}
          onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
          {current
            ? <span class="cv-actdot" data-activity={activityByRun.value.get(current.id)?.state}><Led tone={tone(current, needs)} title={needs.has(current.id) ? "needs you" : current.status} /></span>
            : <Led tone="faint" />}
          <span class="cv-switch-l text" data-run-label>{label}</span>
          <span class="faint" aria-hidden="true">▾</span>
        </button>
        {otherIds.length > 0 && (
          <button type="button" class="cv-needs needs-you" aria-label={`${labelOf(otherIds[0])} needs you`} onClick={() => pick(otherIds[0])}>
            {`▲ ${otherIds.length} needs you`}
          </button>
        )}
        <span class="cv-spacer" />
        {current?.status === "running" && (
          <Button variant="ghost" aria-label={`Stop ${label}`} title="stop this run" onClick={() => cancelRun(current.id)}>Stop</Button>
        )}
        <Button variant="ghost" onClick={() => { composerOpen.value = true; }}>New</Button>
        <Button variant="ghost" title="Agent manager (⇧⌘A)" onClick={() => openAgentManager("runs")}>Manage</Button>
        <Button variant="ghost" aria-pressed="false" onClick={() => setConsoleView("details")}>Full log</Button>
      </div>
      {current && (
        <div class="cv-who faint text">{`${providerLabel(current.provider)} · ${current.model}`}</div>
      )}
      {open && (
        <div class="cv-menu" role="listbox" aria-label="Runs" onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); setOpen(false); } }}>
          {list.map((r) => {
            const sel = r.id === shownId;
            return (
              <div key={r.id} role="option" aria-selected={sel} tabIndex={0} class={`cv-opt${sel ? " sel" : ""}`} data-run={r.id} data-selected={sel ? "" : undefined}
                onClick={() => pick(r.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(r.id); } }}>
                <span class="cv-actdot" data-activity={activityByRun.value.get(r.id)?.state}><Led tone={tone(r, needs)} title={needs.has(r.id) ? "needs you" : r.status} /></span>
                <span class="cv-opt-l text" data-run-label>{labelOf(r.id)}</span>
                <span class={needs.has(r.id) ? "cv-opt-s needs-you" : "cv-opt-s faint"}>{needs.has(r.id) ? "needs you" : r.status}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Details view header: back to the conversation. */
export function DetailsBar(): JSX.Element {
  return (
    <div class="cv-detailsbar">
      <span class="label">Full log</span>
      <span class="cv-spacer" />
      <Button variant="ghost" onClick={() => setConsoleView("chat")}>Conversation</Button>
    </div>
  );
}
