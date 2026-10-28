// One tool call row: "14:02:14 ⚙ mirror_measure part_id=mount_plate", then "◆ <summary>" once the
// result landed (✕ in red on failure). ↳ marks agent_spawn, ⎇ onshape_propose. A call still
// running has a blue glyph (being changed by an agent). Click the head to see the input/output.
// Tool names, inputs and summaries are untrusted: text only.
import { useState } from "preact/hooks";
import type { JSX } from "preact";
import { fmtClock } from "../../lib/format";
import type { LogItem } from "./logModel";

type Call = Extract<LogItem, { kind: "call" }>;

export function callGlyph(item: Call): string {
  if (item.ok === false) return "✕";
  if (item.name === "agent_spawn") return "↳";
  if (item.name.startsWith("onshape_propose")) return "⎇";
  return "⚙";
}

/** Short one-line preview of the input: "part_id=mount_plate vars={…}". */
export function inputPreview(input: Record<string, unknown>, max = 80): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(input)) {
    if (typeof v === "string") parts.push(`${k}=${v.split("\n")[0]}`);
    else if (typeof v === "number" || typeof v === "boolean") parts.push(`${k}=${v}`);
    else if (v !== null && typeof v === "object") parts.push(`${k}={…}`);
  }
  const s = parts.join(" ");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** "2 Onshape calls · 1 sim_run step" for steps shown on this call's row (onshape_call etc.). */
export function attachedText(item: Call): string {
  if (item.attached.length === 0) return "";
  const counts = new Map<string, number>();
  for (const s of item.attached) counts.set(s.kind, (counts.get(s.kind) ?? 0) + 1);
  return [...counts].map(([kind, n]) => (kind === "onshape_call"
    ? `${n} Onshape call${n === 1 ? "" : "s"}` : `${n} ${kind} step${n === 1 ? "" : "s"}`)).join(" · ");
}

/** onshape_call: "GET /api/v10/documents/d1 → 200"; other kinds: "kind · summary". */
function attachedLine(s: Call["attached"][number]): string {
  const p = s.payload;
  if (s.kind === "onshape_call") return `${String(p.method ?? "")} ${String(p.path ?? "")} → ${String(p.status ?? "")}${p.counted === false ? " (not counted)" : ""}`;
  const out = (typeof p.output === "object" && p.output !== null ? p.output : {}) as Record<string, unknown>;
  const summary = typeof out.summary === "string" ? out.summary : typeof p.summary === "string" ? p.summary : "";
  return summary ? `${s.kind} · ${summary}` : s.kind;
}

function pretty(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2) ?? "";
  } catch {
    return String(v);
  }
}

export function ToolCallRow(props: { item: Call; label?: string }): JSX.Element {
  const { item, label } = props;
  const [open, setOpen] = useState(false);
  const glyph = callGlyph(item);
  const running = item.result === null;
  const tone = item.ok === false ? "con-g-fail" : running ? "con-g-live" : "";
  const dur = item.endedTs !== null ? `${Math.max(0, item.endedTs - item.startedTs).toFixed(1)}s` : null;
  return (
    <div class="con-call" data-ok={item.ok === null ? "running" : String(item.ok)} data-changing={running ? "" : undefined}>
      <div class="con-row con-click" data-call-head role="button" tabIndex={0} aria-expanded={open}
        onClick={() => setOpen(!open)} onKeyDown={(e) => { if (e.key === "Enter") setOpen(!open); }}>
        <span class="con-ts">{fmtClock(item.startedTs)}</span>
        <span class={`con-g ${tone}`}>{glyph}</span>
        <span class="con-body">
          {label && <span class="faint text">{`${label} · `}</span>}
          <span class="ink text">{item.name}</span>
          {" "}<span class="faint text">{inputPreview(item.input)}</span>
          {running && <span class="faint"> · running</span>}
          {dur && <span class="faint">{` · ${dur}`}</span>}
          {attachedText(item) && <span class="faint">{` · ${attachedText(item)}`}</span>}
        </span>
      </div>
      {item.summary !== null && (
        <div class="con-row con-result">
          <span class="con-ts" />
          <span class={`con-g ${item.ok === false ? "con-g-fail" : "faint"}`}>{item.ok === false ? "✕" : "◆"}</span>
          <span class="con-body">
            <span class="dim text">{item.summary}</span>
            {item.hint && <span class="faint text con-hint">{item.hint}</span>}
          </span>
        </div>
      )}
      {open && <CallDetails item={item} />}
    </div>
  );
}

/** The raw input / output of a call (and what happened during it), as text. */
export function CallDetails({ item, class: cls = "con-details" }: { item: Call; class?: string }): JSX.Element {
  const output = item.result ? (item.result.payload.output ?? item.result.payload) : null;
  return (
    <div class={`con-box ${cls}`} data-call-details>
      <div class="con-box-hd"><b>input</b></div>
      <pre class="con-box-bd text">{pretty(item.input)}</pre>
      {output !== null && <div class="con-box-hd"><b>output</b></div>}
      {output !== null && <pre class="con-box-bd text">{pretty(output)}</pre>}
      {item.attached.length > 0 && <div class="con-box-hd"><b>during this call</b></div>}
      {item.attached.length > 0 && (
        <pre class="con-box-bd text">{item.attached.map(attachedLine).join("\n")}</pre>
      )}
    </div>
  );
}
