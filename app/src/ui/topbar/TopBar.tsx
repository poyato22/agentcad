import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { runs } from "../../state/store";
import { Button } from "../common/Button";
import { Breadcrumb } from "./Breadcrumb";
import { ModeTabs } from "./ModeTabs";
import { StatusLights } from "./StatusLights";
import { installStopAllShortcut, toast, useStopAll } from "./stopAll";
import "./topbar.css";

export function TopBar(): JSX.Element {
  useEffect(() => installStopAllShortcut(), []);
  const { stopAll, busy } = useStopAll();
  let anyRunning = false;
  for (const r of runs.value.values()) if (r.status === "running") { anyRunning = true; break; }
  return (
    <header class="topbar">
      <div class="topbar-left"><Breadcrumb /></div>
      <ModeTabs />
      <div class="topbar-right">
        <StatusLights />
        <Button variant={anyRunning ? "danger" : "default"} class="stop-all" title="Stop all runs (⌘⇧.)" aria-busy={busy || undefined}
          onClick={() => { void stopAll(); }}>STOP ALL</Button>
      </div>
      <div class="toast" role="status" aria-live="polite">{toast.value}</div>
    </header>
  );
}
