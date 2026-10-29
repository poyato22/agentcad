// Shown while the core starts, over a themed backdrop: the grey cube mark and a single status line
// that advances through the real boot phases — no spinner filler. If the core crashes or stays
// unreachable past the timeout, it becomes a clear error with Retry and an expandable log.
import { invoke, isTauri } from "@tauri-apps/api/core";
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { ShellCoreStatus } from "../../api/connection";
import type { StreamState } from "../../api/events";
import { conn, shellStatus } from "../../state/store";
import { CubeMark } from "./CubeMark";
import "./boot.css";

/** How long the core may take to connect before Loading shows the timeout error. */
export const LOADING_TIMEOUT_MS = 20_000;

const PHASES = ["Starting core", "Loading models", "Checking tools"] as const;

export function statusLine(shell: ShellCoreStatus, stream: StreamState, elapsedMs: number): string {
  if (stream === "open") return "Ready";
  if (shell.state === "ready") return "Connecting";
  // Before the stream opens, advance through the phases so there is a sense of progress.
  const i = Math.min(PHASES.length - 1, Math.floor(elapsedMs / 1200));
  return PHASES[i];
}

export function isFatal(shell: ShellCoreStatus): boolean {
  return shell.state === "crashed" || shell.state === "stopped";
}

function failureDetail(shell: ShellCoreStatus, timedOut: boolean): { message: string; log: string } {
  if (shell.state === "crashed") {
    return { message: `The core stopped unexpectedly (exit ${shell.code ?? "?"}).`, log: shell.stderrTail || "No output captured." };
  }
  if (shell.state === "stopped") return { message: "The core isn't running.", log: "No output captured." };
  if (timedOut) return { message: "The core is taking longer than expected to start.", log: "No output captured yet." };
  return { message: "The core isn't available.", log: "No output captured." };
}

function restartCore(): void {
  if (isTauri()) void invoke("core_restart").catch(() => {});
}

export interface LoadingProps {
  timeoutMs?: number;
  /** Default: Tauri `core_restart`. */
  onRetry?: () => void;
}

export function Loading({ timeoutMs = LOADING_TIMEOUT_MS, onRetry = restartCore }: LoadingProps): JSX.Element {
  const shell = shellStatus.value;
  const stream = conn.value;
  const [elapsed, setElapsed] = useState(0);
  const [timedOut, setTimedOut] = useState(false);
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => {
      const ms = Date.now() - t0;
      setElapsed(ms);
      if (ms >= timeoutMs) setTimedOut(true);
    }, 250);
    return () => clearInterval(id);
  }, [timeoutMs]);

  const failed = isFatal(shell) || timedOut;

  if (failed) {
    const { message, log } = failureDetail(shell, timedOut);
    return (
      <div class="boot" data-boot="loading" data-state="error">
        <div class="boot-stack" role="alertdialog" aria-label="Core didn't start">
          <CubeMark size={72} muted />
          <h1 class="boot-title">Mises couldn't start the core</h1>
          <p class="boot-msg">{message}</p>
          <div class="boot-actions">
            <button type="button" class="boot-btn boot-btn-primary" onClick={() => { setTimedOut(false); onRetry(); }}>Retry</button>
            <button type="button" class="boot-btn" aria-expanded={showLog} onClick={() => setShowLog((v) => !v)}>
              {showLog ? "Hide log" : "Show log"}
            </button>
          </div>
          {showLog && <pre class="boot-log" aria-label="Core log">{log}</pre>}
        </div>
      </div>
    );
  }

  return (
    <div class="boot" data-boot="loading" data-state="starting">
      <div class="boot-stack">
        <CubeMark size={72} spin />
        <div class="boot-status" role="status" aria-live="polite">{statusLine(shell, stream, elapsed)}</div>
      </div>
    </div>
  );
}
