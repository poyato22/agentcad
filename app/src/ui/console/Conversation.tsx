// The Conversation stream of the agent dock (Dock.tsx passes the text rows; views and studies have
// their own streams): the shown run as a thread — your messages, the agent's replies, and one
// compact line per thing it did ("measured mount plate · 8.0 cm³") with ▸ for the raw tool
// input/output. Approval and question cards sit inline (the same cards as the Details view);
// failed calls and errors are always their own red line with the hint. Everything agent- or
// tool-provided renders as text. The view sticks to the bottom unless the user scrolled up.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { effectiveRun } from "../../state/selection";
import { coreStatus } from "../../state/store";
import { ApprovalCard } from "./approvals/ApprovalCard";
import { QuestionCard } from "./approvals/QuestionCard";
import { labelOf, retainThreadFrames, threadFrame, toggleFold } from "./derived";
import { DiffView } from "./DiffView";
import { shownPending } from "./queued";
import { CallDetails } from "./ToolCallRow";
import type { ThreadItem } from "./threadModel";
import { editedKeys, outbox, outboxDrop, pendingFor } from "./chat";
import { isApiErrorText } from "./errorText";
import { CancelQueuedButton, cancelledQueued, EditButton, ErrorCard } from "./MessageActions";
import { Markup } from "../markup/render";
import { CopyButton } from "./CopyButton";
import { RunStatus } from "./RunStatus";
import { StudyCardSlot } from "./studycard";
import { PartCardSlot } from "./cards/partcard";
import { G4Card } from "./cards/critique";
import { buildUserEdits } from "../../state/partEdits";
import { editsForRun, weave } from "./userEditLines";
import { FeedbackNote } from "../viewer/FeedbackNote";
import { AnnouncementCard } from "./AnnouncementCard";
import { StudyGrid } from "./studycard/StudyGrid";
import { AssemblyAnimationChip } from "./AssemblyAnimationChip";
import { TopoptCardSlot } from "../topopt/TopoptCard";
import { activeBuildId } from "../../state/builds";
import { ChatImages } from "./ChatImages";
import { EffortChip } from "./think/EffortChip";
import { UsageLine } from "../usage/UsageLine";
import { runUsage } from "../../state/usage";
import { placeTurns, turnFor } from "../../lib/usage";

const AT_BOTTOM_PX = 24;
/** Rows rendered at once (the last ones); "show earlier" adds this many more. Same as StepLog. */
export const THREAD_WINDOW = 500;

/** `items`: the thread rows to show (the dock passes the Conversation stream; default: all). */
export function Conversation({ items: given }: { items?: ThreadItem[] } = {}): JSX.Element {
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

  const items = given ?? frame.items;
  const span = THREAD_WINDOW + (earlier.runId === frame.runId ? earlier.n : 0);
  const start = Math.max(0, items.length - span);
  const shown = start > 0 ? items.slice(start) : items;
  const newCount = atBottom ? 0 : Math.max(0, items.length - seenLen.current);
  // your messages show at once: the outbox's, until the thread has them (or sending failed)
  const seenUser = items.flatMap((i) => (i.kind === "user" || i.kind === "queued" ? [{ text: i.text, ts: i.ts }] : []));
  if (run?.task) seenUser.push({ text: run.task, ts: run.created_at });
  const { show: pending, done: echoed } = given ? { show: [], done: [] }
    : pendingFor(outbox.value, run?.project_id ?? activeBuildId.value, run?.id ?? null, seenUser);
  useEffect(() => { for (const id of echoed) outboxDrop(id); }, [echoed.join(",")]);
  const usage = run ? runUsage.value.get(run.id) : undefined;
  const turnAt = usage && run ? placeTurns(shown, usage.turns) : null;

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
        {!run && items.length === 0 && pending.length === 0 && <Empty />}
        {weave(shown, editsForRun(buildUserEdits.value, run ?? null)).map((r) => (r.kind === "you"
          ? <div key={r.line.key} class="cv-item" data-key={r.line.key}><YouLine text={r.line.text} /></div>
          : (
            <div key={r.item.key} class="cv-item" data-key={r.item.key}>
              <Item item={r.item} agent={run?.agent ?? "agent"} runId={run?.id ?? null} run={run ?? null}
                stopped={r.item.kind === "error" && stoppedAt(items, r.item.key, run?.status ?? null)} />
              {run && turnAt?.has(r.item.key) && <TurnUsageLine turn={turnFor(turnAt.get(r.item.key)!, run.id)} />}
            </div>
          )))}
        {run && items.length === 0 && run.task && (
          <Item item={{ kind: "user", key: "task", text: run.task, ts: run.created_at, stepIds: [] }} agent={run.agent} />
        )}
        {pending.map((o) => (
          <div key={`outbox-${o.id}`} class="cv-item" data-key={`outbox-${o.id}`}>
            <div class="cv-msg cv-user is-sending" data-msg="user" data-row data-kind="message" data-sending="">
              <div class="cv-msg-head"><span class="label">you</span><span class="faint">sending…</span></div>
              <div class="cv-text text">{o.text}</div>
            </div>
          </div>
        ))}
        {run && run.status === "queued" && <Queued />}
        {run && run.status !== "queued" && <RunStatus runId={run.id} status={run.status} />}
      </div>
      {newCount > 0 && <button type="button" class="cv-chip-new" onClick={toBottom}>{`${newCount} new ↓`}</button>}
      <AssemblyAnimationChip />
    </div>
  );
}

function TurnUsageLine({ turn }: { turn: ReturnType<typeof turnFor> }): JSX.Element | null {
  return turn ? <UsageLine turn={turn} /> : null;
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
    <div class="cv-empty" data-chat-empty>
      <p class="dim">No messages yet. Describe what you want to build below: your first message starts this build's agent.</p>
    </div>
  );
}

/** Continue is offered only on an error the run did not get past (review fix 1): the error is the
 *  thread's last item (notes aside), or the run ended failed / cancelled and this is its last
 *  error with nothing of yours after it. An error the agent recovered from (a reply followed)
 *  never offers it, so a finished turn is never carried on twice. */
export function stoppedAt(items: readonly ThreadItem[], key: string, status: string | null = null): boolean {
  const at = items.findIndex((i) => i.key === key);
  if (at < 0) return false;
  const after = items.slice(at + 1);
  if (after.some((i) => i.kind === "user" || i.kind === "queued")) return false;
  const last = after.every((i) => i.kind === "note");
  const ended = (status === "failed" || status === "cancelled") && !after.some((i) => i.kind === "error");
  return last || ended;
}

function Queued(): JSX.Element {
  const p = coreStatus.value?.parallel;
  const why = p ? ` · ${p.auto ? `Auto: ${p.limit} on this Mac` : `Manual: ${p.limit}`}` : "";
  return <div class="cv-note faint" data-queued>{`waiting for a free agent slot${why}`}</div>;
}

/** `run` / `stopped` (the dock's Conversation): an error row the run did not get past offers
 *  Continue. */
export function Item({ item, agent, runId = null, run = null, stopped = false }: {
  item: ThreadItem; agent: AgentRun["agent"]; runId?: string | null; run?: AgentRun | null; stopped?: boolean;
}): JSX.Element {
  switch (item.kind) {
    case "user": {
      // a comment pinned on the model (U2a): the words, then the pin, never the raw location block
      const fb = item.feedback ?? null;
      // run-scoped: the same step id never collides across runs
      const rowKey = `${runId ?? ""}/${item.key}`;
      const edited = editedKeys.value.has(rowKey);
      return (
        <div class={`cv-msg cv-user${edited ? " is-edited" : ""}`} data-msg="user" data-row data-kind="message" data-edited={edited ? "" : undefined}>
          <div class="cv-msg-head">
            <span class="label">you</span>
            {item.effort && <EffortChip effort={item.effort} />}
            {edited && <span class="cv-edited faint">edited · the new version is below</span>}
            {!fb && item.key !== "task" && item.text.trim() !== "" && <EditButton text={item.text} rowKey={rowKey} />}
          </div>
          {fb ? <FeedbackNote feedback={fb} runId={runId} /> : <div class="cv-text text">{item.text}</div>}
          {item.images && <ChatImages images={item.images} buildId={run?.project_id ?? activeBuildId.value} />}
        </div>
      );
    }
    case "agent":
      return (
        <div class="cv-msg cv-agent" data-msg="agent" data-row data-kind={item.live ? "live" : "message"} data-changing={item.live ? "" : undefined}>
          <div class="cv-msg-head">
            <span class="label">{agent}</span>
            {!item.live && item.text.trim() !== "" && <CopyButton text={item.text} />}
          </div>
          {item.live
            // streaming text is not yet a finished reply: show it plain with a caret, then render
            // the landed message as markup once the step arrives
            ? <div class="cv-text text" data-live-text>{item.text}<span class="cv-caret" aria-hidden="true">▍</span></div>
            // Claude Code's API error, echoed as a reply (P10 E3): plain text, never markup
            : isApiErrorText(item.text)
              ? <div class="cv-text text cv-fail" data-plain-error>{item.text}</div>
              : <div class="cv-text"><Markup source={item.text} /></div>}
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
      return <ErrorCard item={item} stopped={stopped} run={run} />;
    case "note":
      return <div class="cv-note faint text" data-row data-kind="note">{item.text}</div>;
    case "approval":
      return <div class="cv-card" data-row data-kind="approval"><ApprovalCard approval={item.approval} runLabel={labelOf(item.approval.run_id)} /></div>;
    case "question":
      return (
        <div class="cv-card" data-row data-kind="question">
          <QuestionCard question={item.question} runLabel={labelOf(item.question.run_id)} />
        </div>
      );
    case "critique": case "view":
      return <G4Card item={item} />;
    case "announce":
      return <AnnouncementCard announcement={item.announcement} />;
    case "queued":
      return <QueuedLine item={item} runId={runId} />;
    case "studies":
      return <StudyGrid calls={item.calls} />;
  }
}

/** A message sent while the lead was busy (I2): your words at once, with a chip that reads
 *  "queued" until the lead reads them, then "delivered" (or "not delivered" and why). */
function QueuedLine({ item, runId }: { item: Extract<ThreadItem, { kind: "queued" }>; runId: string | null }): JSX.Element {
  // taken back (Cancel) but not yet settled by the core's "dropped": shown as cancelled at once
  const status = item.status === "queued" && cancelledQueued.value.has(item.messageId) ? "dropped" : item.status;
  const reason = status !== item.status ? "cancelled" : item.reason;
  const chip = status === "delivered" ? "delivered"
    : status === "dropped" ? (reason === "cancelled" ? "cancelled" : `not delivered · ${reason ?? "dropped"}`)
    : `queued${item.position && item.position > 1 ? ` · ${item.position} in line` : ""} · delivered when the agent finishes this step`;
  const rowKey = `${runId ?? ""}/${item.key}`;
  const edited = editedKeys.value.has(rowKey);
  return (
    <div class={`cv-msg cv-user cv-queued is-${status}${edited ? " is-edited" : ""}`} data-msg="user" data-row data-kind="queued" data-queued-message={item.messageId}
      data-queued-status={status} data-edited={edited ? "" : undefined}>
      <div class="cv-msg-head">
        <span class="label">you</span>
        <span class={`cv-queued-chip is-${status}`} data-queued-chip role="status">{chip}</span>
        {item.effort && <EffortChip effort={item.effort} />}
        {status === "queued" && runId && <CancelQueuedButton runId={runId} messageId={item.messageId} />}
        {edited && <span class="cv-edited faint">edited · the new version is below</span>}
        {status !== "queued" && item.text.trim() !== "" && <EditButton text={item.text} rowKey={rowKey} />}
      </div>
      <div class="cv-text text">{item.text}</div>
    </div>
  );
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
        {item.call && <StudyCardSlot call={item.call} />}
        {item.call && <PartCardSlot call={item.call} />}
        {item.call && <TopoptCardSlot call={item.call} />}
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
