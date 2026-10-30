import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { atHome, goHome } from "../../state/home";
import { runs } from "../../state/store";
import { Button } from "../common/Button";
import { ActivityPill } from "./ActivityPill";
import { Breadcrumb } from "./Breadcrumb";
import { BudgetMeter } from "./BudgetMeter";
import { ModeTabs } from "./ModeTabs";
import { ProfileButton } from "./ProfileButton";
import { StatusLights } from "./StatusLights";
import { installStopAllShortcut, toast, useStopAll } from "./stopAll";
import "./topbar.css";

export function TopBar(): JSX.Element {
  useEffect(() => installStopAllShortcut(), []);
  const { stopAll, busy } = useStopAll();
  let anyRunning = false;
  for (const r of runs.value.values()) if (r.status === "running") { anyRunning = true; break; }
  return (
    // Empty top-bar space is the macOS window drag region (overlay title bar).
    <header class="topbar" data-tauri-drag-region>
      <div class="topbar-left" data-tauri-drag-region>
        <button type="button" class={atHome.value ? "home-btn on" : "home-btn"} aria-pressed={atHome.value}
          title="Home — all builds (⌘0)" aria-label="Home" onClick={() => goHome()}>⌂</button>
        <Breadcrumb />
      </div>
      <ModeTabs />
      <div class="topbar-right">
        <ActivityPill />
        <div class="budget-slot" data-slot="budget"><BudgetMeter /></div>
        <StatusLights />
        <ProfileButton />
        <Button variant={anyRunning ? "danger" : "default"} class="stop-all" title="Stop all runs (⌘⇧.)" aria-busy={busy || undefined}
          onClick={() => { void stopAll(); }}>STOP ALL</Button>
      </div>
      <div class="toast" role="status" aria-live="polite">{toast.value}</div>
    </header>
  );
}
