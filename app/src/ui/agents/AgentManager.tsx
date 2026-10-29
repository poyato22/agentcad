// The agent manager slide-over (every page): a Runs tab (the run tree, delete and clean up) and an
// Agents tab (custom agent definitions). Opened by the activity pill, the run switcher's
// "Manage" and ⌘6; Esc or the close button closes it and focus returns to where it was.
import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { AgentsTab } from "./AgentsTab";
import { agentManagerOpen, agentManagerTab, closeAgentManager, installManagerShortcut, type ManagerTab } from "./manager";
import { RunsTab } from "./RunsTab";
import "./agents.css";

const TABS: { id: ManagerTab; label: string }[] = [{ id: "runs", label: "Runs" }, { id: "agents", label: "Agents" }];

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
      <div ref={panel} class="am-panel" role="dialog" aria-modal="true" aria-label="Agent manager" tabIndex={-1} data-agent-manager>
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
          <span class="faint am-kbd" aria-hidden="true">⌘6</span>
          <button type="button" class="am-close" aria-label="Close the agent manager" onClick={closeAgentManager}>✕</button>
        </div>
        <div class="am-body" role="tabpanel" id={`am-pane-${tab}`} aria-labelledby={`am-tab-${tab}`}>
          {tab === "runs" ? <RunsTab /> : <AgentsTab />}
        </div>
      </div>
    </div>
  );
}
