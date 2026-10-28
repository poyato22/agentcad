// Sketch page state: module-level signals, so a sketch survives switching pages.
import { batch, computed, signal, type Signal } from "@preact/signals";
import { emptyDoc, History, parseDimension, type SketchDoc, type Stroke } from "./model";
import { releasePhoto, type PickedPhoto } from "./photo";

export type Tool = "pen" | "line" | "rect" | "circle" | "select";
export const TOOLS: readonly { id: Tool; label: string; key: string }[] = [
  { id: "pen", label: "Pen", key: "p" },
  { id: "line", label: "Line", key: "l" },
  { id: "rect", label: "Rectangle", key: "r" },
  { id: "circle", label: "Circle", key: "c" },
  { id: "select", label: "Select", key: "v" },
];
export const GRID_STEPS = [1, 5, 10] as const;

export type BuildState =
  | { state: "idle" }
  | { state: "working"; step: "saving" | "starting" }
  | { state: "started"; run_id: string; sketch_id: string }
  | { state: "error"; message: string };

let history = new History(emptyDoc());
let nextId = 1;

export const doc: Signal<SketchDoc> = signal(history.doc);
const version = signal(0);
export const canUndo = computed(() => (version.value, history.canUndo));
export const canRedo = computed(() => (version.value, history.canRedo));
export const tool: Signal<Tool> = signal<Tool>("pen");
export const snapGrid: Signal<boolean> = signal(true);
export const gridStep: Signal<number> = signal(5);
/** Endpoint and 0/45/90° angle snapping. */
export const snapEnds: Signal<boolean> = signal(true);
export const selectedId: Signal<string | null> = signal<string | null>(null);
/** Free-text notes for the agent (data). Not part of undo: the textarea has its own. */
export const notes: Signal<string> = signal("");
export const photo: Signal<PickedPhoto | null> = signal<PickedPhoto | null>(null);
export const photoError: Signal<string | null> = signal<string | null>(null);
export const build: Signal<BuildState> = signal<BuildState>({ state: "idle" });

export const selectedStroke = computed(() => doc.value.strokes.find((s) => s.id === selectedId.value) ?? null);

function commit(next: SketchDoc): void {
  history.push(next);
  batch(() => {
    doc.value = history.doc;
    version.value++;
    if (selectedId.peek() && !history.doc.strokes.some((s) => s.id === selectedId.peek())) selectedId.value = null;
    if (build.peek().state === "started" || build.peek().state === "error") build.value = { state: "idle" };
  });
}

export const sketchActions = {
  newStrokeId(): string { return `s${nextId++}`; },
  addStroke(s: Stroke): void {
    const d = doc.peek();
    commit({ ...d, strokes: [...d.strokes, s] });
    selectedId.value = s.id;
  },
  /** Typed dimension label on a stroke; empty text removes it. */
  setDimension(strokeId: string, text: string): void {
    const d = doc.peek();
    const s = d.strokes.find((x) => x.id === strokeId);
    if (!s) return;
    const current = d.dimensions.find((x) => x.stroke_id === strokeId);
    const parsed = parseDimension(text, s.kind);
    if ((current?.text ?? "") === (parsed?.text ?? "")) return;
    const rest = d.dimensions.filter((x) => x.stroke_id !== strokeId);
    commit({ ...d, dimensions: parsed ? [...rest, { stroke_id: strokeId, ...parsed }] : rest });
  },
  deleteStroke(id: string): void {
    const d = doc.peek();
    if (!d.strokes.some((s) => s.id === id)) return;
    commit({ ...d, strokes: d.strokes.filter((s) => s.id !== id), dimensions: d.dimensions.filter((x) => x.stroke_id !== id) });
  },
  clear(): void {
    const d = doc.peek();
    if (!d.strokes.length) return;
    commit({ ...d, strokes: [], dimensions: [] });
  },
  undo(): void {
    history.undo();
    batch(() => { doc.value = history.doc; version.value++; });
    if (selectedId.peek() && !history.doc.strokes.some((s) => s.id === selectedId.peek())) selectedId.value = null;
  },
  redo(): void {
    history.redo();
    batch(() => { doc.value = history.doc; version.value++; });
  },
  select(id: string | null): void { selectedId.value = id; },
  setTool(t: Tool): void { tool.value = t; },
  setPhoto(p: PickedPhoto | null): void {
    releasePhoto(photo.peek());
    batch(() => {
      photo.value = p;
      photoError.value = null;
    });
  },
};

/** Back to an empty page (tests). */
export function resetSketch(): void {
  releasePhoto(photo.peek());
  history = new History(emptyDoc());
  nextId = 1;
  batch(() => {
    doc.value = history.doc;
    version.value++;
    tool.value = "pen";
    snapGrid.value = true;
    gridStep.value = 5;
    snapEnds.value = true;
    selectedId.value = null;
    notes.value = "";
    photo.value = null;
    photoError.value = null;
    build.value = { state: "idle" };
  });
}
