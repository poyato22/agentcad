// The step log. Renders from `logFrame` (rebuilt at most once per animation frame), at most the
// last 500 rows (+ "show earlier"). Follow keeps the view at the bottom; scrolling up turns it
// off, the view then never moves and new rows are counted on the "N new ↓" chip.
// Everything agent- or tool-provided renders as text (no HTML, no Markdown).
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Step } from "../../api/types";
import { fmtClock } from "../../lib/format";
import { actions, follow } from "../../state/store";
import { ApprovalCard } from "./approvals/ApprovalCard";
import { QuestionCard } from "./approvals/QuestionCard";
import { expandedFolds, labelOf, logFrame, retainLogFrames, toggleFold } from "./derived";
import { DiffView } from "./DiffView";
import { createFollow, type FollowController } from "./follow";
import { foldKey, itemKey, type LogItem } from "./logModel";
import { ToolCallRow } from "./ToolCallRow";

export const WINDOW = 500;
const AT_BOTTOM_PX = 4;

/** Render counters (tests, and the e2e volume check via window.__misesConsole). */
export const renderStats = { stepLog: 0 };
if (typeof window !== "undefined" && import.meta.env.MODE !== "production") {
  (window as unknown as { __misesConsole?: typeof renderStats }).__misesConsole = renderStats;
}

export function StepLog(): JSX.Element {
  renderStats.stepLog++;
  useEffect(() => retainLogFrames(), []);
  const frame = logFrame.value;
  const followOn = follow.value;
  const expanded = expandedFolds.value;
  const scroller = useRef<HTMLDivElement>(null);
  const ctl = useRef<FollowController | null>(null);
  if (!ctl.current) ctl.current = createFollow((on) => actions.setFollow(on));
  const fc = ctl.current;
  const [earlier, setEarlier] = useState(0);
  const seen = useRef<{ frame: unknown; key: string; unfolded: number }>({ frame: null, key: "", unfolded: 0 });
  const pinned = useRef<number | null>(null);
  const lastStart = useRef(0);
  const keepFromBottom = useRef<number | null>(null);

  // store → controller (⤓ in Filters, or anything else that sets `follow`)
  if (followOn && !fc.following) fc.resume();
  if (!followOn && fc.following) fc.onScroll(false);

  // count new rows once per frame (a view change is not "new")
  if (seen.current.frame !== frame) {
    if (seen.current.key === frame.key) fc.onAppend(frame.unfolded - seen.current.unfolded);
    seen.current = { frame, key: frame.key, unfolded: frame.unfolded };
  }

  const items = frame.items;
  const span = WINDOW + earlier;
  let start: number;
  if (fc.following) {
    pinned.current = null;
    start = Math.max(0, items.length - span);
  } else {
    if (pinned.current === null) pinned.current = lastStart.current;
    start = Math.min(pinned.current, Math.max(0, items.length - 1));
  }
  const end = Math.min(items.length, start + span);
  lastStart.current = start;
  const shown = items.slice(start, end);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (keepFromBottom.current !== null) {
      el.scrollTop = el.scrollHeight - keepFromBottom.current;
      keepFromBottom.current = null;
    } else if (fc.following) {
      el.scrollTop = el.scrollHeight;
    }
  });

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_BOTTOM_PX;
    if (atBottom === fc.following) return;
    fc.onScroll(atBottom);
  };

  const showEarlier = () => {
    const el = scroller.current;
    if (el) keepFromBottom.current = el.scrollHeight - el.scrollTop;
    if (pinned.current !== null) pinned.current = Math.max(0, pinned.current - WINDOW);
    setEarlier(earlier + WINDOW);
  };

  const resume = () => { fc.resume(); setEarlier(0); };

  return (
    <div class="con-log">
      <div class="con-log-scroll" data-log-scroll ref={scroller} onScroll={onScroll} role="log" aria-live="off">
        {start > 0 && <button type="button" class="con-earlier" onClick={showEarlier}>{`show earlier · ${start}`}</button>}
        {items.length === 0 && <div class="pane-empty">no steps yet</div>}
        {shown.map((it) => <Row key={itemKey(it)} item={it} expanded={expanded} />)}
      </div>
      {!fc.following && fc.pendingNew > 0 && (
        <button type="button" class="con-chip" onClick={resume}>{`${fc.pendingNew} new ↓`}</button>
      )}
    </div>
  );
}

function Row({ item, expanded }: { item: LogItem; expanded: ReadonlySet<string> }): JSX.Element {
  const key = itemKey(item);
  const label = item.runLabel;
  return (
    <div data-row data-kind={item.kind} data-key={key}>
      {item.kind === "call" && expanded.has(item.callId) && (
        <button type="button" class="con-fold con-fold-open" onClick={() => toggleFold(item.callId)}>▾ fold tool calls</button>
      )}
      <RowBody item={item} label={label} />
    </div>
  );
}

function Prefix({ label }: { label?: string }): JSX.Element | null {
  return label ? <span class="faint text">{`${label} · `}</span> : null;
}

function RowBody({ item, label }: { item: LogItem; label?: string }): JSX.Element {
  switch (item.kind) {
    case "message":
      return (
        <div class="con-row">
          <span class="con-ts">{fmtClock(item.step.ts)}</span>
          <span class="con-g faint">▸</span>
          <span class="con-body">
            <Prefix label={label} />
            {item.role === "user" && <span class="faint">you · </span>}
            <span class="ink text">{item.text}</span>
          </span>
        </div>
      );
    case "call":
      return <ToolCallRow item={item} label={label} />;
    case "fold": {
      const names = item.names.slice(0, 3);
      const more = item.names.length - names.length;
      const n = item.calls.length;
      const text = `▸ ${n} more tool call${n === 1 ? "" : "s"}${names.map((x) => ` · ${x}`).join("")}${more > 0 ? ` · +${more}` : ""}`;
      return (
        <button type="button" class="con-fold text" aria-expanded="false" onClick={() => toggleFold(foldKey(item))}>
          {label ? `${label} · ${text}` : text}
        </button>
      );
    }
    case "diff":
      return <div class="con-indent"><DiffView item={item} /></div>;
    case "error":
      return (
        <div class="con-row">
          <span class="con-ts">{fmtClock(item.step.ts)}</span>
          <span class="con-g con-g-fail">✕</span>
          <span class="con-body">
            <Prefix label={label} />
            <span class="ink text">{item.code}</span>
            {item.message && <span class="dim text">{` · ${item.message}`}</span>}
            {item.hint && <span class="faint text con-hint">{item.hint}</span>}
          </span>
        </div>
      );
    case "decision":
      return (
        <div class="con-row">
          <span class="con-ts">{fmtClock(item.step.ts)}</span>
          <span class="con-g faint">▸</span>
          <span class="con-body"><Prefix label={label} /><DecisionText step={item.step} /></span>
        </div>
      );
    case "note":
      return (
        <div class="con-row">
          <span class="con-ts">{fmtClock(item.ts)}</span>
          <span class="con-g faint">·</span>
          <span class="con-body faint text"><Prefix label={label} />{item.text}</span>
        </div>
      );
    case "approval":
      return <ApprovalCard approval={item.approval} runLabel={labelOf(item.approval.run_id)} />;
    case "question":
      return <QuestionCard question={item.question} runLabel={labelOf(item.question.run_id)} />;
    case "live":
      return (
        <div class="con-row" data-live>
          <span class="con-ts" />
          <span class="con-g con-g-live">▸</span>
          <span class="con-body"><Prefix label={label} /><span class="ink text">{item.text}</span><span class="con-caret">▍</span></span>
        </div>
      );
  }
}

function DecisionText({ step }: { step: Step }): JSX.Element {
  const p = step.payload;
  if (step.kind === "plan") {
    const entries = Array.isArray(p.entries) ? p.entries : [];
    const text = entries.map((e) => (typeof e === "object" && e !== null && typeof (e as Record<string, unknown>).content === "string"
      ? (e as Record<string, unknown>).content as string : JSON.stringify(e))).join(" · ");
    return <><span class="ink">Plan</span><span class="dim text">{text ? ` · ${text}` : ""}</span></>;
  }
  const skipped = Array.isArray(p.skipped_tools) ? p.skipped_tools.filter((x): x is string => typeof x === "string") : [];
  const reason = typeof p.reason === "string" ? p.reason : "";
  const out = p.output as Record<string, unknown> | undefined;
  const summary = out && typeof out.summary === "string" ? out.summary : "";
  const parts = [skipped.length ? `skipped ${skipped.join(", ")}` : "", reason, summary].filter(Boolean);
  return <><span class="ink">Decision</span><span class="dim text">{parts.length ? ` · ${parts.join(" · ")}` : ""}</span></>;
}
