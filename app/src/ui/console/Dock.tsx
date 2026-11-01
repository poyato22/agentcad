// The agent dock (P10 E6): View, Simulations and Conversation side by side as columns — View and
// Simulations over the whole build's agent tree (treeStreams: the lead and every sub-agent), the
// Conversation the build's one chat (the lead's thread, or a sub-agent's opened from its card).
// Any combination can be open; a closed column is a slim vertical tab with its unread count.
// Open columns share the dock's width (drag the dividers between them); the dock grows with each
// open column and its left edge drags its width (dockLayout, remembered). Dragging an edge or a
// divider never selects page text (E10). The composer sits at the foot of the Conversation (of
// the dock while the Conversation is closed). The Conversation header holds the design focus,
// "← Back to lead" on a sub-agent's thread (E13) and the "needs you" count. There is no ⋯ menu:
// New run is gone (E24) and the full log and agent browser are in Profile → Developer (E22).
import { Fragment, type ComponentChildren, type JSX } from "preact";
import { useEffect, useMemo, useRef } from "preact/hooks";
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
import { DesignFocus } from "./DesignFocus";
import { buildRuns, effectiveRunId, labelOf, offListRuns, retainThreadFrames, retainTreeFrames, runLabels, threadFrame, treeFrame } from "./derived";
import {
  dockLayout, minWidth, openStreams, resizeColumns, resizeWidth, setDockLayout, STREAMS, TAB_W, toggleStream, WIDTH_MAX,
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

export function Dock(): JSX.Element {
  useEffect(() => retainThreadFrames(), []);
  useEffect(() => retainTreeFrames(), []);
  // FW-app I3: the build's other studies and generative runs (Studies page, studies agent, panel)
  useEffect(() => retainBuildStudies(), []);
  const frame = threadFrame.value;
  // the Conversation is the shown run's thread; View and Simulations are the whole build's tree
  const text = useMemo(() => splitStreams(frame.items).text, [frame.items]);
  const tree = treeFrame.value;
  // stable key lists: a column only marks rows seen when its rows changed (N4)
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
  const cols = useRef<HTMLDivElement>(null);
  const running = tree.sim.filter((e) => simRunning(e.item, finishedSims.value)).length + simExtrasRunning.value;
  // unread rows of View / Simulations are counted per build (they span its runs); the
  // Conversation's per shown run
  const buildKey = `build:${tree.buildId ?? ""}`;
  const open = openStreams(layout);
  const simCount = tree.sim.length + extras.length;

  const column = (s: StreamKind): JSX.Element => {
    switch (s) {
      case "view":
        return <Column stream="view" runId={buildKey} keys={viewKeys} count={tree.view.length}><ViewStream entries={tree.view} /></Column>;
      case "sim":
        return <Column stream="sim" runId={buildKey} keys={simKeys} count={simCount} running={running}><SimStream entries={tree.sim} /></Column>;
      case "text":
        return (
          <Column stream="text" runId={frame.runId} keys={textKeys} count={null} head={<ConversationHead />} foot={<CommandLine />}>
            <Conversation items={text} />
          </Column>
        );
    }
  };
  return (
    <div class="dock" data-dock data-cols={open.length} data-text-collapsed={layout.collapsed.text ? "" : undefined}>
      <WidthEdge />
      <div class="dock-cols" ref={cols}>
        {STREAMS.map((s) => {
          const next = open[open.indexOf(s) + 1];
          return (
            <Fragment key={s}>
              {column(s)}
              {!layout.collapsed[s] && next && <ColumnDivider a={s} b={next} cols={cols} />}
            </Fragment>
          );
        })}
      </div>
      {layout.collapsed.text && <CommandLine />}
    </div>
  );
}

/** One column, or its slim tab while closed. */
function Column({ stream, runId, keys, count, running = 0, head, foot, children }: {
  stream: StreamKind; runId: string | null; keys: readonly string[]; count: number | null; running?: number;
  head?: ComponentChildren; foot?: ComponentChildren; children: ComponentChildren;
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
  const share = layout.share[stream];
  return (
    <section class="dock-s" data-stream={stream} aria-label={title} style={{ flex: `${share} 1 0px` }}>
      <header class="dock-h">
        <button type="button" class="dock-t" aria-expanded={true} aria-controls={`dock-b-${stream}`} aria-disabled={last ? "true" : undefined}
          onClick={toggle} title={last ? `${title} (the last open column stays open)` : `Close ${title.toLowerCase()}`}>
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

/** The divider between two open columns: drag (or ← / →) to move width from one to the other. */
function ColumnDivider({ a, b, cols }: { a: StreamKind; b: StreamKind; cols: { current: HTMLDivElement | null } }): JSX.Element {
  // the open columns' width now (0 before layout, in tests: the layout's own width)
  const total = () => {
    const el = cols.current;
    const sum = el ? [...el.querySelectorAll<HTMLElement>(".dock-s")].reduce((t, c) => t + c.getBoundingClientRect().width, 0) : 0;
    const l = dockLayout.peek();
    return sum > 0 ? sum : l.widths[openStreams(l).length - 1];
  };
  const drag = useDrag(
    (e) => ({ x: e.clientX, layout: dockLayout.peek(), total: total() }),
    (s, e) => setDockLayout(resizeColumns(s.layout, a, b, e.clientX - s.x, s.total), false),
    () => setDockLayout(dockLayout.peek()),
  );
  return (
    <div class="dock-div" role="separator" aria-orientation="vertical" tabIndex={0} data-divider={`${a}|${b}`}
      aria-label={`Resize ${TITLE[a].toLowerCase()} and ${TITLE[b].toLowerCase()}`} {...drag}
      onKeyDown={(e) => {
        const d = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
        if (!d) return;
        e.preventDefault();
        setDockLayout(resizeColumns(dockLayout.peek(), a, b, d, total()));
      }} />
  );
}

/** The widest the open columns may get now: the dock's cap (the window less the model's room, the
 *  gaps and the Parts surface; ui/shell/room.ts) less the closed columns' tabs. */
function widthMax(): number {
  const cap = dockMax.peek();
  const l = dockLayout.peek();
  return Number.isFinite(cap) ? cap - (STREAMS.length - openStreams(l).length) * TAB_W : WIDTH_MAX;
}

/** The dock's left edge: drag (or ← / →) to change its width for this many open columns. */
function WidthEdge(): JSX.Element {
  const l = dockLayout.value;
  const n = openStreams(l).length;
  const drag = useDrag(
    (e) => ({ x: e.clientX, layout: dockLayout.peek(), max: widthMax() }),
    (s, e) => setDockLayout(resizeWidth(s.layout, e.clientX - s.x, s.max), false),
    () => setDockLayout(dockLayout.peek()),
  );
  return (
    <div class="dock-edge" role="separator" aria-orientation="vertical" aria-label="Resize the agent dock" tabIndex={0}
      aria-valuenow={l.widths[n - 1]} aria-valuemin={minWidth(n)} aria-valuemax={WIDTH_MAX} {...drag}
      onKeyDown={(e) => {
        const d = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
        if (!d) return;
        e.preventDefault();
        setDockLayout(resizeWidth(dockLayout.peek(), d, widthMax()));
      }} />
  );
}

/** The Conversation header's right side: whose thread this is when it isn't the lead's, a blue
 *  count when another run needs you (one click shows it), the model warning, and the ⋯ menu. */
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
      <DesignFocus run={current} buildId={current?.project_id ?? activeBuildId.value} />
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
