// Sketch page (design §7.9): draw with pen, mouse or trackpad (freehand, line, rectangle, circle;
// grid, endpoint and angle snapping; typed dimension labels; undo/redo/clear) or upload a photo of a
// sketch, then "Build part" starts a run whose agent gets the sketch as structured data.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { providerLabel } from "../../../lib/names";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { PageShell } from "../PageShell";
import { setConsoleCollapsed } from "../router";
import { buildPart, buildTarget, describeError, type BuildTarget } from "./build";
import { measuredLength, MAX_STROKES, MAX_TOTAL_POINTS, withinPointBudget, type Stroke } from "./model";
import { formatBytes, readPhoto } from "./photo";
import { renderPng } from "./render";
import { SketchCanvas } from "./SketchCanvas";
import {
  build, canRedo, canUndo, doc, GRID_STEPS, gridStep, notes, photo, photoError, selectedStroke, sketchActions, snapEnds, snapGrid, tool,
  TOOLS,
} from "./state";
import "./sketch.css";

const KIND_LABEL: Record<Stroke["kind"], string> = { freehand: "Freehand", line: "Line", rect: "Rectangle", circle: "Circle" };

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
  if (s.kind === "circle" && s.radius_mm) return `${KIND_LABEL.circle} · Ø${(s.radius_mm * 2).toFixed(1)} mm drawn`;
  if (s.kind === "rect") {
    const [a, , c] = s.points;
    return `${KIND_LABEL.rect} · ${Math.abs(c[0] - a[0]).toFixed(1)} × ${Math.abs(c[1] - a[1]).toFixed(1)} mm drawn`;
  }
  return `${KIND_LABEL[s.kind]} · ${measuredLength(s).toFixed(1)} mm drawn`;
}

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
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) sketchActions.redo();
        else sketchActions.undo();
        return;
      }
      if (mod || e.altKey) return;
      if (e.key === "Delete" || e.key === "Backspace") {
        const s = selectedStroke.value;
        if (s) { e.preventDefault(); sketchActions.deleteStroke(s.id); }
        return;
      }
      if (e.key === "Escape") { sketchActions.select(null); return; }
      const t = TOOLS.find((x) => x.key === e.key.toLowerCase());
      if (t) sketchActions.setTool(t.id);
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
          <div class="sk-hint faint">
            {doc.value.strokes.length
              ? `${doc.value.strokes.length} stroke${doc.value.strokes.length === 1 ? "" : "s"} · mm, origin bottom-left`
              : "Draw with a pen, mouse or trackpad, or add a photo."}
          </div>
        </div>
        <SidePanel />
      </div>
    </PageShell>
  );
}

function ToolBar(): JSX.Element {
  return (
    <div class="sk-bar" role="toolbar" aria-label="Sketch tools">
      <div class="sk-group">
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
        <label class="sk-check"><input type="checkbox" checked={snapEnds.value} onChange={(e) => { snapEnds.value = e.currentTarget.checked; }} /> Ends + angles</label>
      </div>
      <div class="sk-group">
        <Button onClick={() => sketchActions.undo()} disabled={!canUndo.value} title="Undo (⌘Z)">Undo</Button>
        <Button onClick={() => sketchActions.redo()} disabled={!canRedo.value} title="Redo (⇧⌘Z)">Redo</Button>
        <Button variant="danger" onClick={() => sketchActions.clear()} disabled={!doc.value.strokes.length}>Clear</Button>
      </div>
    </div>
  );
}

function SidePanel(): JSX.Element {
  return (
    <aside class="sk-side" aria-label="Sketch details">
      <SelectedStroke />
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
  if (!s) {
    return (
      <section class="sk-sec">
        <div class="label">Dimension</div>
        <p class="faint sk-p">Select a stroke (V) and type a size (40, Ø20, R5, 2 in) or a note.</p>
      </section>
    );
  }
  return (
    <section class="sk-sec" data-selected-stroke={s.id}>
      <label class="label" for="sk-dim">Dimension</label>
      <div class="dim sk-p">{strokeSummary(s)}</div>
      {/* keyed: a new stroke or a new saved label starts the field afresh (no effect racing typing) */}
      <DimensionField key={`${s.id}:${dim?.text ?? ""}`} stroke={s} saved={dim?.text ?? ""} />
      {dim && <div class="faint sk-p" data-dim-parsed>{dim.value_mm !== null ? `${dim.kind} · ${dim.value_mm} mm` : dim.kind}</div>}
      <Button variant="ghost" class="sk-del" onClick={() => sketchActions.deleteStroke(s.id)}>Delete</Button>
    </section>
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
      const { run_id, sketch } = await buildPart(actions.client(),
        { doc: d, notes: notes.value, photo: photo.value, render, agent: target.agent, modelRef: target.modelRef },
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
