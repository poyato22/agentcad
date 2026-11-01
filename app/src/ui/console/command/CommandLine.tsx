// The composer at the foot of the dock (P10 E24: one chat per build). It always talks to the
// build's lead — the first message of a build creates it (chat.ts) — so there is no run picker and
// no "New run". Viewing a sub-agent's thread is read-only; "Message this agent" points the
// composer at it until you go back to the lead (or show another thread). "@agent …" still
// addresses a run by name, "@all" every finished run, and "/" the commands. Edit (E9) puts a
// message of yours back here: sending it starts a new turn and marks the old one edited.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { registerShortcut } from "../../../lib/keys";
import { runLabel } from "../../../lib/names";
import { agentChips, removeAgentChip } from "../../../state/agentChips";
import { activeBuildId } from "../../../state/builds";
import { effectiveRun } from "../../../state/selection";
import { actions, corePid, filter, follow, runs, sessionRuns, type LogFilter } from "../../../state/store";
import { stopAll } from "../../topbar/stopAll";
import { buildLead, composerDraft, markEdited, messageTarget, sendChat } from "../chat";
import { leadUnreachable } from "../leadFollow";
import { shownPending } from "../queued";
import { startErrorText } from "../startError";
import { stopBusy, stoppable as anyStoppable } from "../stopTarget";
import { ThinkControl } from "../think/ThinkControl";
import { composerOpen } from "../view";
import { allTargets } from "./isExternal";
import { parseCommand, type Command } from "./parseCommand";
import "./command.css";

const FILTER_KINDS: readonly LogFilter["kind"][] = ["everything", "decisions", "tool_calls", "files", "errors"];

const HELP = [
  "your message goes to the build's lead · @designer … message a run by agent or label · @all … every finished (done) run of this core session",
  "/stop  stop the shown run · Esc  stop the agents · /stopall  stop every run (⌘⇧.)",
  "/follow [on|off] · /filter everything|decisions|tool_calls|files|errors|all|run · /clear",
  "approvals are decided on their card, never here",
].join("\n");

/** The core's message plus its hint: "run is still working — wait or stop it". */
function describeError(e: unknown): string {
  if (e instanceof CoreError) return startErrorText(e);
  return e instanceof Error ? e.message : String(e);
}

export function CommandLine(): JSX.Element {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** The thread row being edited (Edit on one of your messages), or null. */
  const [editOf, setEditOf] = useState<string | null>(null);
  /** What you were typing when you chose Edit: kept, and back once the edit is sent or cancelled. */
  const stash = useRef<string | null>(null);
  const restore = useRef<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const history = useRef<string[]>([]);
  const cursor = useRef<number>(-1); // -1 = not browsing history
  const draft = useRef("");

  useEffect(() => registerShortcut("Meta+K", () => input.current?.focus()), []);

  const allRuns = [...runs.value.values()];
  const parsed: Command = value.trim() ? parseCommand(value, allRuns) : { kind: "message", target: "selected", text: "" };
  // the thread on screen (or one just started that the store doesn't list yet)
  const shown = effectiveRun.value ?? shownPending.value ?? null;
  const lead = buildLead.value;
  const chosenId = messageTarget.value;
  const chosen = chosenId ? runs.value.get(chosenId) ?? null : null;
  // a sub-agent only when the user asked to message it; else the lead (a core without builds:
  // the run on screen)
  const target: AgentRun | null = chosen && chosen.role !== "lead" ? chosen : activeBuildId.value ? null : shown;
  const viewingSub = shown !== null && shown.role !== "lead" && shown.id !== target?.id && activeBuildId.value !== null;
  const chip = chipText(parsed, target, lead, allRuns, corePid.value);
  // the send button is Stop while the build's lead (or a sub-agent) works (Plan 9 D2), or while
  // a background study / review the agents started still runs (B3)
  const stoppable = anyStoppable.value;

  // "Message this agent" holds only while its thread is the one shown
  useEffect(() => { if (chosenId && shown?.id !== chosenId) messageTarget.value = null; }, [shown?.id, chosenId]);

  // Edit: the words come back here, ready to change and send again
  const want = composerDraft.value;
  useEffect(() => {
    if (!want) return;
    composerDraft.value = null;
    // a draft of your own is never lost to Edit: it waits (the first one, across repeated Edits)
    if (editOf === null && value.trim() && value !== want.text) stash.current = value;
    setValue(want.text);
    setEditOf(want.editOf);
    setError(null);
    cursor.current = -1;
    requestAnimationFrame(() => {
      const el = input.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }, [want]);

  // "Use with agent" (parts directory) and other openers ask for the composer: focus it
  const ask = composerOpen.value;
  useEffect(() => { if (ask) { composerOpen.value = false; input.current?.focus(); } }, [ask]);

  async function execute(cmd: Command): Promise<boolean> {
    if (cmd.kind === "invalid") { setError(cmd.reason); return false; }
    if (cmd.kind === "slash") return slash(cmd.name, cmd.arg);
    if (cmd.target === "all") {
      const { targets, skipped } = allTargets(allRuns, { pid: corePid.value, started: sessionRuns.value });
      if (targets.length === 0) {
        setError("no idle runs to message — @all only resumes finished (done) runs of this core session");
        return false;
      }
      const results = await Promise.allSettled(targets.map((r) => actions.client().message(r.id, cmd.text)));
      const failed = results.flatMap((res, i) => (res.status === "rejected" ? [`${runLabel(targets[i], allRuns)}: ${describeError(res.reason)}`] : []));
      const sent = targets.length - failed.length;
      const parts = [`sent to ${sent} run${sent === 1 ? "" : "s"}`];
      if (skipped.working) parts.push(`skipped ${skipped.working} still working`);
      if (skipped.stopped) parts.push(`skipped ${skipped.stopped} stopped`);
      if (skipped.external) parts.push(`skipped ${skipped.external} external`);
      if (skipped.notLive) parts.push(`skipped ${skipped.notLive} from an earlier core session`);
      setInfo(parts.join(" · "));
      if (failed.length) { setError(failed.join("\n")); return false; }
      return true;
    }
    let to: AgentRun | null = target;
    if (cmd.target !== "selected") {
      const label = cmd.target.label;
      const named = allRuns.find((r) => runLabel(r, allRuns) === label);
      if (!named) { setError(`no run matches @${label}`); return false; }
      to = named;
    }
    try {
      const sent = await sendChat(cmd.text, to);
      if (sent.note) setInfo(sent.note);
      if (editOf) { markEdited(editOf); setEditOf(null); restore.current = stash.current; stash.current = null; }
      return true;
    } catch (e) {
      setError(describeError(e));
      return false;
    }
  }

  async function slash(name: string, arg: string): Promise<boolean> {
    try {
      switch (name) {
        case "stop": {
          const run = shown ?? lead;
          if (!run) { setError("no agent is working on this build"); return false; }
          await actions.client().cancel(run.id);
          return true;
        }
        case "stopall": await stopAll(); return true; // same request, guard and toast as ⌘⇧.
        case "follow": {
          const a = arg.toLowerCase();
          if (a && a !== "on" && a !== "off") { setError("/follow on or /follow off"); return false; }
          actions.setFollow(a ? a === "on" : !follow.value);
          return true;
        }
        case "filter": {
          const a = arg.toLowerCase();
          const f = filter.value;
          if (a === "all" || a === "run") actions.setFilter({ ...f, scope: a });
          else if ((FILTER_KINDS as readonly string[]).includes(a)) actions.setFilter({ ...f, kind: a as LogFilter["kind"] });
          else { setError(`/filter ${FILTER_KINDS.join("|")}|all|run`); return false; }
          return true;
        }
        case "clear": history.current = []; setInfo(null); return true;
        case "help": setInfo(HELP); return true;
        default: return false;
      }
    } catch (e) {
      setError(describeError(e));
      return false;
    }
  }

  async function submit(): Promise<void> {
    if (busy) return;
    const line = value;
    if (!line.trim()) return;
    setBusy(true);
    setError(null);
    setInfo(null);
    const cmd = parseCommand(line, allRuns);
    const ok = await execute(cmd);
    setBusy(false);
    if (ok) {
      if (cmd.kind !== "slash" || cmd.name !== "clear") history.current.push(line);
      cursor.current = -1;
      setValue(restore.current ?? "");
      restore.current = null;
    }
  }

  function browse(dir: -1 | 1): boolean {
    const h = history.current;
    if (cursor.current === -1) {
      if (dir === 1 || h.length === 0) return false;
      draft.current = value;
      cursor.current = h.length - 1;
    } else {
      cursor.current += dir;
      if (cursor.current >= h.length) { cursor.current = -1; setValue(draft.current); return true; }
      if (cursor.current < 0) cursor.current = 0;
    }
    setValue(h[cursor.current] ?? "");
    return true;
  }

  function cancelEdit(): void {
    setEditOf(null);
    setValue(stash.current ?? "");
    stash.current = null;
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (e.isComposing) return;
    const el = e.currentTarget as HTMLTextAreaElement;
    if (e.key === "Escape" && editOf !== null && !e.defaultPrevented) {
      // Esc first leaves the edit: your earlier draft comes back
      e.preventDefault();
      cancelEdit();
    } else if (e.key === "Escape" && stoppable && !e.defaultPrevented) {
      // Esc stops the busy agents like the Stop button (Plan 9 D2) — not one that waits on your
      // approval or answer, and not when another handler (Animate) already took this Esc
      e.preventDefault();
      void stopBusy(true);
    } else if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    } else if (e.key === "ArrowUp" && !e.shiftKey && !el.value.slice(0, el.selectionStart).includes("\n")) {
      if (browse(-1)) e.preventDefault();
    } else if (e.key === "ArrowDown" && !e.shiftKey && !el.value.slice(el.selectionEnd).includes("\n")) {
      if (browse(1)) e.preventDefault();
    }
  }

  const rows = Math.min(5, Math.max(1, value.split("\n").length));
  const shownLabel = shown ? runLabel(shown, allRuns) : "";
  return (
    <div class="cmd" data-command-line>
      {viewingSub && (
        <div class="cmd-view" data-viewing-sub={shown?.id}>
          <span class="cmd-view-t dim text">{`Viewing ${shownLabel} · your message goes to the lead`}</span>
          <button type="button" class="cmd-link" data-testid="message-this-agent" onClick={() => { if (shown) messageTarget.value = shown.id; input.current?.focus(); }}>
            Message this agent
          </button>
        </div>
      )}
      <div class="cmd-chips">
        {chip && <span class="cmd-chip dim" data-testid="target-chip">{chip}</span>}
        {chosen && target === chosen && (
          <button type="button" class="cmd-link" data-testid="back-to-lead" onClick={() => { messageTarget.value = null; input.current?.focus(); }}>
            Message the lead instead
          </button>
        )}
        {editOf !== null && (
          <span class="cmd-chip cmd-editing" data-testid="editing-chip">
            {stash.current ? "Editing your message · your draft comes back after" : "Editing your message · sends as a new turn"}
            <button type="button" class="cmd-link" onClick={() => { cancelEdit(); input.current?.focus(); }}>Cancel edit</button>
          </span>
        )}
      </div>
      {agentChips.value.length > 0 && (
        // parts-directory items ("Use with agent") ride along with your next message; × drops one
        <div class="cv-context cmd-context" role="list" aria-label="Context for the agent" data-testid="cmd-agent-chips">
          {agentChips.value.map((c) => (
            <span key={c.id} class="cv-context-chip" role="listitem" data-chip={c.id} title={`${c.id}: the agent reads it with catalog_get`}>
              <span class="cv-context-label">{c.label}</span>
              <button type="button" class="cv-context-x" aria-label={`Remove ${c.label}`} onClick={() => removeAgentChip(c.id)}>×</button>
            </span>
          ))}
        </div>
      )}
      <div class="cmd-row">
        <ThinkControl />
        <textarea
          ref={input}
          class="cmd-input"
          aria-label="Command line"
          placeholder={lead || target ? "Message the agent, @all, or / for commands…" : "Describe what to build — your first message starts the build's agent"}
          rows={rows}
          value={value}
          spellcheck={false}
          onInput={(e) => { setValue(e.currentTarget.value); cursor.current = -1; setError(null); }}
          onKeyDown={onKeyDown}
        />
        {stoppable
          ? (
            <button type="button" class="cmd-send is-stop" aria-label="Stop" title="Stop the agents (Esc)" data-testid="composer-stop"
              onClick={() => { void stopBusy(); input.current?.focus(); }}>
              <span class="cmd-stop-sq" aria-hidden="true" />
            </button>
          )
          : (
            <button type="button" class="cmd-send" aria-label="Send" title="Send (⏎)" data-testid="composer-send"
              disabled={busy || !value.trim()} onClick={() => { void submit(); input.current?.focus(); }}>
              <span aria-hidden="true">↑</span>
            </button>
          )}
      </div>
      {error && <div class="cmd-error text" role="alert">{error}</div>}
      {info && !error && <div class="cmd-info text dim" data-testid="cmd-info">{info}</div>}
    </div>
  );
}

function chipText(cmd: Command, target: AgentRun | null, lead: AgentRun | null, all: readonly AgentRun[], pid: number | null): string {
  if (cmd.kind === "slash") return `/${cmd.name}`;
  if (cmd.kind === "invalid") return "";
  if (cmd.target === "all") return "→ all idle runs";
  if (cmd.target === "selected") {
    const to = target ?? lead;
    if (!to) return "→ the build's agent (starts with your first message)";
    if (to.project_id && leadUnreachable(to, pid)) return "→ new lead (the earlier one can't be reached)";
    return `→ ${runLabel(to, all)}`;
  }
  return `→ ${cmd.target.label}`;
}
