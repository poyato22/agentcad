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
import { allTargets } from "./isExternal";
import { parseCommand, type Command } from "./parseCommand";
import "./command.css";

const FILTER_KINDS: readonly LogFilter["kind"][] = ["everything", "decisions", "tool_calls", "files", "errors"];

const HELP = [
  "@designer … message a run by agent or label · @all … every finished (done) run of this core session",
  "/new  new run · /stop  stop the selected run · /stopall  stop every run (⌘⇧.)",
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
  const selected = effectiveRun.value ?? undefined;
  const chip = chipText(parsed, selected, allRuns);

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
      if (run.role === "lead" && !run.archived && run.project_id) await actions.client().createRun({ prompt: text, project_id: run.project_id });
      else await actions.client().message(run.id, text);
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
    if (e.key === "Enter" && !e.shiftKey) {
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
        <span class="cmd-prompt" aria-hidden="true">❯</span>
        <textarea
          ref={input}
          class="cmd-input"
          aria-label="Command line"
          placeholder="message an agent, @all, or / for commands…"
          rows={rows}
          value={value}
          spellcheck={false}
          onInput={(e) => { setValue(e.currentTarget.value); cursor.current = -1; setError(null); }}
          onKeyDown={onKeyDown}
        />
      </div>
      {error && <div class="cmd-error text" role="alert">{error}</div>}
      {info && !error && <div class="cmd-info text dim" data-testid="cmd-info">{info}</div>}
    </div>
  );
}

function chipText(cmd: Command, selected: AgentRun | undefined, all: readonly AgentRun[]): string {
  if (cmd.kind === "slash") return `/${cmd.name}`;
  if (cmd.kind === "invalid") return "";
  if (cmd.target === "all") return "→ all idle runs";
  if (cmd.target === "selected") return selected ? `→ ${runLabel(selected, all)}` : "→ no run selected";
  return `→ ${cmd.target.label}`;
}
