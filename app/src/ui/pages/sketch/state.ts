// Sketch page state: module-level signals, so a sketch survives switching pages. Every edit goes
// through the solver (solver.ts) before it is committed, so the geometry always satisfies the
// constraints; an edit that can't (a conflicting dimension) is refused with a message.
import { batch, computed, signal, type Signal } from "@preact/signals";
import type { SketchConstraint, SketchConstraintKind } from "../../../api/types";
import { dist, round6, type Pt } from "./geom";
import { parseTyped } from "./input";
import { emptyDoc, History, parseDimension, SHEET, type SketchDoc, type Stroke } from "./model";
import { releasePhoto, type PickedPhoto } from "./photo";
import type { GuideKind, Snap } from "./snap";
import { checkConstraint, edgeCount, hasRadius, refKey, solvable, solve, type Ref, type SolveResult } from "./solver";
import { axisOf, mirror as mirrorStroke, moveVertex, offset as offsetStroke, setCorner, toggleConstruction, translate } from "./edit";
import { template, type TemplateId } from "./templates";
import { clicksFor, draftShape, isDrawTool, TOOLS, typedStep, type Drive, type DrawTool, type Tool } from "./tools";
import { DEFAULT_VIEW, fitBox, type View } from "./view";

export { TOOLS, type Tool };
export const GRID_STEPS = [0.5, 1, 2, 5, 10] as const;

export type BuildState =
  | { state: "idle" }
  | { state: "working"; step: "saving" | "starting" }
  | { state: "started"; run_id: string; sketch_id: string }
  | { state: "error"; message: string };

/** A selected thing: a stroke (lines and rectangles by an edge), or one of its vertices. */
export type Pick = Ref;

/** A stroke being drawn: the clicks so far, what each snapped to, and the typed value. */
export interface Draft { tool: DrawTool; clicks: Pt[]; snaps: ClickSnap[] }
export interface ClickSnap { ref?: Ref; guide?: GuideKind }

let history = new History(emptyDoc());
let nextId = 1;
let nextConstraint = 1;

export const doc: Signal<SketchDoc> = signal(history.doc);
const version = signal(0);
export const canUndo = computed(() => (version.value, history.canUndo));
export const canRedo = computed(() => (version.value, history.canRedo));
export const tool: Signal<Tool> = signal<Tool>("pen");
export const snapGrid: Signal<boolean> = signal(true);
export const gridStep: Signal<number> = signal(5);
/** Point snaps (endpoints, centres, midpoints, intersections). */
export const snapEnds: Signal<boolean> = signal(true);
/** Inference guides (horizontal / vertical / 45° / parallel / perpendicular / tangent / aligned). */
export const snapGuides: Signal<boolean> = signal(true);
export const picks: Signal<readonly Pick[]> = signal<readonly Pick[]>([]);
export const selectedConstraint: Signal<string | null> = signal<string | null>(null);
/** The primary selected stroke (the last pick). */
export const selectedId = computed(() => picks.value.at(-1)?.stroke_id ?? null);
export const selectedIds = computed(() => [...new Set(picks.value.map((p) => p.stroke_id))]);
export const draft: Signal<Draft | null> = signal<Draft | null>(null);
export const cursor: Signal<Pt | null> = signal<Pt | null>(null);
export const lastSnap: Signal<Snap | null> = signal<Snap | null>(null);
/** The measurements box: what was typed while drawing. */
export const typed: Signal<string> = signal("");
/** A drag in progress (vertex or move): shown instead of the committed doc until released. */
export const preview: Signal<SketchDoc | null> = signal<SketchDoc | null>(null);
export const view: Signal<View> = signal<View>(DEFAULT_VIEW);
export const filletR: Signal<number> = signal(3);
export const extrudeMm: Signal<number> = signal(5);
/** A short status line: why an edit was refused, what a typed value did. */
export const message: Signal<string | null> = signal<string | null>(null);
/** Free-text notes for the agent (data). Not part of undo: the textarea has its own. */
export const notes: Signal<string> = signal("");
export const photo: Signal<PickedPhoto | null> = signal<PickedPhoto | null>(null);
export const photoError: Signal<string | null> = signal<string | null>(null);
export const build: Signal<BuildState> = signal<BuildState>({ state: "idle" });

export const shown = computed(() => preview.value ?? doc.value);
export const selectedStroke = computed(() => doc.value.strokes.find((s) => s.id === selectedId.value) ?? null);
export const selectedStrokes = computed(() => {
  const ids = new Set(selectedIds.value);
  return doc.value.strokes.filter((s) => ids.has(s.id));
});

function prune(d: SketchDoc): void {
  const ids = new Set(d.strokes.map((s) => s.id));
  const p = picks.peek();
  if (p.some((x) => !ids.has(x.stroke_id))) picks.value = p.filter((x) => ids.has(x.stroke_id));
  const c = selectedConstraint.peek();
  if (c && !d.constraints.some((x) => x.id === c)) selectedConstraint.value = null;
}

function commit(next: SketchDoc): void {
  history.push(next);
  batch(() => {
    doc.value = history.doc;
    version.value++;
    prune(history.doc);
    if (build.peek().state === "started" || build.peek().state === "error") build.value = { state: "idle" };
  });
}

/** Constraints that still point at existing geometry. */
function live(strokes: readonly Stroke[], cs: readonly SketchConstraint[]): SketchConstraint[] {
  return cs.filter((c) => checkConstraint(c.kind, c.refs, strokes) === null);
}

/** Commits and drags get this long to solve (ms); drags warm-start from the last good preview. */
const COMMIT_BUDGET_MS = 400;
const DRAG_BUDGET_MS = 60;

const KIND_WORDS: Record<SketchConstraintKind, string> = {
  horizontal: "horizontal", vertical: "vertical", equal: "equal", coincident: "coincident", distance: "length",
  diameter: "diameter", radius: "radius", distance_x: "x offset", distance_y: "y offset", point_on_circle: "point on circle",
};

/** "the 40 mm length" / "a rectangle's own sides" — the constraint a failed solve names. */
export function describeConstraint(id: string | null, cs: readonly SketchConstraint[]): string {
  if (!id) return "a constraint";
  if (id.startsWith("rect:")) return "a rectangle's square corners";
  const c = cs.find((x) => x.id === id);
  if (!c) return "a constraint";
  return `the ${c.value_mm !== undefined ? `${c.value_mm} mm ` : ""}${KIND_WORDS[c.kind]} constraint`;
}

/** Why a solve isn't ok, in words (null when it is). */
export function solveMessage(res: SolveResult, cs: readonly SketchConstraint[]): string | null {
  if (res.status === "ok") return null;
  const off = `${res.residual < 0.01 ? res.residual.toExponential(1) : res.residual.toFixed(2)} mm`;
  if (res.status === "invalid") return `Can't solve that: ${res.reason ?? "something isn't a number"}.`;
  if (res.status === "conflict") return `That conflicts with ${describeConstraint(res.worst, cs)} (off by ${off}).`;
  return `Couldn't solve that in time (still off by ${off}); try fewer constraints at once.`;
}

/** Strokes an edit touched: changed or new ones, and those any new or changed constraint refers
 *  to. Only their connected groups are solved (the rest of the sketch already holds). */
export function touchedBy(prev: SketchDoc, strokes: readonly Stroke[], cs: readonly SketchConstraint[], also: Iterable<string> = []): Set<string> {
  const before = new Map(prev.strokes.map((s) => [s.id, s]));
  const out = new Set(strokes.filter((s) => before.get(s.id) !== s).map((s) => s.id));
  const old = new Set(prev.constraints);
  for (const c of cs) if (!old.has(c)) for (const r of c.refs) out.add(r.stroke_id);
  for (const id of also) out.add(id);
  return out;
}

/** Solve and commit; false (and nothing changes, with the reason shown) unless the solve is ok. */
function commitSolved(d: SketchDoc, fixed: ReadonlySet<string> = new Set(), refusal?: string): boolean {
  const cs = live(d.strokes, d.constraints);
  const held = [...fixed].map((k) => k.split(":")[0]);
  const res = solve(d.strokes, cs, fixed, { budgetMs: COMMIT_BUDGET_MS, touched: touchedBy(doc.peek(), d.strokes, cs, held) });
  if (!res.ok) { message.value = res.status === "invalid" ? solveMessage(res, cs) : refusal ?? solveMessage(res, cs); return false; }
  commit({ ...d, strokes: res.strokes, constraints: cs });
  return true;
}

/** Keeps a typed point inside the core's coordinate range (core sketch.COORD_LIMIT). */
export const COORD_LIMIT = 100_000;
const inRange = (p: Pt) => Math.abs(p[0]) <= COORD_LIMIT && Math.abs(p[1]) <= COORD_LIMIT;

function newConstraintId(): string {
  return `k${nextConstraint++}`;
}

function driveConstraints(stroke: Stroke, drive: readonly Drive[]): SketchConstraint[] {
  return drive.map((dv): SketchConstraint => {
    if (dv.kind === "distance") {
      const refs: Ref[] = "edge" in dv ? [{ stroke_id: stroke.id, edge: dv.edge }] : [{ stroke_id: stroke.id, vertex: dv.vertices[0] }, { stroke_id: stroke.id, vertex: dv.vertices[1] }];
      return { id: newConstraintId(), kind: "distance", refs, value_mm: round6(dv.value) };
    }
    return { id: newConstraintId(), kind: dv.kind, refs: [{ stroke_id: stroke.id }], value_mm: round6(dv.value) };
  });
}

/** Constraints a finished stroke picks up from how it was drawn: its ends on existing points
 *  (coincident), its segments along a horizontal / vertical guide. */
function drawnConstraints(s: Stroke, d: Draft): SketchConstraint[] {
  const out: SketchConstraint[] = [];
  const vertexOfClick = (i: number): number | null => {
    if (s.kind === "line" || s.kind === "polyline") return i;
    if (s.kind === "arc") return i === 0 ? 0 : i === 1 ? 2 : null;
    if (s.kind === "slot") return i < 2 ? i : null;
    if (s.kind === "circle") return i === 0 ? 0 : null;
    return null;
  };
  d.snaps.forEach((sn, i) => {
    const v = vertexOfClick(i);
    if (sn.ref && v !== null && sn.ref.vertex !== undefined) {
      out.push({ id: newConstraintId(), kind: "coincident", refs: [{ stroke_id: s.id, vertex: v }, { ...sn.ref }] });
    }
    if ((s.kind === "line" || s.kind === "polyline") && i > 0 && (sn.guide === "horizontal" || sn.guide === "vertical")) {
      out.push({ id: newConstraintId(), kind: sn.guide, refs: [{ stroke_id: s.id, edge: i - 1 }] });
    }
  });
  return out;
}

function finishStroke(s: Stroke, d: Draft, drive: readonly Drive[] = []): void {
  const stroke = { ...s, id: sketchActions.newStrokeId() };
  const cur = doc.peek();
  const extra = [...drawnConstraints(stroke, d), ...driveConstraints(stroke, drive)];
  const next: SketchDoc = { ...cur, strokes: [...cur.strokes, stroke], constraints: [...cur.constraints, ...extra] };
  // the new stroke's own clicks stay put; the solver moves nothing it doesn't have to
  if (!commitSolved(next, new Set([...extra.flatMap((c) => c.refs.filter((r) => r.stroke_id !== stroke.id).map(refKey))]))) {
    // drawn constraints that can't hold are dropped rather than losing the stroke
    commit({ ...cur, strokes: [...cur.strokes, stroke] });
  }
  batch(() => {
    picks.value = [defaultPick(stroke)];
    selectedConstraint.value = null;
    draft.value = null;
    typed.value = "";
  });
}

function defaultPick(s: Stroke): Pick {
  return edgeCount(s) > 0 ? { stroke_id: s.id, edge: 0 } : { stroke_id: s.id };
}

/** The dimensions a typed value sets, for the click it stands for. */
let drivenDrive: Drive[] = [];
/** Typed dimensions waiting for the stroke they belong to (multi-click tools). */
let pendingDrive: Drive[] = [];

export const sketchActions = {
  newStrokeId(): string { return `s${nextId++}`; },
  addStroke(s: Stroke): void {
    const d = doc.peek();
    commit({ ...d, strokes: [...d.strokes, s] });
    picks.value = [defaultPick(s)];
  },

  // ---- drawing (click by click; a drag counts as two clicks)

  /** A click with the current draw tool at a snapped point. */
  drawClick(p: Pt, snap: ClickSnap = {}): void {
    const t = tool.peek();
    if (!isDrawTool(t)) return;
    const d = draft.peek();
    if (!d || d.tool !== t) {
      batch(() => { draft.value = { tool: t, clicks: [p], snaps: [snap] }; typed.value = ""; message.value = null; });
      return;
    }
    if (t === "polyline") {
      const first = d.clicks[0];
      const last = d.clicks[d.clicks.length - 1];
      if (dist(p, last) < 1e-6) { sketchActions.finishDraft(); return; }      // double click
      if (d.clicks.length >= 3 && dist(p, first) < 1e-6) { sketchActions.finishDraft(true); return; }
    }
    const next: Draft = { ...d, clicks: [...d.clicks, p], snaps: [...d.snaps, snap] };
    const drive = drivenDrive;
    drivenDrive = [];
    if (next.clicks.length < clicksFor(t)) {
      batch(() => { draft.value = next; typed.value = ""; });
      pendingDrive = [...pendingDrive, ...drive];
      return;
    }
    const s = draftShape(t, next.clicks.slice(0, -1), next.clicks[next.clicks.length - 1], "draft", null);
    const all = [...pendingDrive, ...drive];
    pendingDrive = [];
    if (s) finishStroke(s, next, all);
    else batch(() => { draft.value = null; typed.value = ""; });
  },
  /** Ends a polyline (closing it when `close`); other tools just cancel. */
  finishDraft(close = false): void {
    const d = draft.peek();
    if (!d) return;
    if (d.tool === "polyline" && d.clicks.length >= 2) {
      const s: Stroke = { id: "draft", kind: "polyline", points: d.clicks.map((p) => [p[0], p[1]]), closed: close && d.clicks.length >= 3, label: "" };
      const drive = pendingDrive;
      pendingDrive = [];
      finishStroke(s, d, drive);
      return;
    }
    sketchActions.cancelDraft();
  },
  cancelDraft(): void {
    pendingDrive = [];
    drivenDrive = [];
    batch(() => { draft.value = null; typed.value = ""; });
  },
  /** The measurements box. */
  setTyped(text: string): void { typed.value = text; },
  /** Enter in the measurements box: the typed value as the next click (with its dimension). */
  commitTyped(): boolean {
    const v = parseTyped(typed.peek());
    if (!v) { if (typed.peek()) message.value = `Can't use "${typed.peek()}" here.`; return false; }
    if (tool.peek() === "fillet") {
      const r = v.kind === "length" || v.kind === "radius" ? v.mm : v.kind === "diameter" ? v.mm / 2 : NaN;
      if (!Number.isFinite(r)) { message.value = "Type a fillet radius, e.g. 3."; return false; }
      batch(() => { filletR.value = r; typed.value = ""; message.value = `Fillet radius ${r} mm.`; });
      return true;
    }
    const d = draft.peek();
    if (!d) {
      // no click yet: "x,y" places the first point (drawing by keyboard)
      if (v.kind !== "pair" || !isDrawTool(tool.peek())) { message.value = "Type the first point as x,y."; return false; }
      if (!inRange([v.a, v.b])) { message.value = OUT_OF_RANGE; return false; }
      typed.value = "";
      message.value = null;
      sketchActions.drawClick([v.a, v.b], {});
      return true;
    }
    const step = typedStep(d.tool, d.clicks, cursor.peek() ?? d.clicks[d.clicks.length - 1], v);
    if (!step) { message.value = `Can't use "${typed.peek()}" here.`; return false; }
    if (!inRange(step.click) || step.drive.some((x) => x.value > COORD_LIMIT)) { message.value = OUT_OF_RANGE; return false; }
    drivenDrive = step.drive;
    message.value = null;
    sketchActions.drawClick(step.click, {});
    return true;
  },

  // ---- selection

  /** Selects a stroke (replacing the selection unless `add`); null clears it. */
  select(id: string | null, add = false, pick?: Pick): void {
    batch(() => {
      selectedConstraint.value = null;
      if (id === null) { picks.value = []; return; }
      const s = doc.peek().strokes.find((x) => x.id === id);
      if (!s) return;
      const p = pick ?? defaultPick(s);
      const cur = picks.peek();
      if (!add) { picks.value = [p]; return; }
      const k = refKey(p);
      picks.value = cur.some((x) => refKey(x) === k) ? cur.filter((x) => refKey(x) !== k) : [...cur, p];
    });
  },
  selectConstraint(id: string | null): void {
    batch(() => { selectedConstraint.value = id; if (id) picks.value = []; });
  },
  setTool(t: Tool): void {
    batch(() => {
      if (draft.peek() && draft.peek()?.tool !== t) sketchActions.cancelDraft();
      tool.value = t;
      message.value = null;
    });
  },

  // ---- editing

  /** Typed dimension label on a stroke; empty text removes it. A length on a line, a diameter or
   *  radius on a circle / arc / slot also drives the geometry. */
  setDimension(strokeId: string, text: string): void {
    const d = doc.peek();
    const s = d.strokes.find((x) => x.id === strokeId);
    if (!s) return;
    const current = d.dimensions.find((x) => x.stroke_id === strokeId);
    const parsed = parseDimension(text, s.kind === "arc" || s.kind === "slot" ? "circle" : s.kind);
    if ((current?.text ?? "") === (parsed?.text ?? "")) return;
    const rest = d.dimensions.filter((x) => x.stroke_id !== strokeId);
    const driveId = `dim-${strokeId}`;
    const others = d.constraints.filter((c) => c.id !== driveId);
    let drive: SketchConstraint | null = null;
    if (parsed?.value_mm) {
      if (s.kind === "line" && parsed.kind === "length") drive = { id: driveId, kind: "distance", refs: [{ stroke_id: s.id, edge: 0 }], value_mm: parsed.value_mm };
      else if (hasRadius(s) && (parsed.kind === "diameter" || parsed.kind === "radius")) drive = { id: driveId, kind: parsed.kind, refs: [{ stroke_id: s.id }], value_mm: parsed.value_mm };
    }
    // a new size replaces the one already driving the same thing (e.g. the length typed while drawing)
    const sameTarget = (c: SketchConstraint) => !!drive && c.refs.length === 1 && c.refs[0].stroke_id === s.id
      && (drive.kind === "distance" ? c.kind === "distance" && c.refs[0].edge === 0 : c.kind === "diameter" || c.kind === "radius");
    const kept = others.filter((c) => !sameTarget(c));
    const next: SketchDoc = { ...d, dimensions: parsed ? [...rest, { stroke_id: strokeId, ...parsed }] : rest, constraints: drive ? [...kept, drive] : others };
    // a line grows from its start; a circle about its centre
    if (!commitSolved(next, new Set([`${s.id}:v0`]), "That size conflicts with the constraints; kept as a label.")) {
      commit({ ...next, constraints: others });
    }
  },
  /** A driving length on an edge (null removes it). */
  setEdgeLength(strokeId: string, edge: number, value: number | null): void {
    const d = doc.peek();
    const id = `len-${strokeId}-e${edge}`;
    const others = d.constraints.filter((c) => c.id !== id);
    if (value === null || !(value > 0)) { if (others.length !== d.constraints.length) commitSolved({ ...d, constraints: others }); return; }
    const c: SketchConstraint = { id, kind: "distance", refs: [{ stroke_id: strokeId, edge }], value_mm: round6(value) };
    commitSolved({ ...d, constraints: [...others, c] }, new Set([`${strokeId}:v${edge}`]));
  },
  /** Adds a constraint on the given refs (default: the picks); returns why it can't, or null. */
  addConstraint(kind: SketchConstraintKind, refs: readonly Ref[] = picks.peek(), value?: number): string | null {
    const d = doc.peek();
    const why = checkConstraint(kind, refs, d.strokes);
    if (why) { message.value = why; return why; }
    const c: SketchConstraint = value === undefined ? { id: newConstraintId(), kind, refs: refs.map((r) => ({ ...r })) } : { id: newConstraintId(), kind, refs: refs.map((r) => ({ ...r })), value_mm: round6(value) };
    if (!commitSolved({ ...d, constraints: [...d.constraints, c] }, new Set([refKey(refs[0])]))) return message.peek();
    batch(() => { selectedConstraint.value = c.id; picks.value = []; message.value = null; });
    return null;
  },
  /** Changes a dimension constraint's value. */
  setConstraintValue(id: string, value: number): void {
    const d = doc.peek();
    const c = d.constraints.find((x) => x.id === id);
    if (!c || c.value_mm === undefined || !(value > 0) || c.value_mm === value) return;
    const anchor = c.refs[0].edge !== undefined ? `${c.refs[0].stroke_id}:v${c.refs[0].edge}` : refKey(c.refs[0]);
    commitSolved({ ...d, constraints: d.constraints.map((x) => (x.id === id ? { ...x, value_mm: round6(value) } : x)) }, new Set([anchor]));
  },
  removeConstraint(id: string): void {
    const d = doc.peek();
    if (!d.constraints.some((c) => c.id === id)) return;
    commit({ ...d, constraints: d.constraints.filter((c) => c.id !== id), dimensions: id.startsWith("dim-") ? d.dimensions.filter((x) => `dim-${x.stroke_id}` !== id) : d.dimensions });
  },
  deleteStroke(id: string): void { sketchActions.deleteStrokes([id]); },
  deleteStrokes(ids: readonly string[]): void {
    const d = doc.peek();
    const gone = new Set(ids);
    if (!d.strokes.some((s) => gone.has(s.id))) return;
    const strokes = d.strokes.filter((s) => !gone.has(s.id));
    commit({
      ...d, strokes,
      dimensions: d.dimensions.filter((x) => !gone.has(x.stroke_id)),
      constraints: d.constraints.filter((c) => c.refs.every((r) => !gone.has(r.stroke_id))),
    });
  },
  /** Delete key: the selected strokes, else the selected constraint. */
  deleteSelection(): boolean {
    const ids = selectedIds.peek();
    if (ids.length) { sketchActions.deleteStrokes(ids); return true; }
    const c = selectedConstraint.peek();
    if (c) { sketchActions.removeConstraint(c); return true; }
    return false;
  },
  clear(): void {
    const d = doc.peek();
    if (!d.strokes.length) return;
    commit({ ...d, strokes: [], dimensions: [], constraints: [] });
  },
  undo(): void {
    history.undo();
    batch(() => { doc.value = history.doc; version.value++; prune(history.doc); preview.value = null; });
  },
  redo(): void {
    history.redo();
    batch(() => { doc.value = history.doc; version.value++; prune(history.doc); preview.value = null; });
  },

  // ---- drags (shown as a preview; one undo step when released)

  /** Drag a vertex to p, solving with it held. Only a solve that holds is shown; otherwise the last
   *  good shape stays and the hint bar says why. Warm-starts from the last good preview. */
  dragVertex(ref: Ref, p: Pt): boolean {
    const d = doc.peek();
    const start = preview.peek() ?? d;
    const s = start.strokes.find((x) => x.id === ref.stroke_id);
    if (!s || !solvable(s) || ref.vertex === undefined) return false;
    const moved = start.strokes.map((x) => (x.id === s.id ? moveVertex(x, ref.vertex as number, p) : x));
    return showDrag(d, moved, new Set([refKey(ref)]));
  },
  /** Drag the selected strokes by d (mm, from where they were when the drag began). */
  dragMove(by: Pt): boolean {
    const d = doc.peek();
    const ids = new Set(selectedIds.peek());
    if (!ids.size) return false;
    const base = preview.peek() ?? d;
    // other strokes warm-start from the last good preview; the moved ones go where the pointer says
    const moved = base.strokes.map((x) => (ids.has(x.id) ? translate(d.strokes.find((o) => o.id === x.id) ?? x, by) : x));
    return showDrag(d, moved, ids);
  },
  /** Release: commits the last good preview (one undo step); a drag that never solved commits nothing. */
  endDrag(keep: boolean): void {
    const p = preview.peek();
    preview.value = null;
    if (keep && p && p !== doc.peek()) commit(p);
  },
  /** Arrow keys: nudge the picked vertex (else the selected shapes) by (dx, dy) mm. */
  nudge(dx: number, dy: number): boolean {
    const v = picks.peek().at(-1);
    const d = doc.peek();
    let ok = false;
    if (v?.vertex !== undefined) {
      const s = d.strokes.find((x) => x.id === v.stroke_id);
      const q = s ? (s.kind === "circle" ? s.center : s.points[v.vertex]) : undefined;
      if (q) ok = sketchActions.dragVertex(v, [q[0] + dx, q[1] + dy]);
    } else ok = sketchActions.dragMove([dx, dy]);
    sketchActions.endDrag(ok);
    return ok;
  },
  /** Backspace while drawing: drop the last click (the whole draft when it was the first). */
  undoClick(): void {
    const d = draft.peek();
    if (!d) return;
    if (d.clicks.length <= 1) { sketchActions.cancelDraft(); return; }
    const clicks = d.clicks.slice(0, -1);
    pendingDrive = pendingDrive.filter((x) => !("edge" in x) || x.edge < clicks.length - 1);
    draft.value = { ...d, clicks, snaps: d.snaps.slice(0, -1) };
  },
  /** Fillet the picked corner with the current radius (keyboard path of the fillet tool). */
  filletPicked(): boolean {
    const v = picks.peek().at(-1);
    if (v?.vertex === undefined) { message.value = "Pick a corner first."; return false; }
    return sketchActions.filletCorner(v);
  },

  /** Mirror the selection about the selected line (a construction line when there is one). */
  mirrorSelection(): string | null {
    const sel = selectedStrokes.peek();
    const axisStroke = sel.find((s) => s.construction && axisOf(s)) ?? sel.find((s) => axisOf(s) && s.id === selectedId.peek()) ?? sel.find((s) => axisOf(s));
    const axis = axisStroke ? axisOf(axisStroke) : null;
    const rest = sel.filter((s) => s !== axisStroke);
    if (!axis || !rest.length) { message.value = "Select the shapes and a line to mirror about."; return message.peek(); }
    const d = doc.peek();
    const copies = rest.map((s) => mirrorStroke(s, axis[0], axis[1], sketchActions.newStrokeId()));
    commit({ ...d, strokes: [...d.strokes, ...copies] });
    batch(() => { picks.value = copies.map(defaultPick); message.value = `Mirrored ${copies.length} shape${copies.length === 1 ? "" : "s"}.`; });
    return null;
  },
  /** Offset copies of the selection by mm (outward; negative goes inward). */
  offsetSelection(mm: number): string | null {
    const sel = selectedStrokes.peek();
    const copies = sel.map((s) => offsetStroke(s, mm, sketchActions.newStrokeId())).filter((x): x is Stroke => !!x);
    if (!copies.length) { message.value = "Select a line, shape or circle to offset."; return message.peek(); }
    const d = doc.peek();
    commit({ ...d, strokes: [...d.strokes, ...copies] });
    batch(() => { picks.value = copies.map(defaultPick); message.value = null; });
    return null;
  },
  /** Fillet a polyline / rectangle corner with the current radius (0 removes the fillet). */
  filletCorner(ref: Ref, r = filletR.peek()): boolean {
    const d = doc.peek();
    const s = d.strokes.find((x) => x.id === ref.stroke_id);
    const next = s && ref.vertex !== undefined ? setCorner(s, ref.vertex, r) : null;
    if (!next || !s) { message.value = "Click a corner of a polyline or rectangle."; return false; }
    commit({ ...d, strokes: d.strokes.map((x) => (x.id === s.id ? next : x)) });
    picks.value = [{ stroke_id: s.id, vertex: ref.vertex }];
    return true;
  },
  toggleConstruction(): void {
    const ids = new Set(selectedIds.peek());
    if (!ids.size) return;
    const d = doc.peek();
    commit({ ...d, strokes: d.strokes.map((s) => (ids.has(s.id) ? toggleConstruction(s) : s)) });
  },
  insertTemplate(id: TemplateId): void {
    const t = template(id, () => sketchActions.newStrokeId(), newConstraintId);
    const d = doc.peek();
    const strokes = placeBeside(d.strokes, t.strokes);
    if (commitSolved({ ...d, strokes: [...d.strokes, ...strokes], constraints: [...d.constraints, ...t.constraints] })) {
      batch(() => {
        extrudeMm.value = t.extrude_mm;
        picks.value = [];
        sketchActions.fitView();
      });
    }
  },

  // ---- view

  setView(v: View): void { view.value = v; },
  fitView(): void {
    const s = doc.peek().strokes;
    if (!s.length) { view.value = DEFAULT_VIEW; return; }
    const xs = s.flatMap((x) => x.points.map((p) => p[0]));
    const ys = s.flatMap((x) => x.points.map((p) => p[1]));
    view.value = fitBox([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], DEFAULT_VIEW);
  },

  setPhoto(p: PickedPhoto | null): void {
    releasePhoto(photo.peek());
    batch(() => {
      photo.value = p;
      photoError.value = null;
    });
  },
};

const OUT_OF_RANGE = `That's outside the drawing range (±${COORD_LIMIT.toLocaleString("en-US")} mm).`;

/** Shows a drag's solve when it holds; keeps the last good preview (and says why) when not. */
function showDrag(d: SketchDoc, moved: Stroke[], fixed: ReadonlySet<string>): boolean {
  const cs = live(moved, d.constraints);
  // only the dragged strokes' group is solved; everything else already holds
  const res = solve(moved, cs, fixed, { budgetMs: DRAG_BUDGET_MS, touched: touchedBy(d, moved, cs) });
  if (!res.ok) { message.value = solveMessage(res, cs); return false; }
  batch(() => { preview.value = { ...d, strokes: res.strokes }; if (message.peek()) message.value = null; });
  return true;
}

type BBox = [number, number, number, number];
function bbox(strokes: readonly Stroke[]): BBox | null {
  const pts = strokes.flatMap((s) => s.points);
  if (!pts.length) return null;
  return [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
}

/** New strokes moved clear of the existing ones (to the right, else above) when they would overlap. */
export function placeBeside(existing: readonly Stroke[], added: readonly Stroke[], gap = 10): Stroke[] {
  const a = bbox(existing);
  const b = bbox(added);
  if (!a || !b || a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]) return [...added];
  const right = a[2] + gap - b[0];
  const up = a[3] + gap - b[1];
  const by: Pt = b[2] + right <= SHEET.width_mm ? [right, 0] : b[3] + up <= SHEET.height_mm ? [0, up] : [right, 0];
  return added.map((s) => translate(s, by));
}

/** Back to an empty page (tests). */
export function resetSketch(): void {
  releasePhoto(photo.peek());
  history = new History(emptyDoc());
  nextId = 1;
  nextConstraint = 1;
  pendingDrive = [];
  drivenDrive = [];
  batch(() => {
    doc.value = history.doc;
    version.value++;
    tool.value = "pen";
    snapGrid.value = true;
    gridStep.value = 5;
    snapEnds.value = true;
    snapGuides.value = true;
    picks.value = [];
    selectedConstraint.value = null;
    draft.value = null;
    cursor.value = null;
    lastSnap.value = null;
    typed.value = "";
    preview.value = null;
    view.value = DEFAULT_VIEW;
    filletR.value = 3;
    extrudeMm.value = 5;
    message.value = null;
    notes.value = "";
    photo.value = null;
    photoError.value = null;
    build.value = { state: "idle" };
  });
}
