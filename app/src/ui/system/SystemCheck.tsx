// System check: renders GET /system/check as status rows with a fix action each. Install fixes call
// POST /system/install/{dep}, stream `system_install` output into an expandable log, and re-check
// when the install finishes. Shown after the intro on first run (standalone, with "Continue") and
// from Profile → System.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { CoreClient } from "../../api/client";
import { onCoreEvent } from "../../api/events";
import { isInstallDep, type MisesEvent, type SystemCheckRow, type SystemFix, type SystemInstallEvent } from "../../api/types";
import { actions } from "../../state/store";
import { DragStrip } from "../boot/DragStrip";
import { ExternalLink } from "../common/ExternalLink";
import { Led, type LedTone } from "../common/Led";
import "./system.css";

export function statusTone(status: SystemCheckRow["status"]): LedTone {
  switch (status) {
    case "ok": return "green";
    case "outdated": return "amber";
    case "missing": return "amber";
    case "optional_missing": return "faint";
    case "error": return "red";
  }
}

export function statusLabel(status: SystemCheckRow["status"]): string {
  switch (status) {
    case "ok": return "OK";
    case "outdated": return "Outdated";
    case "missing": return "Missing";
    case "optional_missing": return "Optional";
    case "error": return "Error";
  }
}

/** The URL when it is http(s), else null — a fix link never gets a javascript:/data:/file: href. */
export function safeUrl(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? url : null;
  } catch {
    return null;
  }
}

type InstallState = { lines: string[]; status: "running" | "ok" | "fail" };

function parseInstall(ev: MisesEvent): SystemInstallEvent | null {
  const d = ev.data;
  if (typeof d.dep !== "string" || typeof d.line !== "string" || typeof d.done !== "boolean") return null;
  return { dep: d.dep, line: d.line, done: d.done, ok: typeof d.ok === "boolean" ? d.ok : null };
}

export interface SystemCheckProps {
  /** Full-screen gate with a Continue button (post-intro first run); otherwise an embedded panel. */
  standalone?: boolean;
  onDone?: () => void;
  client?: Pick<CoreClient, "systemCheck" | "systemInstall">;
  /** Event subscription (default: onCoreEvent). */
  subscribe?: (cb: (e: MisesEvent) => void) => () => void;
}

export function SystemCheck({ standalone = false, onDone, client, subscribe = onCoreEvent }: SystemCheckProps): JSX.Element {
  const [rows, setRows] = useState<SystemCheckRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installs, setInstalls] = useState<Record<string, InstallState>>({});
  const [openLogs, setOpenLogs] = useState<Record<string, boolean>>({});
  const [refused, setRefused] = useState<Record<string, SystemFix>>({});

  const api = (): Pick<CoreClient, "systemCheck" | "systemInstall"> => client ?? actions.client();

  async function load(): Promise<void> {
    setError(null);
    try {
      setRows(await api().systemCheck());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => { void load(); }, []);

  // Stream install output; when a dep finishes, mark it and re-check that row.
  useEffect(() => subscribe((ev) => {
    if (ev.type !== "system_install") return;
    const si = parseInstall(ev);
    if (!si) return;
    setInstalls((prev) => {
      const cur = prev[si.dep] ?? { lines: [], status: "running" as const };
      const lines = si.line ? [...cur.lines, si.line] : cur.lines;
      const status: InstallState["status"] = si.done ? (si.ok ? "ok" : "fail") : "running";
      return { ...prev, [si.dep]: { lines, status } };
    });
    if (si.done) void load();
  }), [subscribe]);

  async function install(dep: string): Promise<void> {
    // the dep name is core data: only one the app knows is ever sent (the core allowlists too)
    if (!isInstallDep(dep)) {
      setInstalls((p) => ({ ...p, [dep]: { lines: [`Not installed: "${dep}" is not a dependency this app installs.`], status: "fail" } }));
      setOpenLogs((p) => ({ ...p, [dep]: true }));
      return;
    }
    setRefused((r) => { const n = { ...r }; delete n[dep]; return n; });
    setInstalls((p) => ({ ...p, [dep]: { lines: [], status: "running" } }));
    setOpenLogs((p) => ({ ...p, [dep]: true }));
    try {
      const res = await api().systemInstall(dep);
      if (!res.ok) {
        setInstalls((p) => { const n = { ...p }; delete n[dep]; return n; });
        if (res.fix) setRefused((r) => ({ ...r, [dep]: res.fix as SystemFix }));
      }
    } catch (e) {
      setInstalls((p) => ({ ...p, [dep]: { lines: [`Install failed: ${e instanceof Error ? e.message : String(e)}`], status: "fail" } }));
    }
  }

  const body = (
    <div class="syscheck" data-standalone={standalone ? "" : undefined}>
      <div class="syscheck-head">
        <h2 class="syscheck-title">System check</h2>
        <button type="button" class="syscheck-recheck" onClick={() => void load()}>Re-check</button>
      </div>
      {error && <p class="syscheck-error" role="alert">Couldn't run the system check: {error}</p>}
      {!rows && !error && <p class="syscheck-loading" role="status">Checking your tools…</p>}
      {rows && (
        <ul class="syscheck-rows">
          {rows.map((row) => {
            const dep = row.fix?.dep ?? row.id;
            const depInstall = installs[dep];
            return (
              <li class="syscheck-row" key={row.id} data-status={row.status}>
                <div class="syscheck-main">
                  <Led tone={statusTone(row.status)} title={statusLabel(row.status)} />
                  <div class="syscheck-text">
                    <div class="syscheck-name">
                      {row.name}
                      {row.version && <span class="syscheck-ver"> {row.version}</span>}
                    </div>
                    <div class="syscheck-detail">{row.detail}</div>
                    {row.path && <div class="syscheck-path" title={row.path}>{row.path}</div>}
                  </div>
                  <div class="syscheck-fix">
                    {row.fix && <FixAction fix={row.fix} installing={depInstall?.status === "running"}
                      onInstall={() => void install(dep)} />}
                  </div>
                </div>
                {refused[dep] && <RefusedNote fix={refused[dep]} />}
                {depInstall && (
                  <div class="syscheck-install">
                    <button type="button" class="syscheck-log-toggle" aria-expanded={!!openLogs[dep]}
                      onClick={() => setOpenLogs((p) => ({ ...p, [dep]: !p[dep] }))}>
                      {depInstall.status === "running" ? "Installing…" : depInstall.status === "ok" ? "Installed" : "Install failed"}
                      {" · "}{openLogs[dep] ? "hide log" : "show log"}
                    </button>
                    {openLogs[dep] && (
                      <pre class="syscheck-log" aria-label={`${dep} install log`}>{depInstall.lines.join("\n") || "…"}</pre>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {standalone && (
        <div class="syscheck-foot">
          <button type="button" class="boot-btn boot-btn-primary" onClick={() => onDone?.()}>Continue</button>
        </div>
      )}
    </div>
  );

  if (standalone) {
    return <div class="boot" data-boot="systemcheck"><DragStrip /><div class="syscheck-wrap">{body}</div></div>;
  }
  return body;
}

function FixAction({ fix, installing, onInstall }: { fix: SystemFix; installing: boolean; onInstall: () => void }): JSX.Element {
  if (fix.kind === "install") {
    return <button type="button" class="syscheck-btn" disabled={installing} onClick={onInstall}>{fix.label}</button>;
  }
  const url = fix.kind === "open_url" ? safeUrl(fix.url) : null;
  if (url) {
    // The full URL is shown so the domain is visible before it opens in the system browser.
    return <ExternalLink class="syscheck-btn syscheck-link" href={url}>{fix.label}</ExternalLink>;
  }
  // setting: a plain label; wiring to a specific setting is left to the owning section.
  return <span class="syscheck-fixlabel">{fix.label}</span>;
}

function RefusedNote({ fix }: { fix: SystemFix }): JSX.Element {
  const url = fix.kind === "open_url" ? safeUrl(fix.url) : null;
  return (
    <div class="syscheck-refused" role="note">
      {url
        ? <>Can't install automatically. <ExternalLink href={url}>{fix.label}</ExternalLink>, then re-check.</>
        : fix.label}
    </div>
  );
}
