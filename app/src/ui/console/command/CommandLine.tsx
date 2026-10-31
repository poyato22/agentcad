import { showRoutedNotes } from "../routedNotes";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { registerShortcut } from "../../../lib/keys";
import { runLabel } from "../../../lib/names";
import { agentChips, chipsPromptLine, clearAgentChips } from "../../../state/agentChips";
import { effectiveRun } from "../../../state/selection";
import { actions, corePid, filter, follow, runs, sessionRuns, type LogFilter } from "../../../state/store";
import { stopAll } from "../../topbar/stopAll";
import { followLead, leadUnreachable, NEW_LEAD_NOTE } from "../leadFollow";
import { shownPending } from "../queued";
import { busyRuns, stopBusy } from "../stopTarget";
import { allTargets } from "./isExternal";
import { parseCommand, type Command } from "./parseCommand";
import "./command.css";

const FILTER_KINDS: readonly LogFilter["kind"][] = ["everything", "decisions", "tool_calls", "files", "errors"];

const HELP = [
  "@designer … message a run by agent or label · @all … every finished (done) run of this core session",
  "/new  new run · /stop  stop the selected run · Esc  stop the agents · /stopall  stop every run (⌘⇧.)",
  "/follow [on|off] · /filter everything|decisions|tool_calls|files|errors|all|run · /clear",
  "approvals are decided on their card, never here",
].join("\n");

/** The core's message plus its hint: "run is still working — wait or stop it". */
function describeError(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

export function CommandLine(): JSX.Element {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const history = useRef<string[]>([]);
  const cursor = useRef<number>(-1); // -1 = not browsing history
  const draft = useRef("");

  useEffect(() => registerShortcut("Meta+K", () => input.current?.focus()), []);

  const allRuns = [...runs.value.values()];
  const parsed: Command = value.trim() ? parseCommand(value, allRuns) : { kind: "message", target: "selected", text: "" };
  // the run the console shows (selected, else the newest running run): what the chip names
  // (or the run just started/followed that the store doesn't list yet — I2: a second message sent
  // right after a new lead took over still has a target)
  const selected = effectiveRun.value ?? shownPending.value ?? undefined;
  const chip = chipText(parsed, selected, allRuns, corePid.value);
  // the send button is Stop while the build's lead (or a sub-agent) works (Plan 9 D2)
  const stoppable = busyRuns.value.length > 0;

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
    let run: AgentRun | undefined;
    if (cmd.target === "selected") {
      run = selected;
      if (!run) { setError("select a run first, or address one with @agent"); return false; }
    } else {
      const label = cmd.target.label;
      run = allRuns.find((r) => runLabel(r, allRuns) === label);
      if (!run) { setError(`no run matches @${label}`); return false; }
    }
    try {
      // "Use with agent" chips ride along to the run you message too, not only a new run (M-6)
      const chipLine = chipsPromptLine(agentChips.peek());
      const text = chipLine ? `${cmd.text}\n\n${chipLine}` : cmd.text;
      // the build's lead takes a prompt even while it works: through POST /runs the core routes
      // it to that lead and queues it behind the current step (U3c); other runs get /message
      // (an archived or unreachable lead too: the core hands the prompt to the build's current
      // lead or starts a new one, and the console follows that run — I2)
      if (run.role === "lead" && run.project_id) {
        const res = await actions.client().createRun({ prompt: text, project_id: run.project_id });
        showRoutedNotes(res);
        const note = followLead(run, res);
        if (note) setInfo(note);
      } else {
        try {
          await actions.client().message(run.id, text);
        } catch (e) {
          // a run of an earlier core process (after a restart) is gone from this core: rather than
          // fail with "unknown run", hand the message to its build's lead and follow that (I2)
          if (!(e instanceof CoreError && e.code === "not_found" && /unknown run/i.test(e.message) && run.project_id)) throw e;
          const res = await actions.client().createRun({ prompt: text, project_id: run.project_id });
          showRoutedNotes(res);
          followLead(run, res);
          setInfo(res.new_lead ? NEW_LEAD_NOTE : "that run ended with the earlier Mises session · sent to the build's lead");
        }
      }
      if (chipLine) clearAgentChips();
      return true;
    } catch (e) {
      setError(describeError(e));
      return false;
    }
  }

  async function slash(name: string, arg: string): Promise<boolean> {
    try {
      switch (name) {
        case "new": actions.openNewRun(); return true;
        case "stop":
          if (!selected) { setError("select a run first"); return false; }
          await actions.client().cancel(selected.id);
          return true;
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
      setValue("");
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

  function onKeyDown(e: KeyboardEvent): void {
    if (e.isComposing) return;
    const el = e.currentTarget as HTMLTextAreaElement;
    if (e.key === "Escape" && stoppable && !e.defaultPrevented) {
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
  return (
    <div class="cmd" data-command-line>
      {chip && <div class="cmd-chip dim" data-testid="target-chip">{chip}</div>}
      {agentChips.value.length > 0 && (
        <div class="cmd-chip dim text" data-testid="cmd-agent-chips">
          {`with ${agentChips.value.length} parts-directory item${agentChips.value.length === 1 ? "" : "s"}: ${agentChips.value.map((c) => c.label).join(", ")} (sent with your next message to one run)`}
        </div>
      )}
      <div class="cmd-row">
        <textarea
          ref={input}
          class="cmd-input"
          aria-label="Command line"
          placeholder="Message the agent, @all, or / for commands…"
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

function chipText(cmd: Command, selected: AgentRun | undefined, all: readonly AgentRun[], pid: number | null): string {
  if (cmd.kind === "slash") return `/${cmd.name}`;
  if (cmd.kind === "invalid") return "";
  if (cmd.target === "all") return "→ all idle runs";
  if (cmd.target === "selected") {
    if (!selected) return "→ no run selected";
    if (selected.project_id && leadUnreachable(selected, pid)) return "→ new lead (the earlier one can't be reached)";
    return `→ ${runLabel(selected, all)}`;
  }
  return `→ ${cmd.target.label}`;
}
