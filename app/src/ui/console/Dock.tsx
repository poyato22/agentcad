// The agent dock (P11 F2, on P10 E6): the Conversation on top, full width; View and Simulations
// underneath, side by side, 50/50 — View and Simulations over the whole build's agent tree
// (treeStreams: the lead and every sub-agent), the Conversation the build's one chat (the
// lead's thread, or a sub-agent's opened from its card). Hovering View or Simulations widens it
// to ~90% of the dock (the other keeps ~10%, never 0), smoothly unless reduced motion is asked
// for; leaving the row restores 50/50. Each stream can be closed: the Conversation to a slim bar
// at the top, View or Simulations to a slim vertical tab in the row (both: a bar of two tabs),
// each with its unread count. The dock's left edge drags its width, the bar between the
// Conversation and the row their heights (dockLayout, remembered); dragging never selects page
// text (E10). The composer sits at the foot of the Conversation (of the dock while the
// Conversation is closed). The Conversation header holds "← Back to lead" on a sub-agent's
// thread (E13) and the "needs you" count; the design focus control is gone (P11 F1: the
// classifier and its PATCH stay). There is no ⋯ menu (E22, E24).
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { StreamKind } from "../../api/types";
import { runLabel } from "../../lib/names";
import { finishedSims } from "../../state/activity";
import { activeBuildId } from "../../state/builds";
import { effectiveRun, needsYou } from "../../state/selection";
import { actions, selectedRunId } from "../../state/store";
import { ModelWarning } from "./cards/critique/ModelWarning";
import { BackToLead } from "./BackToLead";
import { CommandLine } from "./command/CommandLine";
import { Conversation } from "./Conversation";
import { buildRuns, effectiveRunId, labelOf, offListRuns, retainThreadFrames, retainTreeFrames, runLabels, threadFrame, treeFrame } from "./derived";
import {
  dockLayout, openRow, openStreams, resizeSplit, resizeWidth, rowGrow, setDockLayout, toggleStream, WIDTH_MAX, WIDTH_MIN,
} from "./dockLayout";
import { dockMax } from "../shell/room";
import { shownPending } from "./queued";
import { retainBuildStudies } from "./buildStudies";
import { SimStream, simExtras, simExtrasRunning } from "./SimStream";
import { simRunning, splitStreams } from "./streams";
import { markSeen, unreadOf } from "./unread";
import { ViewStream } from "./ViewStream";
import "./dock.css";

const TITLE: Record<StreamKind, string> = { view: "View", sim: "Simulations", text: "Conversation" };
/** How long the pointer rests on a half before it widens (fix round 1). */
export const HOVER_INTENT_MS = 120;

export function Dock(): JSX.Element {
  useEffect(() => retainThreadFrames(), []);
  useEffect(() => retainTreeFrames(), []);
  // FW-app I3: the build's other studies and generative runs (Studies page, studies agent, panel)
  useEffect(() => retainBuildStudies(), []);
  const frame = threadFrame.value;
  // the Conversation is the shown run's thread; View and Simulations are the whole build's tree
  const text = useMemo(() => splitStreams(frame.items).text, [frame.items]);
  const tree = treeFrame.value;
  // stable key lists: a stream only marks rows seen when its rows changed (N4)
  const viewKeys = useMemo(() => tree.view.map((e) => e.key), [tree.view]);
  const extras = simExtras.value;
  const simKeys = useMemo(() => [...tree.sim.map((e) => e.key), ...extras.map((r) => `rec/${r.id}`)], [tree.sim, extras]);
  const textKeys = useMemo(() => text.map((i) => i.key), [text]);
  const shownId = effectiveRunId.value;
  // the first fallback (newest running run) becomes an explicit choice once (as in the roster)
  useEffect(() => {
    if (shownId !== null && selectedRunId.peek() === null) actions.selectRun(shownId);
  }, [shownId]);
  const layout = dockLayout.value;
  const stack = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<StreamKind | null>(null);
  const running = tree.sim.filter((e) => simRunning(e.item, finishedSims.value)).length + simExtrasRunning.value;
  // unread rows of View / Simulations are counted per build (they span its runs); the
  // Conversation's per shown run
  const buildKey = `build:${tree.buildId ?? ""}`;
  const open = openStreams(layout);
  const row = openRow(layout);
  const both = row.length === 2;          // hover widens a half only while both are open
  const grow = rowGrow(layout, hover);
  useEffect(() => { if (!both) setHover(null); }, [both]);
  // fix round 1: a short hover intent (a pointer sweeping across the row does not flash), and
  // keyboard focus inside a half widens it too
  const intent = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearIntent = () => { if (intent.current !== null) { clearTimeout(intent.current); intent.current = null; } };
  useEffect(() => clearIntent, []);
  const hoverSoon = (s: StreamKind) => {
    clearIntent();
    intent.current = setTimeout(() => { intent.current = null; setHover(s); }, HOVER_INTENT_MS);
  };
  const simCount = tree.sim.length + extras.length;
  const rowFolded = row.length === 0;
  const split = !layout.collapsed.text && !rowFolded;
  const half = (s: "view" | "sim") => ({
    grow: grow[s], shrunk: both && hover !== null && hover !== s,
    onEnter: both ? () => hoverSoon(s) : undefined, onFocusIn: both ? () => { clearIntent(); setHover(s); } : undefined,
  });
  return (
    <div class="dock" data-dock data-layout="stack" data-cols={open.length} data-text-collapsed={layout.collapsed.text ? "" : undefined}>
      <WidthEdge />
      <div class="dock-stack" ref={stack}>
        <Column stream="text" runId={frame.runId} keys={textKeys} count={null} head={<ConversationHead />} foot={<CommandLine />}
          flex={split ? layout.split : undefined}>
          <Conversation items={text} />
        </Column>
        {split && <SplitBar stack={stack} />}
        <div class="dock-row" data-dock-row data-expand={both && hover ? hover : undefined} data-folded={rowFolded ? "" : undefined}
          style={split ? { flex: `${Math.round((1 - layout.split) * 1000) / 1000} 1 0px` } : undefined}
          onPointerLeave={(e) => {
            clearIntent();
            // keyboard focus still inside the row keeps its half wide
            const row = e.currentTarget as HTMLElement;
            if (!row.contains(document.activeElement)) setHover(null);
          }}
          onFocusOut={(e) => {
            const row = e.currentTarget as HTMLElement;
            if (!row.contains(e.relatedTarget as Node | null) && !row.matches(":hover")) setHover(null);
          }}>
          <Column stream="view" runId={buildKey} keys={viewKeys} count={tree.view.length} {...half("view")}>
            <ViewStream entries={tree.view} />
          </Column>
          <Column stream="sim" runId={buildKey} keys={simKeys} count={simCount} running={running} {...half("sim")}>
            <SimStream entries={tree.sim} />
          </Column>
        </div>
      </div>
      {layout.collapsed.text && <CommandLine />}
    </div>
  );
}

/** One stream, or its slim tab while closed: the Conversation's is a bar at the top, View's and
 *  Simulations' a vertical tab in the row (laid flat when both are closed). */
function Column({ stream, runId, keys, count, running = 0, head, foot, grow, shrunk = false, flex, onEnter, onFocusIn, children }: {
  stream: StreamKind; runId: string | null; keys: readonly string[]; count: number | null; running?: number;
  head?: ComponentChildren; foot?: ComponentChildren; grow?: number; shrunk?: boolean; flex?: number;
  onEnter?: () => void; onFocusIn?: () => void; children: ComponentChildren;
}): JSX.Element {
  const layout = dockLayout.value;
  const collapsed = layout.collapsed[stream];
  const unread = collapsed ? unreadOf(runId, stream, keys) : 0;
  useEffect(() => { if (!collapsed) markSeen(runId, stream, keys); }, [collapsed, runId, keys]);
  const title = TITLE[stream];
  const toggle = () => setDockLayout(toggleStream(dockLayout.peek(), stream));
  if (collapsed) {
    return (
      <button type="button" class="dock-tab" data-stream={stream} data-collapsed="" aria-expanded={false}
        aria-label={`Show ${title.toLowerCase()}${unread > 0 ? ` · ${unread} new` : ""}${running > 0 ? ` · ${running} running` : ""}`}
        title={`Show ${title.toLowerCase()}`} onClick={toggle}>
        <span class="dock-tab-t">{title}</span>
        {running > 0 && <span class="dock-dot" data-running={running} aria-hidden="true" />}
        {unread > 0 && <span class="dock-unread num" data-unread={unread} aria-hidden="true">{unread}</span>}
      </button>
    );
  }
  const last = openStreams(layout).length === 1;
  const style = stream === "text" ? (flex !== undefined ? { flex: `${flex} 1 0px` } : undefined) : { flexGrow: String(grow ?? 1) };
  return (
    <section class="dock-s" data-stream={stream} aria-label={title} style={style} data-grow={grow}
      data-shrunk={shrunk ? "" : undefined} onPointerEnter={onEnter} onFocusIn={onFocusIn}>
      <header class="dock-h">
        <button type="button" class="dock-t" aria-expanded={true} aria-controls={`dock-b-${stream}`} aria-disabled={last ? "true" : undefined}
          onClick={toggle} title={last ? `${title} (the last open stream stays open)` : `Close ${title.toLowerCase()}`}>
          <span class="dock-title">{title}</span>
          {/* a running count replaces the total: "2 running ●" reads at a glance; idle streams show how many */}
          {count !== null && count > 0 && running === 0 && <span class="dock-n num">{count}</span>}
          {running > 0 && (
            <span class="dock-running" data-changing data-running={running} title={`${running} running`}>
              <span class="num">{running}</span> running<span class="dock-dot" aria-hidden="true" />
            </span>
          )}
        </button>
        {head}
      </header>
      <div class="dock-b" id={`dock-b-${stream}`} data-stream-body={stream}>{children}</div>
      {foot}
    </section>
  );
}

/** A drag that never selects text (E10): pointer capture, no default, `body.dock-resizing`
 *  (user-select: none everywhere) until the pointer is released. */
function useDrag<T>(begin: (e: PointerEvent) => T, move: (s: T, e: PointerEvent) => void, end: () => void) {
  const start = useRef<T | null>(null);
  const stop = () => {
    if (start.current === null) return;
    start.current = null;
    document.body.classList.remove("dock-resizing");
    end();
  };
  useEffect(() => () => { document.body.classList.remove("dock-resizing"); }, []);
  return {
    onPointerDown: (e: PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      document.body.classList.add("dock-resizing");
      start.current = begin(e);
    },
    onPointerMove: (e: PointerEvent) => { if (start.current !== null) { e.preventDefault(); move(start.current, e); } },
    onPointerUp: stop,
    onPointerCancel: stop,
    onLostPointerCapture: stop,
  };
}

/** The bar between the Conversation and the row: drag (or ↑ / ↓) to trade their heights. */
function SplitBar({ stack }: { stack: { current: HTMLDivElement | null } }): JSX.Element {
  const total = () => stack.current?.getBoundingClientRect().height || 600;
  const drag = useDrag(
    (e) => ({ y: e.clientY, layout: dockLayout.peek(), total: total() }),
    (s, e) => setDockLayout(resizeSplit(s.layout, e.clientY - s.y, s.total), false),
    () => setDockLayout(dockLayout.peek()),
  );
  return (
    <div class="dock-split" role="separator" aria-orientation="horizontal" tabIndex={0} data-split=""
      aria-label="Resize the conversation and the streams under it" {...drag}
      onKeyDown={(e) => {
        const d = e.key === "ArrowUp" ? -16 : e.key === "ArrowDown" ? 16 : 0;
        if (!d) return;
        e.preventDefault();
        setDockLayout(resizeSplit(dockLayout.peek(), d, total()));
      }} />
  );
}

/** The widest the dock may get now: its cap (the window less the model's room, the gaps and the
 *  Parts surface; ui/shell/room.ts). */
function widthMax(): number {
  const cap = dockMax.peek();
  return Number.isFinite(cap) ? cap : WIDTH_MAX;
}

/** The dock's left edge: drag (or ← / →) to change its width. */
function WidthEdge(): JSX.Element {
  const l = dockLayout.value;
  const drag = useDrag(
    (e) => ({ x: e.clientX, layout: dockLayout.peek(), max: widthMax() }),
    (s, e) => setDockLayout(resizeWidth(s.layout, e.clientX - s.x, s.max), false),
    () => setDockLayout(dockLayout.peek()),
  );
  return (
    <div class="dock-edge" role="separator" aria-orientation="vertical" aria-label="Resize the agent dock" tabIndex={0}
      aria-valuenow={l.width} aria-valuemin={WIDTH_MIN} aria-valuemax={WIDTH_MAX} {...drag}
      onKeyDown={(e) => {
        const d = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
        if (!d) return;
        e.preventDefault();
        setDockLayout(resizeWidth(dockLayout.peek(), d, widthMax()));
      }} />
  );
}

/** The Conversation header's right side: whose thread this is when it isn't the lead's, a blue
 *  count when another run needs you (one click shows it), and the model warning. */
function ConversationHead(): JSX.Element {
  const list = buildRuns.value;
  const shownId = effectiveRunId.value;
  const current = effectiveRun.value ?? shownPending.value ?? (shownId ? offListRuns.value.get(shownId) ?? null : null);
  const { runIds: needs } = needsYou.value;
  const listIds = new Set(list.map((r) => r.id));
  const others = [
    ...list.filter((r) => r.id !== shownId && needs.has(r.id)).map((r) => r.id),
    ...[...needs].filter((id) => id !== shownId && !listIds.has(id)),
  ];
  const label = current ? runLabels.value.get(current.id) ?? runLabel(current, [current]) : null;
  const notLead = current !== null && current.role !== "lead";
  return (
    <div class="dock-hx" data-conversation-head>
      {notLead && <BackToLead buildId={current?.project_id ?? activeBuildId.value} />}
      {notLead && label && <span class="dock-who dim text" data-run-label title={label}>{label}</span>}
      {current && <ModelWarning run={current} />}
      {others.length > 0 && (
        <button type="button" class="dock-needs needs-you" data-needs-you aria-label={`${labelOf(others[0])} needs you · ${others.length} waiting`}
          title={`${others.length} needs you`} onClick={() => actions.selectRun(others[0])}>
          <span class="num">{others.length}</span>
        </button>
      )}
    </div>
  );
}
