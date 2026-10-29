// Sketch page (design §7.9): draw with pen, mouse or trackpad — line, polyline, rectangle (corner
// or center), circle, 3-point arc, slot, fillet, freehand — with grid, point snaps and SketchUp-style
// inference guides; type lengths while drawing; constrain (horizontal, vertical, equal,
// coincident, driving dimensions); mirror, offset, construction lines; start from a template; see
// the closed profiles extruded in 3D; or upload a photo of a sketch. "Build part" starts a run
// whose agent gets the sketch (profiles, dimensions and constraints) as structured data.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { SketchConstraintKind } from "../../../api/types";
import { providerLabel } from "../../../lib/names";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { PageShell } from "../PageShell";
import { setConsoleCollapsed } from "../router";
import { buildPart, buildTarget, describeError, type BuildTarget } from "./build";
import { ExtrudePreview } from "./ExtrudePreview";
import { dist } from "./geom";
import { isTypedChar } from "./input";
import { measuredLength, MAX_STROKES, MAX_TOTAL_POINTS, withinPointBudget, type Stroke } from "./model";
import { formatBytes, readPhoto } from "./photo";
import { closedProfiles } from "./profiles";
import { renderPng } from "./render";
import { SketchCanvas } from "./SketchCanvas";
import { checkConstraint, edgeCount, refKey, vertexCount } from "./solver";
import {
  build, canRedo, canUndo, doc, draft, extrudeMm, filletR, GRID_STEPS, type Pick, gridStep, lastSnap, message, notes, photo, photoError, picks, selectedConstraint,
  selectedStroke, selectedStrokes, sketchActions, snapEnds, snapGrid, snapGuides, tool, TOOLS, typed, view,
} from "./state";
import { TEMPLATES } from "./templates";
import { hint, isDrawTool, takesRadius } from "./tools";
import { zoomAt } from "./view";
import "./sketch.css";

const KIND_LABEL: Record<Stroke["kind"], string> = {
  freehand: "Freehand", line: "Line", polyline: "Polyline", rect: "Rectangle", circle: "Circle", arc: "Arc", slot: "Slot",
};
const CONSTRAINT_LABEL: Record<SketchConstraintKind, string> = {
  horizontal: "Horizontal", vertical: "Vertical", equal: "Equal", coincident: "Coincident", distance: "Distance", diameter: "Diameter", radius: "Radius",
  distance_x: "X offset", distance_y: "Y offset", point_on_circle: "On circle",
};

/** A focused control whose own keys (Enter, arrows) must keep working: buttons, links, the shapes list. */
function isControl(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el?.closest?.("button, a, select, input, textarea, [role=button], [role=option], [role=listbox]");
}
/** Constraint buttons and their keys (with Shift). */
const CONSTRAIN: readonly { kind: SketchConstraintKind; key: string }[] = [
  { kind: "horizontal", key: "H" }, { kind: "vertical", key: "V" }, { kind: "equal", key: "E" }, { kind: "coincident", key: "I" },
];

const NOT_TEXT_INPUTS = new Set(["checkbox", "radio", "button", "submit", "reset", "file", "range", "color"]);

/** Keys go to a text field (or a select) here, not to the sketch shortcuts. */
function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  if (el.tagName === "INPUT") return !NOT_TEXT_INPUTS.has((el as HTMLInputElement).type);
  return el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

/** "Line · 40.0 mm drawn" — what a stroke is and how big it was drawn (mm). */
export function strokeSummary(s: Stroke): string {
  const k = `${KIND_LABEL[s.kind]}${s.construction ? " (construction)" : ""}`;
  if (s.kind === "circle" && s.radius_mm) return `${k} · Ø${(s.radius_mm * 2).toFixed(1)} mm drawn`;
  if (s.kind === "rect") {
    const [a, , c] = s.points;
    return `${k} · ${Math.abs(c[0] - a[0]).toFixed(1)} × ${Math.abs(c[1] - a[1]).toFixed(1)} mm drawn`;
  }
  if (s.kind === "slot" && s.radius_mm) return `${k} · ${dist(s.points[0], s.points[1]).toFixed(1)} mm, width ${(s.radius_mm * 2).toFixed(1)} drawn`;
  return `${k} · ${measuredLength(s).toFixed(1)} mm drawn`;
}

const offsetMm = { value: 2 };

export function SketchPage(): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Scoped to the sketch region (A5): a key fired while focus is elsewhere in the app (the
    // console, another pane) never reaches these shortcuts — the listener is on the region, so
    // only keystrokes that bubble up from inside it (a tool button, the canvas, a field) count.
    const el = root.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.isComposing) return;
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key;
      if (mod && key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) sketchActions.redo();
        else sketchActions.undo();
        return;
      }
      if (mod && (key === "=" || key === "+" || key === "-")) { e.preventDefault(); zoomBy(key === "-" ? 1 / 1.25 : 1.25); return; }
      if (mod && key === "0") { e.preventDefault(); sketchActions.fitView(); return; }
      if (mod || e.altKey) return;
      const d = draft.peek();
      const t = tool.peek();
      const buf = typed.peek();
      // the measurements box: typed lengths while drawing (and the fillet radius)
      const control = isControl(e.target);
      if ((d && d.clicks.length) || t === "fillet" || isDrawTool(t)) {
        if (key === "Enter") {
          if (control && !buf) return;                 // Enter on a button presses the button
          e.preventDefault();
          if (buf) sketchActions.commitTyped();
          else if (t === "fillet") sketchActions.filletPicked();
          else if (d) sketchActions.finishDraft();
          return;
        }
        if (key === "Backspace" && buf) { e.preventDefault(); sketchActions.setTyped(buf.slice(0, -1)); return; }
        if (key === "Backspace" && d?.clicks.length) { e.preventDefault(); sketchActions.undoClick(); return; }
        if (isTypedChar(key, buf, takesRadius(t, d?.clicks.length ?? 0))) { e.preventDefault(); sketchActions.setTyped(buf + key); return; }
      }
      if (key === "Escape") {
        if (buf) sketchActions.setTyped("");
        else if (d) sketchActions.cancelDraft();
        else { sketchActions.select(null); sketchActions.selectConstraint(null); }
        return;
      }
      if (key === "Delete" || key === "Backspace") {
        if (sketchActions.deleteSelection()) e.preventDefault();
        return;
      }
      if (key.startsWith("Arrow") && !control && picks.peek().length) {
        const step = e.shiftKey ? 10 : 1;
        const [dx, dy] = key === "ArrowLeft" ? [-step, 0] : key === "ArrowRight" ? [step, 0] : key === "ArrowUp" ? [0, step] : [0, -step];
        e.preventDefault();
        sketchActions.nudge(dx, dy);
        return;
      }
      if (e.shiftKey) {
        const c = CONSTRAIN.find((x) => x.key === key.toUpperCase());
        if (c) { e.preventDefault(); sketchActions.addConstraint(c.kind); }
        return;
      }
      if (picks.peek().length) {
        if (key === "x") { sketchActions.toggleConstruction(); return; }
        if (key === "m") { sketchActions.mirrorSelection(); return; }
        if (key === "o") { sketchActions.offsetSelection(offsetMm.value); return; }
      }
      if (key === "=" || key === "+" || key === "-") { zoomBy(key === "-" ? 1 / 1.25 : 1.25); return; }
      if (key === "0") { sketchActions.fitView(); return; }
      const tt = TOOLS.find((x) => x.key === key.toLowerCase());
      if (tt) sketchActions.setTool(tt.id);
    };
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  }, []);

  // clicking the canvas or empty space focuses the region so its keyboard shortcuts fire there
  // (the svg isn't focusable on its own); a click on a field or button keeps that control's focus
  const focusRegion = (e: JSX.TargetedPointerEvent<HTMLDivElement>): void => {
    const t = e.target as HTMLElement;
    if (!isTyping(t) && !t.closest("button, a, [role=button]")) root.current?.focus();
  };

  return (
    <PageShell title="Sketch">
      <div class="sk-layout" ref={root} tabIndex={-1} data-sketch-root onPointerDown={focusRegion}>
        <div class="sk-stage">
          <ToolBar />
          <SketchCanvas />
          <HintBar />
        </div>
        <SidePanel />
      </div>
    </PageShell>
  );
}

function zoomBy(f: number): void {
  const v = view.peek();
  sketchActions.setView(zoomAt(v, [v.x + v.w / 2, v.y + v.h / 2], f));
}

function ToolBar(): JSX.Element {
  return (
    <div class="sk-bar" role="toolbar" aria-label="Sketch tools">
      <div class="sk-group sk-tools">
        {TOOLS.map((t) => (
          <button key={t.id} type="button" class="btn sk-tool" aria-pressed={tool.value === t.id} title={`${t.label} (${t.key.toUpperCase()})`}
            onClick={() => sketchActions.setTool(t.id)}>
            <span>{t.label}</span><span class="faint sk-key">{t.key.toUpperCase()}</span>
          </button>
        ))}
      </div>
      <div class="sk-group">
        <span class="label">Snap</span>
        <label class="sk-check"><input type="checkbox" checked={snapGrid.value} onChange={(e) => { snapGrid.value = e.currentTarget.checked; }} /> Grid</label>
        <select aria-label="Grid step" value={String(gridStep.value)} disabled={!snapGrid.value}
          onChange={(e) => { gridStep.value = Number(e.currentTarget.value); }}>
          {GRID_STEPS.map((s) => <option key={s} value={String(s)}>{s} mm</option>)}
        </select>
        <label class="sk-check"><input type="checkbox" checked={snapEnds.value} onChange={(e) => { snapEnds.value = e.currentTarget.checked; }} /> Points</label>
        <label class="sk-check"><input type="checkbox" checked={snapGuides.value} onChange={(e) => { snapGuides.value = e.currentTarget.checked; }} /> Guides</label>
      </div>
      <div class="sk-group">
        <Button variant="ghost" onClick={() => zoomBy(1 / 1.25)} title="Zoom out (−)" aria-label="Zoom out">−</Button>
        <Button variant="ghost" onClick={() => zoomBy(1.25)} title="Zoom in (+)" aria-label="Zoom in">+</Button>
        <Button variant="ghost" onClick={() => sketchActions.fitView()} title="Fit (0)">Fit</Button>
      </div>
      <div class="sk-group">
        <Button onClick={() => sketchActions.undo()} disabled={!canUndo.value} title="Undo (⌘Z)">Undo</Button>
        <Button onClick={() => sketchActions.redo()} disabled={!canRedo.value} title="Redo (⇧⌘Z)">Redo</Button>
        <Button variant="danger" onClick={() => sketchActions.clear()} disabled={!doc.value.strokes.length}>Clear</Button>
      </div>
    </div>
  );
}

function HintBar(): JSX.Element {
  const d = draft.value;
  const t = tool.value;
  const n = d && d.tool === t ? d.clicks.length : 0;
  const buf = typed.value;
  const box = (d && d.clicks.length) || t === "fillet" || isDrawTool(t);
  const s = lastSnap.value;
  return (
    <div class="sk-hint" data-hint-bar>
      <span class="sk-hint-step" data-hint>{hint(t, n)}</span>
      {box && (
        <span class={`sk-typed${buf ? " active" : ""}`} data-typed aria-live="polite">
          <span class="faint">{t === "fillet" ? `R ${filletR.value} mm` : "Type"}</span> <span class="sk-typed-text">{buf || "…"}</span>
        </span>
      )}
      {s?.label && <span class="sk-hint-snap faint" data-snap-label>{s.label}</span>}
      <span class="sk-grow" />
      {message.value
        ? <span class="sk-hint-msg" aria-live="polite" data-sketch-message>{message.value}</span>
        : <span class="faint">{doc.value.strokes.length ? `${doc.value.strokes.length} stroke${doc.value.strokes.length === 1 ? "" : "s"} · mm, origin bottom-left` : "Draw, start from a template, or add a photo."}</span>}
    </div>
  );
}

function SidePanel(): JSX.Element {
  return (
    <aside class="sk-side" aria-label="Sketch details">
      <ShapeList />
      <SelectedStroke />
      <SelectedConstraint />
      <TemplatesBox />
      <ExtrudeBox />
      <PhotoBox />
      <section class="sk-sec">
        <label class="label" for="sk-notes">Notes for the agent</label>
        <textarea id="sk-notes" class="sk-notes" rows={3} maxLength={2000} placeholder="e.g. wall bracket, 5 mm thick, PETG"
          value={notes.value} onInput={(e) => { notes.value = e.currentTarget.value; }} />
      </section>
      <BuildBox />
    </aside>
  );
}

function SelectedStroke(): JSX.Element {
  const s = selectedStroke.value;
  const dim = s ? doc.value.dimensions.find((d) => d.stroke_id === s.id) : undefined;
  const ps = picks.value;
  const strokes = doc.value.strokes;
  if (!s) {
    return (
      <section class="sk-sec">
        <div class="label">Dimension</div>
        <p class="faint sk-p">Select a stroke (V) and type a size (40, Ø20, R5, 2 in) or a note.</p>
      </section>
    );
  }
  const pick = ps.at(-1);
  const edge = pick?.stroke_id === s.id && pick.edge !== undefined && (s.kind === "rect" || s.kind === "polyline") ? pick.edge : null;
  const n = selectedStrokes.value.length;
  return (
    <section class="sk-sec" data-selected-stroke={s.id}>
      <label class="label" for="sk-dim">Dimension</label>
      <div class="dim sk-p">{n > 1 ? `${n} selected · ` : ""}{strokeSummary(s)}</div>
      {/* keyed: a new stroke or a new saved label starts the field afresh (no effect racing typing) */}
      <DimensionField key={`${s.id}:${dim?.text ?? ""}`} stroke={s} saved={dim?.text ?? ""} />
      {dim && <div class="faint sk-p" data-dim-parsed>{dim.value_mm !== null ? `${dim.kind} · ${dim.value_mm} mm` : dim.kind}</div>}
      {edge !== null && <EdgeLength key={`${s.id}:${edge}`} stroke={s} edge={edge} />}
      <div class="label">Constrain</div>
      <div class="sk-row sk-wrap">
        {CONSTRAIN.map((c) => {
          const why = checkConstraint(c.kind, ps, strokes);
          return (
            <Button key={c.kind} variant="ghost" disabled={why !== null} title={why ?? `${CONSTRAINT_LABEL[c.kind]} (⇧${c.key})`}
              onClick={() => sketchActions.addConstraint(c.kind)}>{CONSTRAINT_LABEL[c.kind]}</Button>
          );
        })}
      </div>
      <DistanceField />
      <StrokeConstraints strokeId={s.id} />
      {pick?.vertex !== undefined && (s.kind === "rect" || s.kind === "polyline") && (
        <div class="sk-row">
          <Button variant="ghost" onClick={() => sketchActions.filletPicked()} title="Round the picked corner (Fillet tool: Enter)">Fillet corner</Button>
          <span class="faint">R {filletR.value} mm</span>
        </div>
      )}
      <div class="sk-row sk-wrap">
        <button type="button" class="btn btn-ghost" aria-pressed={!!s.construction} title="Construction line (X)" onClick={() => sketchActions.toggleConstruction()}>Construction</button>
        <Button variant="ghost" title="Mirror about the selected line (M)" onClick={() => sketchActions.mirrorSelection()}>Mirror</Button>
        <OffsetField />
        <Button variant="ghost" class="sk-del" onClick={() => sketchActions.deleteSelection()}>Delete</Button>
      </div>
    </section>
  );
}

/** A driving distance on the picks: a side's length, or between two points (also by x or y). */
function DistanceField(): JSX.Element | null {
  const ps = picks.value;
  const strokes = doc.value.strokes;
  const [text, setText] = useState("");
  const kinds = (["distance", "distance_x", "distance_y"] as const).filter((k) => checkConstraint(k, ps, strokes) === null);
  if (!kinds.length) return null;
  const add = (k: SketchConstraintKind) => { const v = Number(text); if (v > 0) sketchActions.addConstraint(k, ps, v); };
  return (
    <div class="sk-row sk-wrap" data-distance-field>
      <input type="text" inputMode="decimal" class="sk-num" value={text} aria-label="Distance (mm)" placeholder="mm"
        onInput={(e) => setText(e.currentTarget.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(kinds[0]); } }} />
      {kinds.map((k) => <Button key={k} variant="ghost" onClick={() => add(k)}>{CONSTRAINT_LABEL[k]}</Button>)}
    </div>
  );
}

/** Ways to pick within a stroke, in order: the stroke (or its sides), then its points. */
export function subPicks(s: Stroke): Pick[] {
  const sides = Array.from({ length: edgeCount(s) }, (_, edge) => ({ stroke_id: s.id, edge }));
  const pts = s.kind === "freehand" ? [] : Array.from({ length: vertexCount(s) }, (_, vertex) => ({ stroke_id: s.id, vertex }));
  return [...(sides.length ? sides : [{ stroke_id: s.id }]), ...pts];
}

function pickText(p: Pick | undefined): string {
  if (!p) return "";
  return p.edge !== undefined ? ` · side ${p.edge + 1}` : p.vertex !== undefined ? ` · point ${p.vertex + 1}` : "";
}

/** The shapes as a listbox: ↑ ↓ choose a shape, ← → step through its sides and points, Space adds
 *  to the selection (Shift+↑ ↓ too). What is picked here is what the canvas highlights and every
 *  edit acts on. */
function ShapeList(): JSX.Element {
  const strokes = doc.value.strokes;
  const ps = picks.value;
  const selected = new Set(ps.map((p) => p.stroke_id));
  const [active, setActive] = useState<string | null>(null);
  const cur = strokes.find((s) => s.id === active) ?? strokes.find((s) => s.id === ps.at(-1)?.stroke_id) ?? strokes[0];
  const pickOf = (s: Stroke) => [...ps].reverse().find((p) => p.stroke_id === s.id);
  const choose = (s: Stroke, add: boolean, p?: Pick) => { setActive(s.id); sketchActions.select(s.id, add, p ?? pickOf(s) ?? subPicks(s)[0]); };
  const onKey = (e: JSX.TargetedKeyboardEvent<HTMLUListElement>) => {
    if (!cur) return;
    const i = strokes.indexOf(cur);
    const k = e.key;
    if (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End") {
      const j = k === "Home" ? 0 : k === "End" ? strokes.length - 1 : Math.max(0, Math.min(strokes.length - 1, i + (k === "ArrowDown" ? 1 : -1)));
      choose(strokes[j], e.shiftKey);
    } else if (k === "ArrowLeft" || k === "ArrowRight") {
      const subs = subPicks(cur);
      const at = Math.max(0, subs.findIndex((p) => refKey(p) === refKey(pickOf(cur) ?? subs[0])));
      const next = subs[(at + (k === "ArrowRight" ? 1 : subs.length - 1)) % subs.length];
      const others = ps.filter((p) => p.stroke_id !== cur.id);
      setActive(cur.id);
      picks.value = [...others, next];
      selectedConstraint.value = null;
    } else if (k === " " || k === "Enter") {
      choose(cur, k === " ");                          // Space toggles it in the selection; Enter selects only it
    } else return;
    e.preventDefault();
    e.stopPropagation();
  };
  return (
    <section class="sk-sec">
      <div class="label" id="sk-shapes-label">Shapes</div>
      {strokes.length ? (
        <ul class="sk-shapes" role="listbox" tabIndex={0} aria-labelledby="sk-shapes-label" aria-multiselectable="true"
          aria-activedescendant={cur ? `sk-opt-${cur.id}` : undefined} onKeyDown={onKey}>
          {strokes.map((s) => (
            <li key={s.id} id={`sk-opt-${s.id}`} role="option" aria-selected={selected.has(s.id)} class={`sk-shape${s === cur ? " active" : ""}`}
              data-shape={s.id} onClick={(e) => choose(s, e.shiftKey)}>
              {KIND_LABEL[s.kind]}{s.construction ? " (construction)" : ""}{selected.has(s.id) ? pickText(pickOf(s)) : ""}
            </li>
          ))}
        </ul>
      ) : <p class="faint sk-p">Nothing drawn yet.</p>}
    </section>
  );
}

/** The constraints on a stroke, each selectable and removable (the keyboard way to the badges). */
function StrokeConstraints({ strokeId }: { strokeId: string }): JSX.Element | null {
  const cs = doc.value.constraints.filter((c) => c.refs.some((r) => r.stroke_id === strokeId));
  if (!cs.length) return null;
  return (
    <ul class="sk-clist" aria-label="Constraints on this shape">
      {cs.map((c) => (
        <li key={c.id} class="sk-row" data-constraint-row={c.id}>
          <button type="button" class="btn btn-ghost sk-grow sk-cname" onClick={() => sketchActions.selectConstraint(c.id)}>
            {CONSTRAINT_LABEL[c.kind]}{c.value_mm !== undefined ? ` ${c.kind === "diameter" ? "Ø" : c.kind === "radius" ? "R" : ""}${c.value_mm}` : ""}
            {c.refs.map((r) => (r.edge !== undefined ? ` · side ${r.edge + 1}` : r.vertex !== undefined ? ` · point ${r.vertex + 1}` : "")).join("")}
          </button>
          <Button variant="ghost" aria-label={`Remove ${CONSTRAINT_LABEL[c.kind].toLowerCase()} constraint`} onClick={() => sketchActions.removeConstraint(c.id)}>✕</Button>
        </li>
      ))}
    </ul>
  );
}

function DimensionField({ stroke: s, saved }: { stroke: Stroke; saved: string }): JSX.Element {
  const [text, setText] = useState(saved);
  const apply = () => sketchActions.setDimension(s.id, text);
  return (
    <div class="sk-row">
      <input id="sk-dim" type="text" maxLength={64} value={text} placeholder="40, Ø20, R5…" aria-label="Dimension label"
        onInput={(e) => setText(e.currentTarget.value)} onBlur={apply}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); apply(); } else if (e.key === "Escape") setText(saved); }} />
      <Button onClick={apply}>Set</Button>
    </div>
  );
}

function EdgeLength({ stroke: s, edge }: { stroke: Stroke; edge: number }): JSX.Element {
  const c = doc.value.constraints.find((x) => x.id === `len-${s.id}-e${edge}`);
  const a = s.points[edge];
  const b = s.points[(edge + 1) % s.points.length];
  const now = a && b ? dist(a, b) : 0;
  const [text, setText] = useState(c?.value_mm !== undefined ? String(c.value_mm) : "");
  const apply = () => {
    const t = text.trim();
    sketchActions.setEdgeLength(s.id, edge, t ? Number(t) : null);
  };
  return (
    <div class="sk-row">
      <label class="faint" for="sk-edge">Side {edge + 1}</label>
      <input id="sk-edge" type="text" inputMode="decimal" value={text} placeholder={now.toFixed(1)} aria-label="Side length (mm)"
        onInput={(e) => setText(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); apply(); } }} />
      <Button onClick={apply}>Set</Button>
    </div>
  );
}

function OffsetField(): JSX.Element {
  const [text, setText] = useState(String(offsetMm.value));
  const run = () => {
    const v = Number(text);
    if (Number.isFinite(v) && v !== 0) { offsetMm.value = v; sketchActions.offsetSelection(v); }
  };
  return (
    <span class="sk-row sk-offset">
      <Button variant="ghost" title="Offset copy (O); negative goes inward" onClick={run}>Offset</Button>
      <input type="text" inputMode="decimal" value={text} aria-label="Offset (mm)" onInput={(e) => setText(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); run(); } }} />
      <span class="faint">mm</span>
    </span>
  );
}

function SelectedConstraint(): JSX.Element | null {
  const id = selectedConstraint.value;
  const c = id ? doc.value.constraints.find((x) => x.id === id) : undefined;
  if (!c) return null;
  return (
    <section class="sk-sec" data-selected-constraint={c.id}>
      <div class="label">Constraint</div>
      <div class="dim sk-p">{CONSTRAINT_LABEL[c.kind]}{c.value_mm !== undefined ? ` · ${c.kind === "diameter" ? "Ø" : c.kind === "radius" ? "R" : ""}${c.value_mm} mm` : ""}</div>
      {c.value_mm !== undefined && <ConstraintValue key={`${c.id}:${c.value_mm}`} id={c.id} value={c.value_mm} />}
      <Button variant="ghost" class="sk-del" onClick={() => sketchActions.removeConstraint(c.id)}>Remove constraint</Button>
    </section>
  );
}

function ConstraintValue({ id, value }: { id: string; value: number }): JSX.Element {
  const [text, setText] = useState(String(value));
  const apply = () => { const v = Number(text); if (v > 0) sketchActions.setConstraintValue(id, v); };
  return (
    <div class="sk-row">
      <input type="text" inputMode="decimal" value={text} aria-label="Constraint value (mm)" onInput={(e) => setText(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); apply(); } }} />
      <Button onClick={apply}>Set</Button>
    </div>
  );
}

function TemplatesBox(): JSX.Element {
  return (
    <section class="sk-sec" aria-label="Templates">
      <div class="label">Start from</div>
      <div class="sk-templates">
        {TEMPLATES.map((t) => (
          <button key={t.id} type="button" class="btn btn-ghost sk-template" onClick={() => sketchActions.insertTemplate(t.id)} data-template={t.id}>
            <span>{t.label}</span><span class="faint">{t.detail}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ExtrudeBox(): JSX.Element {
  const profiles = useMemo(() => closedProfiles(doc.value.strokes), [doc.value.strokes]);
  const depth = extrudeMm.value;
  const [text, setText] = useState(String(depth));
  useEffect(() => setText(String(depth)), [depth]);
  const apply = () => { const v = Number(text); if (v > 0 && v <= 1000) extrudeMm.value = v; else setText(String(depth)); };
  const holes = profiles.reduce((n, p) => n + p.holes.length, 0);
  return (
    <section class="sk-sec" aria-label="Extrude">
      <div class="sk-row">
        <label class="label sk-grow" for="sk-extrude">Extrude</label>
        <input id="sk-extrude" class="sk-num" type="text" inputMode="decimal" value={text} aria-label="Extrude depth (mm)"
          onInput={(e) => setText(e.currentTarget.value)} onBlur={apply} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); apply(); } }} />
        <span class="faint">mm</span>
      </div>
      {profiles.length ? (
        <>
          <ExtrudePreview profiles={profiles} depth={depth} />
          <div class="faint sk-p" data-profiles>{profiles.length} closed profile{profiles.length === 1 ? "" : "s"}{holes ? ` · ${holes} hole${holes === 1 ? "" : "s"}` : ""}</div>
        </>
      ) : (
        <p class="faint sk-p" data-profiles>No closed profile yet. Close a shape to extrude it.</p>
      )}
    </section>
  );
}

function PhotoBox(): JSX.Element {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [previewOk, setPreviewOk] = useState(true);
  const p = photo.value;
  useEffect(() => setPreviewOk(true), [p?.previewUrl]);

  async function take(file: File | undefined): Promise<void> {
    if (!file) return;
    const r = await readPhoto(file);
    if (r.ok) sketchActions.setPhoto(r.photo);
    else photoError.value = r.error;
  }

  return (
    <section class={`sk-sec sk-photo${over ? " over" : ""}`} aria-label="Photo"
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); void take(e.dataTransfer?.files[0]); }}>
      <div class="label">Photo of a sketch</div>
      {p ? (
        <div class="sk-photo-card">
          {p.previewUrl && previewOk
            ? <img class="sk-photo-img" src={p.previewUrl} alt="Uploaded sketch photo" onError={() => setPreviewOk(false)} />
            : <div class="sk-photo-none faint">No preview for {p.kind.toUpperCase()}; Mises converts it to PNG.</div>}
          <div class="sk-row">
            <span class="dim text sk-grow" data-photo-name>{p.name || "photo"} · {p.kind.toUpperCase()} · {formatBytes(p.size)}</span>
            <Button variant="ghost" onClick={() => sketchActions.setPhoto(null)}>Remove</Button>
          </div>
        </div>
      ) : (
        <p class="faint sk-p">PNG, JPEG or HEIC, max 10 MB. Drop it here.</p>
      )}
      <input ref={input} type="file" class="sk-file" accept="image/png,image/jpeg,image/heic,image/heif,.heic,.heif" aria-label="Choose photo"
        onChange={(e) => { const f = e.currentTarget.files?.[0]; e.currentTarget.value = ""; void take(f); }} />
      <Button onClick={() => input.current?.click()}>{p ? "Replace" : "Choose photo"}</Button>
      {photoError.value && <div class="sk-err" role="alert">{photoError.value}</div>}
    </section>
  );
}

function BuildBox(): JSX.Element {
  const [target, setTarget] = useState<BuildTarget | null>(null);
  const [targetErr, setTargetErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => { const c = actions.client(); return Promise.all([c.getProfile(), c.agents(), c.models()]); })
      .then(([prof, agents, models]) => { if (live) setTarget(buildTarget(prof, agents, models)); },
        (e: unknown) => { if (live) setTargetErr(describeError(e)); });
    return () => { live = false; };
  }, []);

  const b = build.value;
  const d = doc.value;
  const empty = !d.strokes.length && !photo.value;
  const tooMany = d.strokes.length > MAX_STROKES || !withinPointBudget(d);
  const busy = b.state === "working";
  const ready = !!target && !empty && !tooMany && !busy;

  async function start(): Promise<void> {
    if (!ready || !target) return;
    build.value = { state: "working", step: "saving" };
    try {
      const render = d.strokes.length ? await renderPng(d) : null;
      const extrude = closedProfiles(d.strokes).length ? extrudeMm.value : null;
      const { run_id, sketch } = await buildPart(actions.client(),
        { doc: d, notes: notes.value, photo: photo.value, render, agent: target.agent, modelRef: target.modelRef, extrude_mm: extrude },
        (step) => { build.value = { state: "working", step }; });
      build.value = { state: "started", run_id, sketch_id: sketch.sketch_id };
      actions.selectRun(run_id);
      setConsoleCollapsed(false);
    } catch (e) {
      build.value = { state: "error", message: describeError(e) };
    }
  }

  return (
    <section class="sk-sec sk-build" aria-label="Build part">
      <div class="label">Build</div>
      {target ? (
        <div class="dim sk-p" data-build-target>
          {target.agent} · {providerLabel(target.vendor)} {target.modelRef.slice(target.vendor.length + 1)}
        </div>
      ) : (
        <div class="faint sk-p">{targetErr ?? "Loading the default agent…"}</div>
      )}
      {target && photo.value && !target.vision && (
        <div class="sk-note" data-vision-note>This model can't read images. The photo is not sent; strokes, dimensions and notes are.</div>
      )}
      {target?.keyMissing && <div class="sk-note">{providerLabel(target.vendor)} is not set up. Add its key or sign in from Profile.</div>}
      <p class="faint sk-p">The agent asks at most 3 questions in the console.</p>
      <Button variant="ink" onClick={() => void start()} disabled={!ready}>
        {busy ? (b.step === "saving" ? "Sending sketch…" : "Starting run…") : "Build part"}
      </Button>
      {empty && <div class="faint sk-p">Draw something or add a photo first.</div>}
      {tooMany && <div class="sk-err" data-too-many>This sketch has too much detail to send (over {MAX_STROKES} strokes or {MAX_TOTAL_POINTS} points). Delete a few strokes.</div>}
      {b.state === "started" && <div class="dim" role="status">Run started. See the console.</div>}
      {b.state === "error" && <div class="sk-err" role="alert">{b.message}</div>}
    </section>
  );
}
