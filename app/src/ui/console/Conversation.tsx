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
import { buildUserEdits } from "../../state/partEdits";
import { editsForRun, weave } from "./userEditLines";

const AT_BOTTOM_PX = 24;
/** Rows rendered at once (the last ones); "show earlier" adds this many more. Same as StepLog. */
export const THREAD_WINDOW = 500;

export function Conversation(): JSX.Element {
  useEffect(() => retainThreadFrames(), []);
  const frame = threadFrame.value;
  const run = effectiveRun.value ?? shownPending.value;
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  /** Rows there were when the user scrolled up: the rest count as new. */
  const seenLen = useRef(0);
  const lastRun = useRef<string | null>(null);
  /** Approval / question cards already brought into view (by row key). */
  const seenCards = useRef<Set<string>>(new Set());
  const keepFromBottom = useRef<number | null>(null);
  const [earlier, setEarlier] = useState<{ runId: string | null; n: number }>({ runId: null, n: 0 });

  const items = frame.items;
  const span = THREAD_WINDOW + (earlier.runId === frame.runId ? earlier.n : 0);
  const start = Math.max(0, items.length - span);
  const shown = start > 0 ? items.slice(start) : items;
  const newCount = atBottom ? 0 : Math.max(0, items.length - seenLen.current);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (lastRun.current !== frame.runId) {
      lastRun.current = frame.runId;
      stick.current = true;
      seenCards.current = new Set();
      if (!atBottom) setAtBottom(true);
    }
    if (keepFromBottom.current !== null) {
      el.scrollTop = el.scrollHeight - keepFromBottom.current;
      keepFromBottom.current = null;
    } else if (stick.current) {
      el.scrollTop = el.scrollHeight;
    }
    // a card that needs the human is never left out of sight: bring each new one into view
    for (const it of items) {
      if ((it.kind !== "approval" && it.kind !== "question") || seenCards.current.has(it.key)) continue;
      seenCards.current.add(it.key);
      if (stick.current) continue;            // already at the bottom, where cards land
      el.querySelector<HTMLElement>(`[data-key="${CSS.escape(it.key)}"]`)?.scrollIntoView?.({ block: "nearest" });
    }
  });

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_BOTTOM_PX;
    if (bottom === stick.current) return;
    stick.current = bottom;
    if (!bottom) seenLen.current = items.length;
    setAtBottom(bottom);
  };

  const toBottom = () => {
    const el = scroller.current;
    stick.current = true;
    setAtBottom(true);
    setEarlier({ runId: null, n: 0 });
    if (el) el.scrollTop = el.scrollHeight;
  };

  const showEarlier = () => {
    const el = scroller.current;
    if (el) keepFromBottom.current = el.scrollHeight - el.scrollTop;
    const extra = span - THREAD_WINDOW;
    setEarlier({ runId: frame.runId, n: extra + THREAD_WINDOW });
  };

  return (
    <div class="cv-wrap">
      <div class="cv-thread" ref={scroller} onScroll={onScroll} role="log" aria-label="Conversation" aria-live="off" data-conversation>
        {start > 0 && <button type="button" class="cv-earlier" onClick={showEarlier}>{`Show earlier (${start})`}</button>}
        {!run && items.length === 0 && <Empty />}
        {weave(shown, editsForRun(buildUserEdits.value, run ?? null)).map((r) => (r.kind === "you"
          ? <div key={r.line.key} class="cv-item" data-key={r.line.key}><YouLine text={r.line.text} /></div>
          : <div key={r.item.key} class="cv-item" data-key={r.item.key}><Item item={r.item} agent={run?.agent ?? "agent"} /></div>))}
        {run && items.length === 0 && run.task && (
          <Item item={{ kind: "user", key: "task", text: run.task, ts: run.created_at, stepIds: [] }} agent={run.agent} />
        )}
        {run && run.status === "queued" && <Queued />}
      </div>
      {newCount > 0 && <button type="button" class="cv-chip-new" onClick={toBottom}>{`${newCount} new ↓`}</button>}
    </div>
  );
}

/** One of your own edits to a part (vars, code, a feature op). */
function YouLine({ text }: { text: string }): JSX.Element {
  return (
    <div class="cv-line cv-you" data-row data-kind="you" data-you-edit>
      <span class="cv-x" aria-hidden="true" />
      <span class="cv-g faint" aria-hidden="true">·</span>
      <span class="cv-body"><span class="text"><span class="cv-you-who">You</span>{` · ${text}`}</span></span>
    </div>
  );
}

function Empty(): JSX.Element {
  return (
    <div class="cv-empty">
      <p class="dim">No runs yet.</p>
      <Button onClick={() => { composerOpen.value = true; }}>New run</Button>
    </div>
  );
}

function Queued(): JSX.Element {
  const p = coreStatus.value?.parallel;
  const why = p ? ` · ${p.auto ? `Auto: ${p.limit} on this Mac` : `Manual: ${p.limit}`}` : "";
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
