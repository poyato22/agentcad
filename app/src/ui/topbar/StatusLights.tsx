import { invoke, isTauri } from "@tauri-apps/api/core";
import type { JSX } from "preact";
import type { ShellCoreStatus } from "../../api/connection";
import type { StreamState } from "../../api/events";
import type { CoreStatusInfo } from "../../api/types";
import { needsYou } from "../../state/selection";
import { conn, coreStatus, runs, shellStatus } from "../../state/store";
import { Led, type LedTone } from "../common/Led";

export function coreTone(shell: ShellCoreStatus, stream: StreamState): LedTone {
  if (shell.state === "crashed" || shell.state === "stopped" || stream === "unauthorized") return "red";
  if (stream === "open") return "green";
  return "amber";
}

function coreTitle(shell: ShellCoreStatus, stream: StreamState, tone: LedTone): string {
  if (shell.state === "crashed") return `core crashed (exit ${shell.code ?? "?"}, ${shell.restarts} restarts)\n${shell.stderrTail}\nclick to restart`;
  if (shell.state === "stopped") return "core stopped · click to restart";
  if (stream === "unauthorized") return "core rejected the token · click to restart";
  return tone === "green" ? "core connected" : `core ${stream}`;
}

export function onshapeTone(s: CoreStatusInfo | null): { tone: LedTone; text: string } {
  if (!s || s.onshape !== "connected") return { tone: "faint", text: "not set up" };
  const n = s.onshape_calls_year;
  const cap = s.onshape_cap_year;
  const tone: LedTone = cap > 0 && n >= cap ? "red" : cap > 0 && n >= cap * 0.8 ? "amber" : "green";
  return { tone, text: `connected · ${n.toLocaleString("en-US")}/${cap.toLocaleString("en-US")}` };
}

function restartCore(): void {
  if (isTauri()) void invoke("core_restart").catch(() => {});
}

export function StatusLights(): JSX.Element {
  const shell = shellStatus.value;
  const stream = conn.value;
  const tone = coreTone(shell, stream);
  const restartable = tone === "red";
  const os = onshapeTone(coreStatus.value);
  let running = 0;
  for (const r of runs.value.values()) if (r.status === "running") running++;
  const needs = needsYou.value.count;

  return (
    <div class="lights">
      <span class={restartable ? "light light-click" : "light"} data-light="core" title={coreTitle(shell, stream, tone)}
        role={restartable ? "button" : undefined} tabIndex={restartable ? 0 : undefined}
        onClick={restartable ? restartCore : undefined}
        onKeyDown={restartable ? (e) => { if (e.key === "Enter" || e.key === " ") restartCore(); } : undefined}>
        <Led tone={tone} /> CORE
      </span>
      <span class="light" data-light="onshape"><Led tone={os.tone} /> ONSHAPE <span class="light-val">{os.text}</span></span>
      <span class="light" data-light="agents">
        <Led tone={running > 0 ? "blue" : "faint"} /> AGENTS{" "}
        <span class="light-val">{running} {running === 1 ? "agent" : "agents"}</span>
        {needs > 0 && <span class="light-need"> · {needs} needs you</span>}
      </span>
      <span class="light" data-light="printer"><Led tone="faint" /> PRINTER <span class="light-val">no printer</span></span>
    </div>
  );
}
