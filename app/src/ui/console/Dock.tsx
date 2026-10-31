// The agent dock (Plan 9 D2 / D4): three streams stacked and visible at once — View, Simulations,
// Conversation — over the shown run's thread (streams.ts splits it). Each scrolls on its own and
// collapses to its header (with an unread count while collapsed); the dividers between them drag
// to resize, and the dock's left edge drags its width (dockLayout, remembered). An empty stream
// shrinks to one quiet line. The composer sits at the foot. There is no button bar: the
// Conversation header holds the "needs you" count and one ⋯ menu (new run, agent browser, full log).
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { StreamKind } from "../../api/types";
import { runLabel } from "../../lib/names";
import { finishedSims } from "../../state/activity";
import { effectiveRun, needsYou } from "../../state/selection";
import { actions, selectedRunId } from "../../state/store";
import { SpecPanel } from "../spec";
import { ModelWarning } from "./cards/critique/ModelWarning";
import { CommandLine } from "./command/CommandLine";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { buildRuns, effectiveRunId, labelOf, offListRuns, retainThreadFrames, runLabels, threadFrame } from "./derived";
import { dockLayout, resizeStream, resizeWidth, setDockLayout, toggleStream } from "./dockLayout";
import { shownPending } from "./queued";
import { SimStream } from "./SimStream";
import { simRunning, splitStreams } from "./streams";
import { markSeen, unreadOf } from "./unread";
import { composerOpen, setConsoleView } from "./view";
import { ViewStream } from "./ViewStream";
import "./dock.css";

const TITLE: Record<StreamKind, string> = { view: "View", sim: "Simulations", text: "Conversation" };

export function Dock(): JSX.Element {
  useEffect(() => retainThreadFrames(), []);
  const frame = threadFrame.value;
  const streams = useMemo(() => splitStreams(frame.items), [frame.items]);
  const run = effectiveRun.value ?? shownPending.value;
  const shownId = effectiveRunId.value;
  // the first fallback (newest running run) becomes an explicit choice once (as in the roster)
  useEffect(() => {
    if (shownId !== null && selectedRunId.peek() === null) actions.selectRun(shownId);
  }, [shownId]);
  const layout = dockLayout.value;
  const root = useRef<HTMLDivElement>(null);
  const agent = run?.agent ?? "agent";
  const runId = run?.id ?? null;
  const running = streams.sim.filter((i) => simRunning(i, finishedSims.value)).length;

  const open = (s: StreamKind) => !layout.collapsed[s];
  const filled = (s: StreamKind) => open(s) && streams[s].length > 0;
  return (
    <div class="dock" ref={root} data-dock data-text-collapsed={layout.collapsed.text ? "" : undefined}>
      <WidthEdge />
      <Section stream="view" runId={frame.runId} keys={streams.view.map((i) => i.key)} count={streams.view.length}
        height={filled("view") ? layout.view : null}>
        <ViewStream items={streams.view} agent={agent} runId={runId} />
      </Section>
      {filled("view") && <Divider which="view" root={root} />}
      <Section stream="sim" runId={frame.runId} keys={streams.sim.map((i) => i.key)} count={streams.sim.length} running={running}
        height={filled("sim") ? layout.sim : null}>
        <SimStream items={streams.sim} agent={agent} runId={runId} />
      </Section>
      {filled("sim") && open("text") && <Divider which="sim" root={root} />}
      <Section stream="text" runId={frame.runId} keys={streams.text.map((i) => i.key)} count={null} fill head={<ConversationHead />}>
        <SpecPanel />
        <Conversation items={streams.text} />
      </Section>
      {composerOpen.value ? <Composer /> : <CommandLine />}
    </div>
  );
}

function Section({ stream, runId, keys, count, running = 0, height = null, fill = false, head, children }: {
  stream: StreamKind; runId: string | null; keys: readonly string[]; count: number | null; running?: number;
  /** Fixed height (px) of a filled stream; null: as tall as its content (empty) or flexible (fill). */
  height?: number | null; fill?: boolean; head?: ComponentChildren; children: ComponentChildren;
}): JSX.Element {
  const collapsed = dockLayout.value.collapsed[stream];
  const unread = collapsed ? unreadOf(runId, stream, keys) : 0;
  useEffect(() => { if (!collapsed) markSeen(runId, stream, keys); });
  const title = TITLE[stream];
  const style = !collapsed && height !== null ? { height: `${height}px` } : undefined;
  return (
    <section class="dock-s" data-stream={stream} data-collapsed={collapsed ? "" : undefined} data-fill={fill ? "" : undefined}
      data-sized={style ? "" : undefined} style={style} aria-label={title}>
      <header class="dock-h">
        <button type="button" class="dock-t" aria-expanded={!collapsed} aria-controls={`dock-b-${stream}`}
          onClick={() => setDockLayout(toggleStream(dockLayout.peek(), stream))}
          title={collapsed ? `Show ${title.toLowerCase()}` : `Collapse ${title.toLowerCase()}`}>
          <span class="dock-chev" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
          <span class="dock-title">{title}</span>
          {/* a running count replaces the total: "2 running ●" reads at a glance; idle streams show how many */}
          {count !== null && count > 0 && running === 0 && <span class="dock-n num">{count}</span>}
          {running > 0 && (
            <span class="dock-running" data-changing data-running={running} title={`${running} running`}>
              <span class="num">{running}</span> running<span class="dock-dot" aria-hidden="true" />
            </span>
          )}
          {unread > 0 && <span class="dock-unread num" data-unread={unread} aria-label={`${unread} new`}>{unread}</span>}
        </button>
        {head}
      </header>
      {!collapsed && <div class="dock-b" id={`dock-b-${stream}`} data-stream-body={stream}>{children}</div>}
    </section>
  );
}

/** The divider under a stream: drag (or ↑ / ↓) to resize the stream above it. */
function Divider({ which, root }: { which: "view" | "sim"; root: { current: HTMLDivElement | null } }): JSX.Element {
  const total = () => root.current?.clientHeight || 800;   // 0 before layout (tests): a typical dock
  const start = useRef<{ y: number; layout: typeof dockLayout.value } | null>(null);
  const label = `Resize ${TITLE[which].toLowerCase()}`;
  return (
    <div class="dock-div" role="separator" aria-orientation="horizontal" aria-label={label} tabIndex={0} data-divider={which}
      aria-valuenow={dockLayout.value[which]}
      onPointerDown={(e) => {
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        start.current = { y: e.clientY, layout: dockLayout.peek() };
      }}
      onPointerMove={(e) => {
        const s = start.current;
        if (s) setDockLayout(resizeStream(s.layout, which, e.clientY - s.y, total()), false);
      }}
      onPointerUp={() => { if (start.current) { start.current = null; setDockLayout(dockLayout.peek()); } }}
      onPointerCancel={() => { start.current = null; }}
      onKeyDown={(e) => {
        const d = e.key === "ArrowUp" ? -16 : e.key === "ArrowDown" ? 16 : 0;
        if (!d) return;
        e.preventDefault();
        setDockLayout(resizeStream(dockLayout.peek(), which, d, total()));
      }} />
  );
}

/** The dock's left edge: drag (or ← / →) to change its width (360–640 px). */
function WidthEdge(): JSX.Element {
  const start = useRef<{ x: number; layout: typeof dockLayout.value } | null>(null);
  return (
    <div class="dock-edge" role="separator" aria-orientation="vertical" aria-label="Resize the agent dock" tabIndex={0}
      aria-valuenow={dockLayout.value.width} aria-valuemin={360} aria-valuemax={640}
      onPointerDown={(e) => {
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        start.current = { x: e.clientX, layout: dockLayout.peek() };
      }}
      onPointerMove={(e) => {
        const s = start.current;
        if (s) setDockLayout(resizeWidth(s.layout, e.clientX - s.x), false);
      }}
      onPointerUp={() => { if (start.current) { start.current = null; setDockLayout(dockLayout.peek()); } }}
      onPointerCancel={() => { start.current = null; }}
      onKeyDown={(e) => {
        const d = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
        if (!d) return;
        e.preventDefault();
        setDockLayout(resizeWidth(dockLayout.peek(), d));
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
      {notLead && label && <span class="dock-who dim text" data-run-label title={label}>{label}</span>}
      {current && <ModelWarning run={current} />}
      {others.length > 0 && (
        <button type="button" class="dock-needs needs-you" data-needs-you aria-label={`${labelOf(others[0])} needs you · ${others.length} waiting`}
          title={`${others.length} needs you`} onClick={() => actions.selectRun(others[0])}>
          <span class="num">{others.length}</span>
        </button>
      )}
      <MoreMenu hasRun={current !== null} />
    </div>
  );
}

function MoreMenu({ hasRun }: { hasRun: boolean }): JSX.Element {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  useEffect(() => { if (open) root.current?.querySelector<HTMLElement>("[role=menuitem]:not([disabled])")?.focus(); }, [open]);
  const pick = (f: () => void) => { setOpen(false); f(); };
  return (
    <div class="dock-more" ref={root}>
      <button type="button" class="dock-more-btn" aria-haspopup="menu" aria-expanded={open} aria-label="Conversation options"
        title="Conversation options" onClick={() => setOpen(!open)}>⋯</button>
      {open && (
        <div class="dock-menu" role="menu" aria-label="Conversation options"
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); root.current?.querySelector<HTMLElement>(".dock-more-btn")?.focus(); }
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              const items = [...(root.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not([disabled])") ?? [])];
              const at = items.indexOf(document.activeElement as HTMLElement);
              items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
            }
          }}>
          <button type="button" role="menuitem" onClick={() => pick(() => { composerOpen.value = true; })}>New run…</button>
          <button type="button" role="menuitem" disabled={!hasRun} onClick={() => pick(() => setConsoleView("browser", false))}>Agent browser</button>
          <button type="button" role="menuitem" onClick={() => pick(() => setConsoleView("details"))}>Full log</button>
        </div>
      )}
    </div>
  );
}
