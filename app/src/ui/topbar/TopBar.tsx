// The build's top bar (only inside a build; Home has none — D23):
//   ⌂ Home · the build's name (click to rename it in place) | Design · Studies · Print · BOM |
//   working agents · Onshape count · Status · Profile · Stop all (only while something runs).
// There are no build tabs (D1): switching builds goes through Home (⌂ or ⌘0).
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import { activeBuild, buildActions } from "../../state/builds";
import { goHome } from "../../state/home";
import { runs } from "../../state/store";
import { Button } from "../common/Button";
import { ActivityPill } from "./ActivityPill";
import { BudgetChip } from "./BudgetMeter";
import { ModeTabs } from "./ModeTabs";
import { ProfileButton } from "./ProfileButton";
import { StatusLights } from "./StatusLights";
import { toast, useStopAll } from "./stopAll";
import "./topbar.css";

export function TopBar(): JSX.Element {
  const { stopAll, busy } = useStopAll();
  let anyRunning = false;
  for (const r of runs.value.values()) if (r.status === "running") { anyRunning = true; break; }
  return (
    // Empty top-bar space is the macOS window drag region (overlay title bar).
    <header class="topbar" data-tauri-drag-region>
      <div class="topbar-left" data-tauri-drag-region>
        <button type="button" class="home-btn" title="All builds (⌘0)" aria-label="Home" onClick={() => goHome()}>
          <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path d="M2.5 7.2 8 2.6l5.5 4.6V13a.6.6 0 0 1-.6.6H9.6V9.8H6.4v3.8H3.1a.6.6 0 0 1-.6-.6Z"
              fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
          </svg>
        </button>
        <BuildName />
      </div>
      <ModeTabs />
      <div class="topbar-right">
        <ActivityPill />
        {/* U2b: the live Onshape count (only while connected), then one Status button whose card
            holds the lights and the budget details */}
        <BudgetChip />
        <StatusLights />
        <ProfileButton />
        {/* ⇧⌘. stops everything from anywhere (App); the button shows only while it has work */}
        {anyRunning && (
          <Button variant="danger" class="stop-all" title="Stop all runs (⇧⌘.)" aria-busy={busy || undefined}
            onClick={() => { void stopAll(); }}>Stop all</Button>
        )}
      </div>
      <div class="toast" role="status" aria-live="polite">{toast.value}</div>
    </header>
  );
}

/** The open build's name. A click (or Enter / F2) turns it into a field: Enter or leaving it
 *  saves, Esc keeps the old name. Build names are user text, rendered as text. */
export function BuildName(): JSX.Element | null {
  const b = activeBuild.value;
  const [editing, setEditing] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  if (!b) return null;
  if (editing) {
    return (
      <NameField initial={b.name} label={`Rename ${b.name}`} onDone={(v) => {
        setEditing(false);
        if (v && v !== b.name) void buildActions.rename(b.id, v);
        requestAnimationFrame(() => btn.current?.focus());
      }} />
    );
  }
  return (
    <button type="button" ref={btn} class="build-name" data-build={b.id} title="Rename this build"
      aria-label={`Build: ${b.name}. Rename`}
      onClick={() => setEditing(true)}
      onKeyDown={(e) => { if (e.key === "F2") { e.preventDefault(); setEditing(true); } }}>
      {b.name}
    </button>
  );
}

function NameField({ initial, label, onDone }: { initial: string; label: string; onDone(v: string | null): void }): JSX.Element {
  const [v, setV] = useState(initial);
  const done = useRef(false);
  const finish = (x: string | null) => { if (done.current) return; done.current = true; onDone(x); };
  return (
    <input class="build-name-input" aria-label={label} value={v} maxLength={80} placeholder="Build name"
      ref={(el) => { if (el && !done.current && document.activeElement !== el) { el.focus(); el.select(); } }}
      onInput={(e) => setV((e.target as HTMLInputElement).value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); finish(v.trim() ? v.trim() : null); }
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
      }}
      onBlur={() => finish(v.trim() && v.trim() !== initial ? v.trim() : null)} />
  );
}
