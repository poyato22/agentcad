// The agent manager slide-over (every page): a Runs tab (the run tree, delete and clean up) and an
// Agents tab (custom agent definitions). Opened by the activity pill, the run switcher's
// "Manage" and ⇧⌘A; Esc or the close button closes it and focus returns to where it was.
import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { AgentsPanel } from "./AgentsPanel";
import { AgentsTab } from "./AgentsTab";
import { agentManagerOpen, agentManagerTab, closeAgentManager, installManagerShortcut, type ManagerTab } from "./manager";
import { RunsTab } from "./RunsTab";
import "./agents.css";

// R10: "Agents" is the simple lead/sub view of every build (the default); "Runs" is the dense run
// tree with the power controls; "Definitions" edits custom agents.
const TABS: { id: ManagerTab; label: string }[] = [{ id: "runs", label: "Agents" }, { id: "tree", label: "Runs" }, { id: "agents", label: "Definitions" }];
/** Tab stops the focus trap cycles through (tree rows hold a roving tabindex 0 / -1). */
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function AgentManager(): JSX.Element | null {
  useEffect(() => installManagerShortcut(), []);
  if (!agentManagerOpen.value) return null;
  return <Panel />;
}

function Panel(): JSX.Element {
  const panel = useRef<HTMLDivElement>(null);
  const tab = agentManagerTab.value;

  useEffect(() => {
    const back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      closeAgentManager();
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); back?.focus?.(); };
  }, []);

  /** Focus trap: Tab and Shift+Tab cycle inside the slide-over (it is aria-modal). */
  const onPanelKey = (e: KeyboardEvent) => {
    if (e.key !== "Tab" || !panel.current) return;
    // closed sections aren't rendered at all, so every match is visible; skip disabled controls
    // and the in-row controls taken out of the tab order (tabindex -1)
    const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
      .filter((el) => !el.hasAttribute("disabled") && el.getAttribute("tabindex") !== "-1");
    if (items.length === 0) { e.preventDefault(); panel.current.focus(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === panel.current || !panel.current.contains(active))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (active === last || !panel.current.contains(active))) { e.preventDefault(); first.focus(); }
  };

  const onTabKey = (e: KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const i = TABS.findIndex((t) => t.id === tab);
    const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    agentManagerTab.value = next.id;
    panel.current?.querySelector<HTMLElement>(`[data-tab="${next.id}"]`)?.focus();
  };

  return (
    <div class="am-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) closeAgentManager(); }}>
      <div ref={panel} class="am-panel" role="dialog" aria-modal="true" aria-label="Agent manager" tabIndex={-1} data-agent-manager
        onKeyDown={onPanelKey}>
        <div class="am-head">
          <h2 class="am-h">Agents</h2>
          <div class="am-tabs" role="tablist" aria-label="Agent manager sections" onKeyDown={onTabKey}>
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" id={`am-tab-${t.id}`} data-tab={t.id} aria-selected={tab === t.id}
                aria-controls={`am-pane-${t.id}`} tabIndex={tab === t.id ? 0 : -1} class={`am-tab${tab === t.id ? " on" : ""}`}
                onClick={() => { agentManagerTab.value = t.id; }}>
                {t.label}
              </button>
            ))}
          </div>
          <span class="am-spacer" />
          <span class="faint am-kbd" aria-hidden="true">⇧⌘A</span>
          <button type="button" class="am-close" aria-label="Close the agent manager" onClick={closeAgentManager}>✕</button>
        </div>
        <div class="am-body" role="tabpanel" id={`am-pane-${tab}`} aria-labelledby={`am-tab-${tab}`}>
          {tab === "runs" ? <AgentsPanel /> : tab === "tree" ? <RunsTab /> : <AgentsTab />}
        </div>
      </div>
    </div>
  );
}
