// The drawing area: an SVG whose viewBox is the current view of the sheet in mm. Pointer events
// cover pen, mouse and trackpad alike (pen strokes use coalesced events for full fidelity).
// Draw tools take clicks (a drag counts as two); the select tool picks, drags vertices, moves
// shapes and pans on empty space; the wheel pans and ⌘/pinch zooms. Geometry, snapping, the
// solver and the view math live in pure modules; this component only wires pointers to them and
// draws: grid + rulers + origin, closed-profile shading, strokes, constraint badges, inference
// guides and the snap marker.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { constraintBadges, type Badge } from "./badges";
import { hitCorner, hitStroke, hitVertex, handles, nearestEdge } from "./edit";
import { add, circumcircle, dist, mul, outline, sub, unit, type Pt } from "./geom";
import { closeIfNear, freehandStroke, labelAnchor, SHEET, toSvg, type Box, type Stroke } from "./model";
import { closedProfiles, profilePath } from "./profiles";
import { strokeCaption } from "./render";
import { snap, snapTargets, type Snap, type Targets } from "./snap";
import { refKey } from "./solver";
import {
  cursor, draft, gridStep, lastSnap, message, picks, selectedConstraint, selectedIds, shown, doc, sketchActions, snapEnds, snapGrid, snapGuides, tool, view,
} from "./state";
import { draftShape, drawsFrom, isDrawTool } from "./tools";
import { gridLines, gridSpacing, pan, placement, rulerTicks, screenToMm, viewBox, visible, zoomAt, type View } from "./view";

const SNAP_PX = 8;
const CLOSE_PX = 10;
const HIT_PX = 6;
const DRAG_PX = 4;
const RULER_PX = 16;

type Gesture =
  | { kind: "pen"; pointerId: number; raw: Pt[] }
  | { kind: "draw"; pointerId: number; x: number; y: number; clicks: number }
  | { kind: "vertex"; pointerId: number; x: number; y: number; ref: { stroke_id: string; vertex: number }; moved: boolean; add: boolean }
  | { kind: "move"; pointerId: number; x: number; y: number; from: Pt; moved: boolean }
  | { kind: "pan"; pointerId: number; x: number; y: number; start: View };

const round1 = (v: number) => (Math.round(v * 10) / 10).toFixed(1);
const clampSheet = (p: Pt): Pt => [Math.min(SHEET.width_mm, Math.max(0, p[0])), Math.min(SHEET.height_mm, Math.max(0, p[1]))];

export function SketchCanvas(): JSX.Element {
  const svg = useRef<SVGSVGElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const [penDraft, setPenDraft] = useState<Stroke | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [hoverBadge, setHoverBadge] = useState<string | null>(null);

  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const measure = () => { const r = el.getBoundingClientRect(); setBox({ left: r.left, top: r.top, width: r.width, height: r.height }); };
    measure();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    window.addEventListener("resize", measure);
    // the wheel pans; ⌘ / ctrl (and the trackpad pinch, which sends ctrl) zooms at the pointer
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = rect();
      const v = view.peek();
      const { scale } = placement(r, v);
      if (!scale) return;
      if (e.ctrlKey || e.metaKey) {
        sketchActions.setView(zoomAt(v, screenToMm(e.clientX, e.clientY, r, v), Math.exp(-e.deltaY * 0.01)));
      } else {
        sketchActions.setView(pan(v, e.deltaX / scale, -e.deltaY / scale));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    // Escape cancels an in-progress gesture (scoped to the sketch region)
    const region = el.closest("[data-sketch-root]") ?? el;
    const onKey = (e: Event) => {
      if ((e as KeyboardEvent).key !== "Escape" || !gesture.current) return;
      if (gesture.current.kind === "vertex" || gesture.current.kind === "move") sketchActions.endDrag(false);
      gesture.current = null;
      setPenDraft(null);
    };
    region.addEventListener("keydown", onKey);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", measure);
      el.removeEventListener("wheel", onWheel);
      region.removeEventListener("keydown", onKey);
    };
  }, []);

  function rect(): Box {
    const r = (svg.current as SVGSVGElement).getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  }
  const mmPerPx = (r: Box = rect()) => { const { scale } = placement(r, view.peek()); return scale > 0 ? 1 / scale : 1; };
  const at = (e: { clientX: number; clientY: number }): Pt => clampSheet(screenToMm(e.clientX, e.clientY, rect(), view.peek()));

  const base = doc.value;
  const targets = useMemo(() => snapTargets(base.strokes), [base]);

  /** Snap for the current tool and step. */
  function snapFor(p: Pt, exclude?: string): Snap {
    const u = mmPerPx();
    const d = draft.peek();
    const t = tool.peek();
    let tg: Targets = exclude ? snapTargets(doc.peek().strokes, new Set([exclude])) : targets;
    if (d && d.clicks.length) tg = { ...tg, endpoints: [...tg.endpoints, ...d.clicks.map((q) => ({ p: q }))] };
    if (t === "pen") return snap(p, { targets: tg, radius: SNAP_PX * u, grid: null, points: snapEnds.peek(), guides: false });
    const from = d && drawsFrom(d.tool, d.clicks.length) ? d.clicks[d.clicks.length - 1] : undefined;
    const s = snap(p, { targets: tg, radius: SNAP_PX * u, grid: snapGrid.peek() ? gridStep.peek() : null, from, points: snapEnds.peek(), guides: snapGuides.peek() });
    // a circle's radius from the grid is a whole grid step
    if (d?.tool === "circle" && d.clicks.length === 1 && s.kind === "grid" && snapGrid.peek()) {
      const c = d.clicks[0];
      const step = gridStep.peek();
      const r = Math.max(step, Math.round(dist(c, p) / step) * step);
      return { ...s, point: add(c, mul(unit(sub(p, c)), r)) };
    }
    return s;
  }

  function onPointerDown(e: PointerEvent): void {
    if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1) return;
    if (gesture.current) return;
    e.preventDefault();
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    const p = at(e);
    const t = tool.peek();
    const u = mmPerPx();
    if (e.button === 1) { gesture.current = { kind: "pan", pointerId: e.pointerId, x: e.clientX, y: e.clientY, start: view.peek() }; return; }
    if (t === "select") {
      const strokes = doc.peek().strokes;
      const sel = new Set(selectedIds.peek());
      const v = hitVertex(strokes.filter((s) => sel.has(s.id)), p, HIT_PX * u) ?? hitVertex(strokes, p, HIT_PX * u * 0.7);
      if (v && v.vertex !== undefined) {
        gesture.current = { kind: "vertex", pointerId: e.pointerId, x: e.clientX, y: e.clientY, ref: { stroke_id: v.stroke_id, vertex: v.vertex }, moved: false, add: e.shiftKey };
        return;
      }
      const hit = hitStroke(strokes, p, HIT_PX * u);
      if (hit) {
        const edge = nearestEdge(hit, p);
        const already = sel.has(hit.id);
        if (!already || e.shiftKey) sketchActions.select(hit.id, e.shiftKey, edge === null ? { stroke_id: hit.id } : { stroke_id: hit.id, edge });
        else if (edge !== null) {
          // re-pick the edge of an already selected stroke (keeps the rest of the selection)
          const others = picks.peek().filter((x) => x.stroke_id !== hit.id);
          picks.value = [...others, { stroke_id: hit.id, edge }];
        }
        gesture.current = { kind: "move", pointerId: e.pointerId, x: e.clientX, y: e.clientY, from: p, moved: false };
        return;
      }
      if (!e.shiftKey) sketchActions.select(null);
      gesture.current = { kind: "pan", pointerId: e.pointerId, x: e.clientX, y: e.clientY, start: view.peek() };
      return;
    }
    if (t === "fillet") {
      const c = hitCorner(doc.peek().strokes, p, HIT_PX * u * 1.5);
      if (c) sketchActions.filletCorner(c);
      else message.value = "Click a corner of a polyline or rectangle.";
      return;
    }
    if (t === "pen") {
      const s = snapFor(p);
      gesture.current = { kind: "pen", pointerId: e.pointerId, raw: [s.kind === "endpoint" ? s.point : p] };
      lastSnap.value = null;
      return;
    }
    if (isDrawTool(t)) {
      const d = draft.peek();
      if (d && d.tool === "polyline" && d.clicks.length >= 3 && dist(p, d.clicks[0]) <= CLOSE_PX * u) {
        sketchActions.finishDraft(true);
        return;
      }
      const s = snapFor(p);
      const clicks = d?.tool === t ? d.clicks.length : 0;
      sketchActions.drawClick(s.point, clickSnap(s));
      gesture.current = { kind: "draw", pointerId: e.pointerId, x: e.clientX, y: e.clientY, clicks: clicks + 1 };
      cursor.value = s.point;
    }
  }

  function onPointerMove(e: PointerEvent): void {
    const g = gesture.current;
    const p = at(e);
    if (!g) {
      if (e.pointerType === "touch") return;
      if (tool.peek() === "select" || tool.peek() === "fillet") { cursor.value = p; lastSnap.value = null; return; }
      const s = snapFor(p);
      cursor.value = s.point;
      lastSnap.value = s.kind ? s : null;
      return;
    }
    if (e.pointerId !== g.pointerId) return;
    const moved = Math.hypot(e.clientX - ("x" in g ? g.x : 0), e.clientY - ("y" in g ? g.y : 0)) >= DRAG_PX;
    switch (g.kind) {
      case "pan": {
        const u = mmPerPx();
        sketchActions.setView(pan(g.start, -(e.clientX - g.x) * u, (e.clientY - g.y) * u));
        return;
      }
      case "vertex": {
        if (!g.moved && !moved) return;
        g.moved = true;
        const s = snapFor(p, g.ref.stroke_id);
        lastSnap.value = s.kind ? s : null;
        sketchActions.dragVertex(g.ref, s.point);
        return;
      }
      case "move": {
        if (!g.moved && !moved) return;
        g.moved = true;
        let by = sub(p, g.from);
        const step = snapGrid.peek() ? gridStep.peek() : 0;
        if (step) by = [Math.round(by[0] / step) * step, Math.round(by[1] / step) * step];
        sketchActions.dragMove(by);
        return;
      }
      case "pen": {
        const events = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
        for (const ev of events.length ? events : [e]) {
          const q = at(ev);
          const last = g.raw[g.raw.length - 1];
          if (Math.hypot(q[0] - last[0], q[1] - last[1]) >= 0.2) g.raw.push(q);
        }
        lastSnap.value = null;
        setPenDraft(g.raw.length >= 2 ? freehandStroke("draft", g.raw, 0.05) : null);
        return;
      }
      case "draw": {
        const s = snapFor(p);
        cursor.value = s.point;
        lastSnap.value = s.kind ? s : null;
      }
    }
  }

  function onPointerUp(e: PointerEvent, keep: boolean): void {
    const g = gesture.current;
    if (!g || e.pointerId !== g.pointerId) return;
    gesture.current = null;
    switch (g.kind) {
      case "vertex":
        if (g.moved) sketchActions.endDrag(keep);
        else if (keep) sketchActions.select(g.ref.stroke_id, g.add, g.ref);
        return;
      case "move":
        if (g.moved) sketchActions.endDrag(keep);
        return;
      case "pan": return;
      case "pen": {
        setPenDraft(null);
        if (!keep) return;
        const end = snapFor(at(e));
        if (end.kind === "endpoint") g.raw.push(end.point);
        else if (g.raw.length < 2) g.raw.push(at(e));
        if (g.raw.length < 2) return;
        const s = closeIfNear(freehandStroke("draft", g.raw), CLOSE_PX * mmPerPx());
        if (s.points.length < 2) return;
        sketchActions.addStroke({ ...s, id: sketchActions.newStrokeId() });
        return;
      }
      case "draw": {
        // a drag: the release is the next click
        const d = draft.peek();
        const far = Math.hypot(e.clientX - g.x, e.clientY - g.y) >= DRAG_PX;
        if (keep && far && d && d.clicks.length === g.clicks) {
          const s = snapFor(at(e));
          sketchActions.drawClick(s.point, clickSnap(s));
        }
      }
    }
  }

  // ---- drawing

  const d = shown.value;
  const v = view.value;
  const b = box ?? { left: 0, top: 0, width: 800, height: 600 };
  const { scale } = placement(b, v);
  const u = scale > 0 ? 1 / scale : 0.25;
  const vis = visible(b, v);
  const vb = viewBox(v);
  const W = SHEET.width_mm;
  const H = SHEET.height_mm;
  const sp = gridSpacing(scale || 4);
  const gx0 = Math.max(0, vis.x);
  const gx1 = Math.min(W, vis.x + vis.w);
  const gy0 = Math.max(0, vis.y);
  const gy1 = Math.min(H, vis.y + vis.h);
  const minor: JSX.Element[] = [];
  const major: JSX.Element[] = [];
  for (const x of gridLines(gx0, gx1, sp.minor)) (Math.abs(x / sp.major - Math.round(x / sp.major)) < 1e-6 ? major : minor).push(<line key={`x${x}`} x1={x} y1={H - gy1} x2={x} y2={H - gy0} />);
  for (const y of gridLines(gy0, gy1, sp.minor)) (Math.abs(y / sp.major - Math.round(y / sp.major)) < 1e-6 ? major : minor).push(<line key={`y${y}`} x1={gx0} y1={H - y} x2={gx1} y2={H - y} />);

  const profiles = useMemo(() => closedProfiles(d.strokes), [d.strokes]);
  const badges = useMemo(() => constraintBadges(d.strokes, d.constraints, u), [d.strokes, d.constraints, u]);
  const sel = new Set(selectedIds.value);
  const pickKeys = new Set(picks.value.map(refKey));
  const selC = selectedConstraint.value;
  const t = tool.value;
  const dr = draft.value;
  const cur = cursor.value;
  const drawShape = dr && cur ? draftShape(dr.tool, dr.clicks, cur) : null;
  const ghost = penDraft ?? drawShape;
  const snapNow = lastSnap.value;
  const readout = drawShape && cur ? draftReadout(drawShape) : null;
  const fs = (px: number) => ({ fontSize: `${px * u}px` });
  const rulerH = RULER_PX * u;

  return (
    <svg ref={svg} class={`sk-svg tool-${t}`} viewBox={vb} preserveAspectRatio="xMidYMin meet"
      role="img" aria-label="Sketch drawing area" data-sketch-canvas data-view={`${v.x} ${v.y} ${v.w} ${v.h}`}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={(e) => onPointerUp(e, true)} onPointerCancel={(e) => onPointerUp(e, false)}
      onDblClick={() => { if (draft.peek()?.tool === "polyline") sketchActions.finishDraft(); }}
      onPointerLeave={() => { if (!gesture.current) lastSnap.value = null; }}>
      <rect class="sk-sheet" x={0} y={0} width={W} height={H} />
      <g class="sk-grid-minor">{minor}</g>
      <g class="sk-grid-major">{major}</g>
      <g class="sk-axes" data-origin>
        <line class="sk-axis-x" x1={Math.max(vis.x, -5)} y1={H} x2={Math.min(vis.x + vis.w, W + 5)} y2={H} />
        <line class="sk-axis-y" x1={0} y1={H - Math.max(vis.y, -5)} x2={0} y2={H - Math.min(vis.y + vis.h, H + 5)} />
        <circle class="sk-origin" cx={0} cy={H} r={3 * u} />
      </g>
      <g class="sk-profiles">
        {profiles.map((p, i) => <path key={i} class="sk-profile" d={profilePath(p, toSvg)} data-profile={i} />)}
      </g>
      <g class="sk-strokes">
        {d.strokes.map((s) => {
          const cls = `sk-stroke${sel.has(s.id) ? " selected" : ""}${s.construction ? " construction" : ""}`;
          const pts = outline(s).map((q) => toSvg(q).map(fmt).join(",")).join(" ");
          const closed = s.kind === "rect" || s.kind === "circle" || s.kind === "slot" || s.closed;
          return closed
            ? <polygon key={s.id} class={cls} points={pts} data-stroke-id={s.id} data-kind={s.kind} />
            : <polyline key={s.id} class={cls} points={pts} data-stroke-id={s.id} data-kind={s.kind} />;
        })}
      </g>
      <g class="sk-picked">
        {picks.value.filter((p) => p.edge !== undefined).map((p) => {
          const s = d.strokes.find((x) => x.id === p.stroke_id);
          if (!s) return null;
          const a = s.points[p.edge as number];
          const c = s.points[((p.edge as number) + 1) % s.points.length];
          if (!a || !c) return null;
          const [x1, y1] = toSvg(a as Pt);
          const [x2, y2] = toSvg(c as Pt);
          return <line key={refKey(p)} class="sk-edge-pick" x1={x1} y1={y1} x2={x2} y2={y2} data-picked-edge={refKey(p)} />;
        })}
      </g>
      <g class="sk-labels">
        {d.strokes.map((s) => {
          const text = strokeCaption(s, d.dimensions);
          if (!text) return null;
          const [x, y] = toSvg(labelAnchor(s));
          return <text key={s.id} class={`sk-label${sel.has(s.id) ? " selected" : ""}`} x={x} y={y - 6 * u} style={fs(11)} data-label-for={s.id}>{text}</text>;
        })}
      </g>
      <g class="sk-badges">
        {badges.map((bd, i) => <BadgeView key={`${bd.id}:${i}`} b={bd} u={u} selected={bd.id === selC} hover={bd.id === hoverBadge}
          onPick={() => { if (!draft.peek()?.clicks.length) sketchActions.selectConstraint(bd.id); }} onHover={setHoverBadge} />)}
      </g>
      <g class="sk-handles">
        {d.strokes.filter((s) => sel.has(s.id)).flatMap((s) => handles(s).map((q, i) => {
          const [x, y] = toSvg(q);
          const k = `${s.id}:v${i}`;
          const hs = 7 * u;
          return <rect key={k} class={`sk-handle${pickKeys.has(k) ? " picked" : ""}`} x={x - hs / 2} y={y - hs / 2} width={hs} height={hs} data-handle={k} />;
        }))}
      </g>
      {ghost && (() => {
        const pts = outline(ghost).map((q) => toSvg(q).map(fmt).join(",")).join(" ");
        const closed = ghost.kind === "rect" || ghost.kind === "circle" || ghost.kind === "slot" || ghost.closed;
        return closed ? <polygon class="sk-draft" points={pts} /> : <polyline class="sk-draft" points={pts} />;
      })()}
      {dr && dr.clicks.map((q, i) => { const [x, y] = toSvg(q); return <circle key={i} class="sk-click" cx={x} cy={y} r={2.5 * u} />; })}
      <g class="sk-guides">
        {snapNow?.guides.map((g, i) => {
          const [x1, y1] = toSvg(g.a);
          const [x2, y2] = toSvg(g.b);
          const far = extend(g.a, g.b, 30 * u);
          const [x3, y3] = toSvg(far);
          return (
            <g key={i} data-guide={g.kind}>
              <line class="sk-guide" x1={x1} y1={y1} x2={x3} y2={y3} />
              {i === 0 && <text class="sk-guide-label" x={x2 + 8 * u} y={y2 + 14 * u} style={fs(10)}>{g.label}</text>}
              {i === 1 && <text class="sk-guide-label" x={x2 + 8 * u} y={y2 + 26 * u} style={fs(10)}>{g.label}</text>}
            </g>
          );
        })}
      </g>
      {readout && cur && (() => { const [x, y] = toSvg(cur); return <text class="sk-readout" x={x + 10 * u} y={y - 8 * u} style={fs(11)} data-readout>{readout}</text>; })()}
      {snapNow && snapNow.kind && snapNow.kind !== "grid" && snapNow.kind !== "guide" && (() => {
        const [x, y] = toSvg(snapNow.point);
        const r = 5 * u;
        return (
          <g data-snap={snapNow.kind}>
            {snapNow.kind === "endpoint" && <rect class="sk-snap" x={x - r} y={y - r} width={2 * r} height={2 * r} />}
            {snapNow.kind === "midpoint" && <polygon class="sk-snap" points={`${x},${y - r} ${x + r},${y + r} ${x - r},${y + r}`} />}
            {snapNow.kind === "center" && <circle class="sk-snap" cx={x} cy={y} r={r} />}
            {snapNow.kind === "intersection" && <path class="sk-snap" d={`M${x - r},${y - r}L${x + r},${y + r}M${x - r},${y + r}L${x + r},${y - r}`} />}
            {snapNow.kind === "on" && <circle class="sk-snap" cx={x} cy={y} r={r * 0.6} />}
            {snapNow.label && <text class="sk-guide-label" x={x + 8 * u} y={y + 14 * u} style={fs(10)}>{snapNow.label}</text>}
          </g>
        );
      })()}
      <Rulers vis={vis} scale={scale || 4} u={u} h={rulerH} />
    </svg>
  );
}

function clickSnap(s: Snap): { ref?: { stroke_id: string; vertex?: number; edge?: number }; guide?: "horizontal" | "vertical" } {
  const out: { ref?: { stroke_id: string; vertex?: number }; guide?: "horizontal" | "vertical" } = {};
  if (s.ref) out.ref = s.ref;
  const g = s.guides[0]?.kind;
  if (s.kind === "guide" && s.guides.length === 1 && (g === "horizontal" || g === "vertical")) out.guide = g;
  return out;
}

const fmt = (v: number) => String(Math.round(v * 100) / 100);

function extend(a: Pt, b: Pt, by: number): Pt {
  const l = dist(a, b);
  return l < 1e-9 ? b : add(b, mul(unit(sub(b, a)), by));
}

function BadgeView({ b, u, selected, hover, onPick, onHover }: { b: Badge; u: number; selected: boolean; hover: boolean; onPick: () => void; onHover: (id: string | null) => void }): JSX.Element {
  const [x, y] = toSvg(b.at);
  const cls = `sk-badge kind-${b.kind}${selected ? " selected" : ""}${hover ? " hover" : ""}`;
  const down = (e: PointerEvent) => { if (draft.peek()?.clicks.length) return; e.stopPropagation(); onPick(); };
  if (b.kind === "coincident") {
    return <circle class={cls} cx={x} cy={y} r={2.6 * u} data-constraint={b.id} data-ckind={b.kind} onPointerDown={down} onPointerEnter={() => onHover(b.id)} onPointerLeave={() => onHover(null)} />;
  }
  const lines = [...(b.line ? [b.line] : []), ...(b.ext ?? [])];
  return (
    <g class={cls} data-constraint={b.id} data-ckind={b.kind} onPointerDown={down} onPointerEnter={() => onHover(b.id)} onPointerLeave={() => onHover(null)}>
      {lines.map(([p, q], i) => { const [x1, y1] = toSvg(p); const [x2, y2] = toSvg(q); return <line key={i} class="sk-dimline" x1={x1} y1={y1} x2={x2} y2={y2} />; })}
      {b.text && <text class="sk-badge-text" x={x} y={y} style={{ fontSize: `${(b.kind === "distance" || b.kind === "diameter" || b.kind === "radius" ? 11 : 9) * u}px` }}>{b.text}</text>}
    </g>
  );
}

function Rulers({ vis, scale, u, h }: { vis: View; scale: number; u: number; h: number }): JSX.Element {
  const H = SHEET.height_mm;
  const top = H - (vis.y + vis.h);       // SVG y of the visible top edge
  const left = vis.x;
  const xs = rulerTicks(vis.x, vis.x + vis.w, scale);
  const ys = rulerTicks(vis.y, vis.y + vis.h, scale);
  const minorX = gridLines(vis.x, vis.x + vis.w, gridSpacing(scale, 6).minor);
  const minorY = gridLines(vis.y, vis.y + vis.h, gridSpacing(scale, 6).minor);
  return (
    <g class="sk-rulers" aria-hidden="true">
      <rect class="sk-ruler" x={vis.x} y={top} width={vis.w} height={h} />
      <rect class="sk-ruler" x={left} y={top} width={h} height={vis.h} />
      {minorX.map((x) => <line key={`mx${x}`} class="sk-tick" x1={x} y1={top + h * 0.7} x2={x} y2={top + h} />)}
      {minorY.map((y) => <line key={`my${y}`} class="sk-tick" x1={left + h * 0.7} y1={H - y} x2={left + h} y2={H - y} />)}
      {xs.map((t) => (
        <g key={`x${t.at}`}>
          <line class="sk-tick major" x1={t.at} y1={top + h * 0.35} x2={t.at} y2={top + h} />
          <text class="sk-ruler-label" x={t.at + 2 * u} y={top + h * 0.6} style={{ fontSize: `${9 * u}px` }}>{t.label}</text>
        </g>
      ))}
      {ys.map((t) => (
        <g key={`y${t.at}`}>
          <line class="sk-tick major" x1={left + h * 0.35} y1={H - t.at} x2={left + h} y2={H - t.at} />
          <text class="sk-ruler-label" x={left + 2 * u} y={H - t.at - 2 * u} style={{ fontSize: `${9 * u}px` }}>{t.label}</text>
        </g>
      ))}
      <rect class="sk-ruler corner" x={left} y={top} width={h} height={h} />
      <text class="sk-ruler-label" x={left + 2 * u} y={top + h * 0.7} style={{ fontSize: `${8 * u}px` }}>mm</text>
    </g>
  );
}

function draftReadout(s: Stroke): string | null {
  const p = s.points as Pt[];
  if (s.kind === "line" || s.kind === "polyline") {
    const a = p[p.length - 2];
    const b = p[p.length - 1];
    if (!a || !b) return null;
    const deg = (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI;
    return `${round1(dist(a, b))} mm · ${round1(((deg % 360) + 360) % 360)}°`;
  }
  if (s.kind === "rect") return `${round1(Math.abs(p[2][0] - p[0][0]))} × ${round1(Math.abs(p[2][1] - p[0][1]))} mm`;
  if (s.kind === "circle" && s.radius_mm) return `Ø${round1(s.radius_mm * 2)} mm`;
  if (s.kind === "arc") { const cc = circumcircle(p[0], p[1], p[2]); return cc ? `R${round1(cc.r)} mm` : null; }
  if (s.kind === "slot" && s.radius_mm) return `${round1(dist(p[0], p[1]))} mm · width ${round1(s.radius_mm * 2)}`;
  return null;
}
