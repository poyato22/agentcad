// The agent manager slide-over (every page). By default it is one view: every build's lead and
// sub-agents (R10). "Advanced" (remembered) adds two tabs: Runs (every run as the dense
// orchestrator → worker tree) and Definitions (custom agents). Opened by the activity pill, the
// Home toolbar and ⇧⌘A; Esc or the close button closes it and focus returns.
import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { AgentsPanel } from "./AgentsPanel";
import { AgentsTab } from "./AgentsTab";
import { agentManagerOpen, agentManagerTab, closeAgentManager, installManagerShortcut, managerAdvanced, setManagerAdvanced, type ManagerTab } from "./manager";
import { RunsTab } from "./RunsTab";
import "./agents.css";

const TABS: { id: ManagerTab; label: string }[] = [{ id: "agents", label: "Agents" }, { id: "runs", label: "All runs" }, { id: "definitions", label: "Definitions" }];
/** Tab stops the focus trap cycles through (tree rows hold a roving tabindex 0 / -1). */
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function AgentManager(): JSX.Element | null {
  useEffect(() => installManagerShortcut(), []);
  if (!agentManagerOpen.value) return null;
  return <Panel />;
}

function Panel(): JSX.Element {
  const panel = useRef<HTMLDivElement>(null);
  const advanced = managerAdvanced.value;
  const tab: ManagerTab = advanced ? agentManagerTab.value : "agents";

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
          {advanced && (
            <div class="am-tabs" role="tablist" aria-label="Agent manager sections" onKeyDown={onTabKey}>
              {TABS.map((t) => (
                <button key={t.id} type="button" role="tab" id={`am-tab-${t.id}`} data-tab={t.id} aria-selected={tab === t.id}
                  aria-controls={`am-pane-${t.id}`} tabIndex={tab === t.id ? 0 : -1} class={`am-tab${tab === t.id ? " on" : ""}`}
                  onClick={() => { agentManagerTab.value = t.id; }}>
                  {t.label}
                </button>
              ))}
            </div>
          )}
          <span class="am-spacer" />
          <button type="button" class="am-advanced" aria-pressed={advanced} data-advanced
            title={advanced ? "Back to the simple view" : "Every run as a tree, and your custom agent definitions"}
            onClick={() => setManagerAdvanced(!advanced)}>
            {advanced ? "Advanced ▾" : "Advanced ▸"}
          </button>
          <span class="faint am-kbd" aria-hidden="true">⇧⌘A</span>
          <button type="button" class="am-close" aria-label="Close the agent manager" onClick={closeAgentManager}>✕</button>
        </div>
        <div class="am-body" {...(advanced ? { role: "tabpanel", id: `am-pane-${tab}`, "aria-labelledby": `am-tab-${tab}` } : {})}>
          {tab === "agents" ? <AgentsPanel /> : tab === "runs" ? <RunsTab /> : <AgentsTab />}
        </div>
      </div>
    </div>
  );
}
