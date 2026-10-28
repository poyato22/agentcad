// The conversation view: the shown run as a thread — your messages, the agent's replies, and one
// compact line per thing it did ("measured mount plate · 8.0 cm³") with ▸ for the raw tool
// input/output. Approval and question cards sit inline (the same cards as the Details view);
// failed calls and errors are always their own red line with the hint. Everything agent- or
// tool-provided renders as text. The view sticks to the bottom unless the user scrolled up.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { effectiveRun } from "../../state/selection";
import { coreStatus } from "../../state/store";
import { Button } from "../common/Button";
import { ApprovalCard } from "./approvals/ApprovalCard";
import { QuestionCard } from "./approvals/QuestionCard";
import { labelOf, retainThreadFrames, threadFrame, toggleFold } from "./derived";
import { DiffView } from "./DiffView";
import { shownPending } from "./queued";
import { CallDetails } from "./ToolCallRow";
import type { ThreadItem } from "./threadModel";
import { composerOpen } from "./view";

const AT_BOTTOM_PX = 24;

export function Conversation(): JSX.Element {
  useEffect(() => retainThreadFrames(), []);
  const frame = threadFrame.value;
  const run = effectiveRun.value ?? shownPending.value;
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const lastRun = useRef<string | null>(null);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (lastRun.current !== frame.runId) { stick.current = true; lastRun.current = frame.runId; }
    if (stick.current) el.scrollTop = el.scrollHeight;
  });

  const onScroll = () => {
    const el = scroller.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_BOTTOM_PX;
  };

  return (
    <div class="cv-thread" ref={scroller} onScroll={onScroll} role="log" aria-label="Conversation" aria-live="off" data-conversation>
      {!run && frame.items.length === 0 && <Empty />}
      {frame.items.map((it) => <Item key={it.key} item={it} agent={run?.agent ?? "agent"} />)}
      {run && frame.items.length === 0 && run.task && (
        <Item item={{ kind: "user", key: "task", text: run.task, ts: run.created_at, stepIds: [] }} agent={run.agent} />
      )}
      {run && run.status === "queued" && <Queued />}
    </div>
  );
}

function Empty(): JSX.Element {
  return (
    <div class="cv-empty">
      <p class="dim">No runs yet. Describe a change and an agent will make it, measure it and ask before anything reaches Onshape.</p>
      <Button onClick={() => { composerOpen.value = true; }}>+ New run</Button>
    </div>
  );
}

function Queued(): JSX.Element {
  const p = coreStatus.value?.parallel;
  const why = p ? ` · ${p.auto ? "Auto" : "limit"}: ${p.limit} on this Mac` : "";
  return <div class="cv-note faint" data-queued>{`waiting for a free agent slot${why}`}</div>;
}

function Item({ item, agent }: { item: ThreadItem; agent: AgentRun["agent"] }): JSX.Element {
  switch (item.kind) {
    case "user":
      return (
        <div class="cv-msg cv-user" data-msg="user" data-row data-kind="message">
          <div class="label">you</div>
          <div class="cv-text text">{item.text}</div>
        </div>
      );
    case "agent":
      return (
        <div class="cv-msg cv-agent" data-msg="agent" data-row data-kind={item.live ? "live" : "message"} data-changing={item.live ? "" : undefined}>
          <div class="label">{agent}</div>
          <div class="cv-text text">{item.text}{item.live && <span class="cv-caret" aria-hidden="true">▍</span>}</div>
        </div>
      );
    case "activity":
      return <Activity item={item} />;
    case "fold":
      return (
        <button type="button" class="cv-fold text" aria-expanded="false" data-row data-kind="fold" onClick={() => toggleFold(item.foldKey)}>
          {`▸ ${item.text}`}
        </button>
      );
    case "error":
      return (
        <div class="cv-line cv-error" data-error data-row data-kind="error">
          <span class="cv-g cv-fail" aria-hidden="true">✕</span>
          <span class="cv-body">
            <span class="cv-fail text">{item.message ? `${item.code} · ${item.message}` : item.code}</span>
            {item.hint && <span class="cv-hint faint text">{item.hint}</span>}
          </span>
        </div>
      );
    case "note":
      return <div class="cv-note faint text" data-row data-kind="note">{item.text}</div>;
    case "approval":
      return <div class="cv-card" data-row data-kind="approval"><ApprovalCard approval={item.approval} runLabel={labelOf(item.approval.run_id)} /></div>;
    case "question":
      return <div class="cv-card" data-row data-kind="question"><QuestionCard question={item.question} runLabel={labelOf(item.question.run_id)} /></div>;
  }
}

function Activity({ item }: { item: Extract<ThreadItem, { kind: "activity" }> }): JSX.Element {
  const [open, setOpen] = useState(false);
  const canOpen = item.call !== null || item.diff !== null || item.extra.length > 0;
  const glyph = item.tone === "fail" ? "✕" : item.tone === "running" ? "•" : "·";
  return (
    <div class="cv-line" data-activity data-tone={item.tone} data-changing={item.tone === "running" ? "" : undefined} data-row data-kind="call">
      {canOpen
        ? (
          <button type="button" class="cv-x" aria-expanded={open} aria-label={`Show details of ${item.text}`} onClick={() => setOpen(!open)}>
            {open ? "▾" : "▸"}
          </button>
        )
        : <span class="cv-x" aria-hidden="true" />}
      <span class={`cv-g${item.tone === "fail" ? " cv-fail" : item.tone === "running" ? " cv-live" : " faint"}`} aria-hidden="true">{glyph}</span>
      <span class="cv-body">
        <span class={`text${item.tone === "fail" ? " cv-fail" : " dim"}`} data-activity-text>{item.text}</span>
        {item.hint && <span class="cv-hint faint text">{item.hint}</span>}
        {open && (
          <span class="cv-open">
            {item.diff && <DiffView item={item.diff} />}
            {item.call && <CallDetails item={item.call} class="cv-details" />}
            {item.extra.map((x, i) => (
              <pre key={i} class="con-box con-box-bd cv-details text">{JSON.stringify("step" in x ? x.step.payload : x, null, 2)}</pre>
            ))}
          </span>
        )}
      </span>
    </div>
  );
}
