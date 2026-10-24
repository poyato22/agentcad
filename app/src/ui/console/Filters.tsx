// [<run label>] [all] [decisions] [tool calls] [files] [errors N]  ……  [⤓ follow]
// Scope (this run / all runs) and kind are independent; clicking the active kind again shows
// everything. Counts come from the once-per-frame log build.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { actions, filter, follow, type LogFilter } from "../../state/store";
import { effectiveRunId, labelOf, logFrame, retainLogFrames } from "./derived";

const KINDS: { kind: Exclude<LogFilter["kind"], "everything">; text: string }[] = [
  { kind: "decisions", text: "decisions" }, { kind: "tool_calls", text: "tool calls" }, { kind: "files", text: "files" }, { kind: "errors", text: "errors" },
];

export function Filters(): JSX.Element {
  useEffect(() => retainLogFrames(), []);
  const f = filter.value;
  const runId = effectiveRunId.value;
  const errors = logFrame.value.counts.errors;
  const chip = (on: boolean, text: string, onClick: () => void, extra?: JSX.Element, cls = "") => (
    <button type="button" class={`con-chipf${cls}${on ? " on" : ""}`} aria-pressed={on} onClick={onClick} title={text}>{text}{extra}</button>
  );
  return (
    <div class="con-filters" data-filters role="toolbar" aria-label="Log filters">
      {runId && chip(f.scope === "run", labelOf(runId), () => actions.setFilter({ ...f, scope: "run" }), undefined, " con-chipf-run")}
      {chip(f.scope === "all", "all", () => actions.setFilter({ ...f, scope: "all" }))}
      {KINDS.map((k) => chip(f.kind === k.kind, k.kind === "errors" ? "errors " : k.text,
        () => actions.setFilter({ ...f, kind: f.kind === k.kind ? "everything" : k.kind }),
        k.kind === "errors" ? <span class={errors > 0 ? "con-err-n" : ""}>{String(errors)}</span> : undefined))}
      <span class="con-filters-gap" />
      {chip(follow.value, "⤓ follow", () => actions.setFollow(!follow.value))}
    </div>
  );
}
