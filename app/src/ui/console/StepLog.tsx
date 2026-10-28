// The step log. Renders from `logFrame` (rebuilt at most once per animation frame), at most the
// last 500 rows (+ "show earlier"). Follow keeps the view at the bottom; scrolling up turns it
// off, the view then never moves and new rows are counted on the "N new ↓" chip.
// Everything agent- or tool-provided renders as text (no HTML, no Markdown).
// A focus request (focusedStepId: an evidence link, a History row) switches to that step's run,
// opens its fold, turns follow off, scrolls the row into view and highlights it for ~2 s.
import { batch } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Step } from "../../api/types";
import { fmtClock } from "../../lib/format";
import { actions, filter, focusedStepId, follow, steps } from "../../state/store";
import { ApprovalCard } from "./approvals/ApprovalCard";
import { QuestionCard } from "./approvals/QuestionCard";
import { expandedFolds, labelOf, logFrame, retainLogFrames, toggleFold } from "./derived";
import { DiffView } from "./DiffView";
import { createFollow, type FollowController } from "./follow";
import { callFoldKey, findStepRow, foldKey, itemKey, type LogItem } from "./logModel";
import { ToolCallRow } from "./ToolCallRow";

export const WINDOW = 500;
const AT_BOTTOM_PX = 4;
export const FOCUS_HIGHLIGHT_MS = 2000;
/** Renders a focus request may wait for its row (frame rebuilds, a fold opening) before giving up. */
const FOCUS_MAX_TRIES = 20;

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
  // "show earlier" belongs to one view (scope · filter · run): another view starts at the last 500
  const [earlierFor, setEarlierFor] = useState<{ key: string; n: number }>({ key: "", n: 0 });
  const earlier = earlierFor.key === frame.key ? earlierFor.n : 0;
  const seen = useRef<{ frame: unknown; key: string; unfolded: number }>({ frame: null, key: "", unfolded: 0 });
  const pinned = useRef<number | null>(null);
  const lastStart = useRef(0);
  const keepFromBottom = useRef<number | null>(null);
  const focusReq = useRef<{ stepId: number; runId: string; tries: number } | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [, rerender] = useState(0);
  const requested = focusedStepId.value;

  // take a focus request: switch to its run's log, follow off; the row is found once the frame is rebuilt
  useEffect(() => {
    if (requested === null) return;
    focusedStepId.value = null;
    let runId: string | null = null;
    for (const [rid, list] of steps.peek()) if (list.some((x) => x.id === requested)) { runId = rid; break; }
    if (runId === null) return;
    focusReq.current = { stepId: requested, runId, tries: 0 };
    const rid = runId;
    batch(() => {
      actions.selectRun(rid);
      const f = filter.peek();
      if (f.scope !== "run") actions.setFilter({ ...f, scope: "run" });
      actions.setFollow(false);
    });
    rerender((n) => n + 1);
  }, [requested]);

  useEffect(() => {
    if (highlight === null) return;
    const t = setTimeout(() => setHighlight(null), FOCUS_HIGHLIGHT_MS);
    return () => clearTimeout(t);
  }, [highlight]);

  // store → controller (⤓ in Filters, or anything else that sets `follow`)
  if (followOn && !fc.following) fc.resume();
  if (!followOn && fc.following) fc.onScroll(false);

  // count new rows once per frame (a view change is not "new", and unpins the window)
  if (seen.current.frame !== frame) {
    if (seen.current.key === frame.key) fc.onAppend(frame.unfolded - seen.current.unfolded);
    else { pinned.current = null; lastStart.current = 0; }
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
    const req = focusReq.current;
    if (!req) return;
    const kind = filter.peek().kind;
    if (frame.key !== `run|${kind}|${req.runId}`) return; // the frame for that run isn't built yet
    if (++req.tries > FOCUS_MAX_TRIES) { focusReq.current = null; return; }
    const loc = findStepRow(items, req.stepId, steps.peek().get(req.runId) ?? []);
    if (!loc) {
      // hidden by the kind filter: show everything; otherwise the step has no row
      if (kind !== "everything") actions.setFilter({ scope: "run", kind: "everything" });
      else focusReq.current = null;
      return;
    }
    if (loc.unfold) {
      if (!expandedFolds.peek().has(loc.unfold)) toggleFold(loc.unfold);
      return;
    }
    if (loc.index < start || loc.index >= end) {
      pinned.current = Math.max(0, loc.index - Math.floor(WINDOW / 2));
      rerender((n) => n + 1);
      return;
    }
    focusReq.current = null;
    const row = [...(scroller.current?.querySelectorAll<HTMLElement>("[data-row]") ?? [])].find((r) => r.getAttribute("data-key") === loc.key);
    row?.scrollIntoView?.({ block: "center" });
    setHighlight(loc.key);
  });

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
    setEarlierFor({ key: frame.key, n: earlier + WINDOW });
  };

  const resume = () => { fc.resume(); setEarlierFor({ key: "", n: 0 }); };

  return (
    <div class="con-log">
      <div class="con-log-scroll" data-log-scroll ref={scroller} onScroll={onScroll} role="log" aria-live="off">
        {start > 0 && <button type="button" class="con-earlier" onClick={showEarlier}>{`Show earlier (${start})`}</button>}
        {items.length === 0 && <div class="pane-empty">no steps yet</div>}
        {shown.map((it) => <Row key={itemKey(it)} item={it} expanded={expanded} focused={highlight === itemKey(it)} />)}
      </div>
      {!fc.following && fc.pendingNew > 0 && (
        <button type="button" class="con-chip" onClick={resume}>{`${fc.pendingNew} new ↓`}</button>
      )}
    </div>
  );
}

function Row({ item, expanded, focused }: { item: LogItem; expanded: ReadonlySet<string>; focused: boolean }): JSX.Element {
  const key = itemKey(item);
  const label = item.runLabel;
  return (
    <div data-row data-kind={item.kind} data-key={key} data-focused={focused ? "" : undefined} class={focused ? "con-focused" : undefined}>
      {item.kind === "call" && expanded.has(callFoldKey(item)) && (
        <button type="button" class="con-fold con-fold-open" onClick={() => toggleFold(callFoldKey(item))}>▾ Fold tool calls</button>
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
    case "file":
      return (
        <div class="con-row" data-file>
          <span class="con-ts">{fmtClock(item.step.ts)}</span>
          <span class="con-g faint">◆</span>
          <span class="con-body">
            <Prefix label={label} />
            <span class="faint">file · </span><span class="ink text">{item.path}</span>
            {" "}<span class="con-add">{`+${item.added}`}</span>{" "}<span class="con-del">{`−${item.removed}`}</span>
          </span>
        </div>
      );
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
        <div class="con-row" data-live data-changing>
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
  if (p.acp === "session_started") {
    // acp_host/session.py: {acp, agent, session_id, protocol_version, agent_info}
    const parts = [typeof p.agent === "string" ? p.agent : "", typeof p.protocol_version === "number" ? `protocol ${p.protocol_version}` : ""].filter(Boolean);
    return <><span class="ink">ACP session started</span><span class="dim text">{parts.length ? ` · ${parts.join(" · ")}` : ""}</span></>;
  }
  if (p.acp === "permission") {
    // acp_host/session.py: {acp, approval_id, status, tool_call_id, option_id}
    const status = typeof p.status === "string" ? p.status : "decided";
    const option = typeof p.option_id === "string" ? p.option_id : "no option (cancelled)";
    return <><span class="ink">{`permission ${status}`}</span><span class="dim text">{` · ${option}`}</span></>;
  }
  const skipped = Array.isArray(p.skipped_tools) ? p.skipped_tools.filter((x): x is string => typeof x === "string") : [];
  const reason = typeof p.reason === "string" ? p.reason : "";
  const out = p.output as Record<string, unknown> | undefined;
  const summary = out && typeof out.summary === "string" ? out.summary : "";
  const parts = [skipped.length ? `skipped ${skipped.join(", ")}` : "", reason, summary].filter(Boolean);
  return <><span class="ink">Decision</span><span class="dim text">{parts.length ? ` · ${parts.join(" · ")}` : ""}</span></>;
}
