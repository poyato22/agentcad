// The agent dock (G1, replacing P11 F2's 50/50 row and its hover-expand): View / Simulations on
// top, ONE at a time, switched by the header's segmented control "View · Simulations" (a colored
// pill slides to the shown one; no slide under reduced motion); the other's tab carries an unread
// count of what arrived for it. The Conversation (the build's one chat: the lead's thread, or a
// sub-agent's opened from its card) sits underneath with the composer at its foot; the
// horizontal divider between them drags (or ↑ / ↓) their heights and is remembered. Either part
// closes to a slim bar (never both; the top area's bar keeps its tabs). View and Simulations span
// the whole build's agent tree (treeStreams). The dock's left edge drags its width; dragging never
// selects page text (E10). The Conversation header holds "← Back to lead" on a sub-agent's thread
// (E13) and the "needs you" count. There is no ⋯ menu (E22, E24).
import type { ComponentChildren, JSX } from "preact";
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
import { buildRuns, effectiveRunId, labelOf, offListRuns, retainThreadFrames, retainTreeFrames, runLabels, threadFrame, treeFrame } from "./derived";
import {
  dockLayout, openStreams, resizeSplit, resizeWidth, setDockLayout, showStream, togglePart, type TopStream, WIDTH_MAX, WIDTH_MIN,
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
  const running = tree.sim.filter((e) => simRunning(e.item, finishedSims.value)).length + simExtrasRunning.value;
  // unread rows of View / Simulations are counted per build (they span its runs); the
  // Conversation's per shown run
  const buildKey = `build:${tree.buildId ?? ""}`;
  const simCount = tree.sim.length + extras.length;
  const split = !layout.collapsed.text && !layout.collapsed.top;
  const streams: Record<TopStream, { keys: readonly string[]; count: number; running: number; body: ComponentChildren }> = {
    view: { keys: viewKeys, count: tree.view.length, running: 0, body: <ViewStream entries={tree.view} /> },
    sim: { keys: simKeys, count: simCount, running, body: <SimStream entries={tree.sim} /> },
  };
  return (
    <div class="dock" data-dock data-layout="stack" data-cols={openStreams(layout).length} data-active={layout.active} data-top-collapsed={layout.collapsed.top ? "" : undefined}
      data-text-collapsed={layout.collapsed.text ? "" : undefined}>
      <WidthEdge />
      <div class="dock-stack" ref={stack}>
        <TopArea runId={buildKey} streams={streams} flex={split ? layout.split : undefined} ready={tree.runIds !== undefined} />
        {split && <SplitBar stack={stack} />}
        <Conversation_ runId={frame.runId} keys={textKeys} flex={split ? 1 - layout.split : undefined}>
          <Conversation items={text} />
        </Conversation_>
      </div>
      {layout.collapsed.text && <CommandLine />}
    </div>
  );
}

/** The header's segmented control: View · Simulations, a pill sliding to the shown one; the
 *  other tab's unread count (and a dot while a study runs). A click shows that stream. */
function Switcher({ runId, streams, active, ready }: {
  runId: string; active: TopStream | null; ready: boolean;
  streams: Record<TopStream, { keys: readonly string[]; count: number; running: number }>;
}): JSX.Element {
  const idx = active === "sim" ? 1 : 0;
  return (
    <div class="dock-seg" role="tablist" aria-label="View or Simulations" data-seg={active ?? "none"}
      style={{ "--seg-i": String(idx) }}>
      {active !== null && <span class="dock-seg-pill" data-seg-pill={active} aria-hidden="true" />}
      {(["view", "sim"] as const).map((s) => {
        const on = s === active;
        const st = streams[s];
        // (only once the build's tree has been read: its first, empty frame is no baseline)
        const unread = on || !ready ? 0 : unreadOf(runId, s, st.keys);
        return (
          <button key={s} type="button" role="tab" class="dock-seg-t" data-stream-tab={s} aria-selected={on}
            aria-controls={`dock-b-${s}`} id={`dock-tab-${s}`}
            aria-label={`${TITLE[s]}${unread > 0 ? ` · ${unread} new` : ""}${st.running > 0 ? ` · ${st.running} running` : ""}`}
            onClick={() => setDockLayout(showStream(dockLayout.peek(), s))}>
            <span class="dock-title">{TITLE[s]}</span>
            {on && st.count > 0 && st.running === 0 && <span class="dock-n num">{st.count}</span>}
            {st.running > 0 && <span class="dock-running" data-changing data-running={st.running} title={`${st.running} running`}>
              {on && <span class="num">{st.running}</span>}<span class="dock-dot" aria-hidden="true" /></span>}
            {unread > 0 && <span class="dock-unread num" data-unread={unread} aria-hidden="true">{unread}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** The top area: the switcher and the shown stream (the other stays mounted, hidden, so its
 *  state and unread count keep up); closed, a slim bar that keeps the switcher. */
function TopArea({ runId, streams, flex, ready }: {
  runId: string; flex?: number; ready: boolean;
  streams: Record<TopStream, { keys: readonly string[]; count: number; running: number; body: ComponentChildren }>;
}): JSX.Element {
  const layout = dockLayout.value;
  const collapsed = layout.collapsed.top;
  const active = layout.active;
  // what the user sees is read: the shown stream's rows are marked seen
  const keys = streams[active].keys;
  useEffect(() => { if (!collapsed) markSeen(runId, active, keys); }, [collapsed, runId, active, keys]);
  const last = layout.collapsed.text;
  const fold = (
    <button type="button" class="dock-fold" aria-expanded={!collapsed} aria-disabled={!collapsed && last ? "true" : undefined}
      aria-label={collapsed ? "Show View and Simulations" : "Close View and Simulations"}
      title={!collapsed && last ? "The last open part stays open" : collapsed ? "Show View and Simulations" : "Close View and Simulations"}
      onClick={() => setDockLayout(togglePart(dockLayout.peek(), "top"))}>
      <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden="true" style={{ transform: collapsed ? "rotate(-90deg)" : undefined }}>
        <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
  );
  if (collapsed) {
    return (
      <div class="dock-tab dock-topbar" data-stream="top" data-collapsed="">
        {fold}
        <Switcher runId={runId} streams={streams} active={null} ready={ready} />
      </div>
    );
  }
  return (
    <section class="dock-s dock-top" data-stream-area="top" aria-label={TITLE[active]}
      style={flex !== undefined ? { flex: `${flex} 1 0px` } : undefined}>
      <header class="dock-h">
        {fold}
        <Switcher runId={runId} streams={streams} active={active} ready={ready} />
      </header>
      {(["view", "sim"] as const).map((s) => (
        <div key={s} class="dock-b" id={`dock-b-${s}`} role="tabpanel" aria-labelledby={`dock-tab-${s}`} data-stream={s} data-stream-body={s}
          hidden={s !== active} data-inactive={s !== active ? "" : undefined}>{streams[s].body}</div>
      ))}
    </section>
  );
}

/** The Conversation, under the top area; closed, a slim bar with its unread count. */
function Conversation_({ runId, keys, flex, children }: {
  runId: string | null; keys: readonly string[]; flex?: number; children: ComponentChildren;
}): JSX.Element {
  const layout = dockLayout.value;
  const collapsed = layout.collapsed.text;
  const unread = collapsed ? unreadOf(runId, "text", keys) : 0;
  useEffect(() => { if (!collapsed) markSeen(runId, "text", keys); }, [collapsed, runId, keys]);
  const toggle = () => setDockLayout(togglePart(dockLayout.peek(), "text"));
  if (collapsed) {
    return (
      <button type="button" class="dock-tab" data-stream="text" data-collapsed="" aria-expanded={false}
        aria-label={`Show conversation${unread > 0 ? ` · ${unread} new` : ""}`} title="Show conversation" onClick={toggle}>
        <span class="dock-tab-t">Conversation</span>
        {unread > 0 && <span class="dock-unread num" data-unread={unread} aria-hidden="true">{unread}</span>}
      </button>
    );
  }
  const last = layout.collapsed.top;
  return (
    <section class="dock-s" data-stream="text" aria-label="Conversation" style={flex !== undefined ? { flex: `${flex} 1 0px` } : undefined}>
      <header class="dock-h">
        <button type="button" class="dock-t" aria-expanded={true} aria-controls="dock-b-text" aria-disabled={last ? "true" : undefined}
          onClick={toggle} title={last ? "Conversation (the last open part stays open)" : "Close conversation"}>
          <span class="dock-title">Conversation</span>
        </button>
        <ConversationHead />
      </header>
      <div class="dock-b" id="dock-b-text" data-stream-body="text">{children}</div>
      <CommandLine />
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

/** G1: the divider between View / Simulations and the Conversation: drag (or ↑ / ↓) to trade
 *  their heights; remembered when the drag ends. */
function SplitBar({ stack }: { stack: { current: HTMLDivElement | null } }): JSX.Element {
  const total = () => stack.current?.getBoundingClientRect().height || 600;
  const drag = useDrag(
    (e) => ({ y: e.clientY, layout: dockLayout.peek(), total: total() }),
    (s, e) => setDockLayout(resizeSplit(s.layout, e.clientY - s.y, s.total), false),
    () => setDockLayout(dockLayout.peek()),
  );
  return (
    <div class="dock-split" role="separator" aria-orientation="horizontal" tabIndex={0} data-split=""
      aria-label="Resize View and Simulations against the conversation" {...drag}
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
