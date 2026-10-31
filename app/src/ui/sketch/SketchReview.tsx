// The concept sketch, full window, to mark up: Circle (drag from the centre out), Lasso (draw
// around an area), Pin (click). Each new mark opens a small comment box at the mark; the marks
// list on the right keeps every comment editable. Send feedback posts the round to the build's
// lead (core concept/service.submit_feedback: the marks are drawn onto the images it sees).
// Keys: C / L / P pick a tool, Esc closes the comment box, then the review.
import type { JSX } from "preact";
import { createPortal } from "preact/compat";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { ConceptSketch } from "../../api/concept";
import { Button } from "../common/Button";
import { useFocusTrap } from "../common/focusTrap";
import {
  canSend, circleFromDrag, draftId, lassoPath, markAnchor, MARK_WORD, MAX_MARKS, normPoint, simplifyLasso,
  type DraftMark, type MarkShape, type Pt, type Tool,
} from "./marks";
import { SketchImage } from "./SketchImage";
import { approve, closeReview, concepts, draftOf, review, sendFeedback, setDraft, skip } from "./state";
import "./sketch.css";

const TOOLS: { tool: Tool; key: string; hint: string }[] = [
  { tool: "circle", key: "c", hint: "Drag from the centre out" },
  { tool: "lasso", key: "l", hint: "Draw around an area" },
  { tool: "pin", key: "p", hint: "Click a spot" },
];

export function SketchReviewRoot(): JSX.Element | null {
  const r = review.value;
  if (!r) return null;
  const sketch = concepts.value[r.projectId]?.sketches.find((s) => s.sketch_id === r.sketchId);
  if (!sketch) return null;
  return createPortal(<SketchReview projectId={r.projectId} sketch={sketch} image={r.image} />, document.body);
}

type Stroke = { tool: "circle"; a: Pt; b: Pt } | { tool: "lasso"; pts: Pt[] } | null;

function SketchReview({ projectId, sketch, image: initialImage }: { projectId: string; sketch: ConceptSketch; image: number }): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement | null>(null);
  useFocusTrap(root, () => root.current?.querySelector<HTMLElement>("[data-tool='circle']") ?? null);
  const [tool, setTool] = useState<Tool>("circle");
  const [image, setImage] = useState(Math.min(initialImage, sketch.images.length - 1));
  const [stroke, setStroke] = useState<Stroke>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const draft = draftOf(sketch.sketch_id);
  const img = sketch.images[image];
  const w = img.width || 1024;
  const h = img.height || 1024;
  const update = (marks: DraftMark[], comment = draft.comment) => setDraft(sketch.sketch_id, { marks, comment });
  const editMark = draft.marks.find((m) => m.id === editing) ?? null;

  useLayoutEffect(() => {
    const k = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement;
      if (e.key === "Escape") {
        if (e.defaultPrevented) return;
        e.preventDefault();
        if (editing) setEditing(null); else closeReview();
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = TOOLS.find((x) => x.key === e.key.toLowerCase());
      if (t) { e.preventDefault(); setTool(t.tool); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [editing]);

  const pt = (e: PointerEvent): Pt => {
    const r = svg.current?.getBoundingClientRect();
    return r ? normPoint(r, e.clientX, e.clientY) : { x: 0, y: 0 };
  };
  const add = (m: MarkShape) => {
    if (draft.marks.length >= MAX_MARKS) return;
    const mark = { ...m, id: draftId(), image, comment: "" } as DraftMark;
    update([...draft.marks, mark]);
    setEditing(mark.id);
  };
  const down = (e: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    const p = pt(e);
    if (tool === "pin") { add({ kind: "pin", x: p.x, y: p.y }); return; }
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setEditing(null);
    setStroke(tool === "circle" ? { tool, a: p, b: p } : { tool: "lasso", pts: [p] });
  };
  const move = (e: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    if (!stroke) return;
    const p = pt(e);
    setStroke(stroke.tool === "circle" ? { ...stroke, b: p } : { tool: "lasso", pts: [...stroke.pts, p] });
  };
  const up = (e: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    if (!stroke) return;
    const s = stroke.tool === "circle" ? { ...stroke, b: pt(e) } : stroke;
    setStroke(null);
    if (s.tool === "circle") {
      const c = circleFromDrag(s.a, s.b, w, h);
      if (c) add({ kind: "circle", ...c });
    } else {
      const pts = simplifyLasso(s.pts);
      if (pts) add({ kind: "lasso", points: pts });
    }
  };
  // a cancelled pointer never commits its stroke (nor does a later click)
  const cancel = () => setStroke(null);
  const numberedHere = draft.marks.map((m, i) => ({ n: i + 1, mark: m, id: m.id, selected: m.id === editing })).filter((x) => x.mark.image === image);
  const editAt = editMark && editMark.image === image ? markAnchor(editMark, w, h) : null;
  const run = async (f: () => Promise<boolean>, close: boolean) => {
    setBusy(true);
    try { if (await f() && close) closeReview(); } finally { setBusy(false); }
  };
  const sendable = canSend(draft.marks, draft.comment);

  return (
    <div class="sk sk-review" ref={root} role="dialog" aria-modal="true" aria-label={`Mark up concept sketch v${sketch.version}`} data-testid="sketch-review">
      <header class="sk-review-top">
        <Button class="sk-btn sk-quiet" variant="ghost" onClick={closeReview} aria-label="Close (Esc)" data-close>Close</Button>
        <h2 class="sk-review-title">Concept sketch v{sketch.version}</h2>
        {sketch.images.length > 1 && (
          <div class="sk-seg" role="group" aria-label="Views">
            {sketch.images.map((im, i) => {
              const n = draft.marks.filter((m) => m.image === i).length;
              return (
                <button key={im.view} type="button" tabIndex={0} class={i === image ? "is-on" : ""} aria-pressed={i === image} onClick={() => { setImage(i); setEditing(null); }} data-testid="review-view">
                  {im.label}{n > 0 && <span class="sk-count num">{n}</span>}
                </button>
              );
            })}
          </div>
        )}
      </header>
      <div class="sk-review-body">
        <main class="sk-canvas">
          <div class="sk-canvas-inner">
            <SketchImage image={img} callouts={sketch.callouts} marks={numberedHere} alt={`Concept sketch v${sketch.version}, ${img.label}`}
              class="is-review" svgRef={(el) => { svg.current = el; }} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel}
              cursor={tool === "pin" ? "copy" : "crosshair"}>
              {stroke?.tool === "circle" && (() => {
                const c = circleFromDrag(stroke.a, stroke.b, w, h);
                return c ? <circle cx={c.cx * w} cy={c.cy * h} r={c.r * w} class="sk-mark-line is-live" /> : null;
              })()}
              {stroke?.tool === "lasso" && stroke.pts.length > 1 && (
                <path d={lassoPath(stroke.pts.map((p) => [p.x, p.y] as [number, number]), w, h).replace(/ Z$/, "")} class="sk-mark-line is-live" />
              )}
            </SketchImage>
            {editMark && editAt && (
              <CommentBox key={editMark.id} n={draft.marks.indexOf(editMark) + 1} kind={editMark.kind} value={editMark.comment}
                left={editAt.x} top={editAt.y}
                onChange={(v) => update(draft.marks.map((m) => (m.id === editMark.id ? { ...m, comment: v } : m)))}
                onDone={() => setEditing(null)}
                onRemove={() => { update(draft.marks.filter((m) => m.id !== editMark.id)); setEditing(null); }} />
            )}
          </div>
          <div class="sk-tools" role="toolbar" aria-label="Marking tools">
            {TOOLS.map((t) => (
              <button key={t.tool} type="button" tabIndex={0} class={`sk-tool${tool === t.tool ? " is-on" : ""}`} aria-pressed={tool === t.tool}
                title={`${t.hint} (${t.key.toUpperCase()})`} data-tool={t.tool} onClick={() => setTool(t.tool)}>
                <ToolGlyph tool={t.tool} /><span>{MARK_WORD[t.tool]}</span>
              </button>
            ))}
          </div>
        </main>
        <aside class="sk-panel" aria-label="Your marks">
          <h3 class="sk-panel-title">Marks <span class="sk-dim num">{draft.marks.length}</span></h3>
          {draft.marks.length === 0 ? (
            <p class="sk-dim sk-empty">Circle, lasso or pin what should change, and say how.</p>
          ) : (
            <ol class="sk-marklist" data-testid="mark-list">
              {draft.marks.map((m, i) => (
                <li key={m.id} class={`sk-markrow${m.id === editing ? " is-on" : ""}`} data-kind={m.kind}>
                  <button type="button" tabIndex={0} class="sk-markrow-pick" onClick={() => { setImage(m.image); setEditing(m.id); }} aria-label={`Mark ${i + 1}: ${MARK_WORD[m.kind]}`}>
                    <span class="sk-badge num">{i + 1}</span>
                    <span class="sk-markrow-kind">{MARK_WORD[m.kind]}</span>
                    <span class="sk-markrow-view sk-dim">{sketch.images[m.image]?.label}</span>
                  </button>
                  <textarea class="sk-input" rows={2} value={m.comment} placeholder="What should change here?" aria-label={`Comment for mark ${i + 1}`}
                    onInput={(e) => update(draft.marks.map((x) => (x.id === m.id ? { ...x, comment: e.currentTarget.value } : x)))} />
                  <button type="button" tabIndex={0} class="sk-x" aria-label={`Remove mark ${i + 1}`} onClick={() => update(draft.marks.filter((x) => x.id !== m.id))}>×</button>
                </li>
              ))}
            </ol>
          )}
          <label class="sk-label" for="sk-general">General comment</label>
          <textarea id="sk-general" class="sk-input" rows={3} value={draft.comment} placeholder="Anything about the whole design" data-testid="review-comment"
            onInput={(e) => update(draft.marks, e.currentTarget.value)} />
          <div class="sk-panel-foot">
            <Button class="sk-btn sk-wide" variant="primary" disabled={!sendable || busy} onClick={() => void run(() => sendFeedback(projectId, sketch.sketch_id), true)} data-testid="review-send">
              Send feedback
            </Button>
            <div class="sk-panel-row">
              <Button class="sk-btn sk-quiet" variant="ghost" disabled={busy} onClick={() => void run(() => skip(projectId), true)}>Skip sketch</Button>
              <Button class="sk-btn" disabled={busy} onClick={() => void run(() => approve(projectId, sketch.sketch_id), true)} data-testid="review-approve">Approve v{sketch.version}</Button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function CommentBox({ n, kind, value, left, top, onChange, onDone, onRemove }: {
  n: number; kind: Tool; value: string; left: number; top: number;
  onChange: (v: string) => void; onDone: () => void; onRemove: () => void;
}): JSX.Element {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  // keep the box on the sheet: flip left / up near the right and bottom edges
  const style = {
    left: `${Math.min(left, 0.7) * 100}%`, top: `${Math.min(top, 0.78) * 100}%`,
    transform: `translate(${left > 0.7 ? "-100%" : "14px"}, ${top > 0.78 ? "-100%" : "14px"})`,
  };
  return (
    <div class="sk-comment" style={style} data-testid="mark-comment">
      <div class="sk-comment-head"><span class="sk-badge num">{n}</span>{MARK_WORD[kind]}</div>
      <textarea ref={ref} class="sk-input" rows={2} value={value} placeholder="What should change here?" aria-label={`Comment for mark ${n}`}
        onInput={(e) => onChange(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onDone(); } }} />
      <div class="sk-comment-foot">
        <button type="button" tabIndex={0} class="sk-link" onClick={onRemove}>Remove</button>
        <Button class="sk-btn" onClick={onDone} data-testid="mark-done">Done</Button>
      </div>
    </div>
  );
}

function ToolGlyph({ tool }: { tool: Tool }): JSX.Element {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" class="sk-glyph">
      {tool === "circle" && <circle cx="8" cy="8" r="5.5" />}
      {tool === "lasso" && <path d="M3.5 9.5c-1.5-3 1.5-6.5 5-6 3.5.5 5 3.5 3.5 6s-5 3-7 1.5" />}
      {tool === "pin" && <><circle cx="8" cy="6" r="3" /><path d="M8 9v5" /></>}
    </svg>
  );
}
