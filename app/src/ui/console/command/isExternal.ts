import type { AgentRun } from "../../../api/types";

/** Runs driven by the user's own Claude Code: external in the core, but they take messages (the
 *  core continues the session, or resumes it with --resume, even after a restart). */
export const isClaudeCodeRun = (run: AgentRun): boolean => run.provider === "claude-code";

/** External runs (driven by an outside agent) cannot take messages: the core's persisted flag.
 *  Claude Code runs are the exception. */
export const isExternal = (run: AgentRun): boolean => run.external === true && !isClaudeCodeRun(run);

/** The current core session: its pid when the shell knows it, else the runs this UI saw start. */
export interface CoreSessionInfo { pid: number | null; started: ReadonlySet<string> }

/**
 * Is the run driven by this core process (so it can be resumed with a message)? By owner_pid when
 * the core's pid is known; otherwise only runs this UI saw start in this session (conservative).
 * A finished Claude Code run always can: the core resumes its session.
 */
export function isLiveInSession(run: AgentRun, s: CoreSessionInfo): boolean {
  if (isClaudeCodeRun(run)) return true;
  if (s.pid !== null) return run.owner_pid === s.pid;
  return s.started.has(run.id);
}

/**
 * @all: only finished ("done") runs that are live in this core session and not external.
 * Running, stopped (cancelled/failed), external and earlier-session runs are skipped and counted.
 */
export function allTargets(list: readonly AgentRun[], s: CoreSessionInfo): {
  targets: AgentRun[]; skipped: { working: number; stopped: number; external: number; notLive: number };
} {
  const targets: AgentRun[] = [];
  const skipped = { working: 0, stopped: 0, external: 0, notLive: 0 };
  for (const r of list) {
    if (isExternal(r)) skipped.external++;
    else if (r.status === "running") skipped.working++;
    else if (r.status !== "done") skipped.stopped++;
    else if (!isLiveInSession(r, s)) skipped.notLive++;
    else targets.push(r);
  }
  return { targets, skipped };
}
