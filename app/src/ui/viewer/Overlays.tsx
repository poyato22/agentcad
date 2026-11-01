// The viewer's HTML overlays (Plan 7 U2a): the selection label at a click, the measure panel,
// the comment box and the feedback pins on the model. Placed in viewer pixels; pins follow the
// camera through the scene's onView.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { MeasureValue } from "../../api/measure";
import type { Vec3 } from "../../api/types";
import { fmtValueOnly, labelFor, shownValues, type Picked } from "./interact";
import type { Pin } from "./pins";

type At = { x: number; y: number };
/** The part of the sheet a box may use: x0 → w (FW-layout: clear of the floating surfaces). */
type Bounds = { w: number; h: number; x0?: number };

/** Keep a box of w × h near `at` (right-below it) inside the sheet. */
function place(at: At, w: number, h: number, b: Bounds): { left: string; top: string } {
  const x0 = (b.x0 ?? 0) + 8;
  const x = Math.min(Math.max(x0, at.x + 14), Math.max(x0, b.w - w - 8));
  const y = Math.min(Math.max(8, at.y + 14), Math.max(8, b.h - h - 8));
  return { left: `${x}px`, top: `${y}px` };
}

/** What the click hit, in words; "Modify…" opens the feature ops on it. */
export function SelectionLabel({ picked, at, bounds, canModify, onModify, onClose }: {
  picked: Picked; at: At; bounds: Bounds; canModify: boolean; onModify(): void; onClose(): void;
}): JSX.Element {
  return (
    <div class="vx-label" style={place(at, 260, 30, bounds)} data-testid="pick-label" role="status">
      <span class="vx-label-text">{labelFor(picked)}</span>
      {canModify && <button type="button" class="vx-link" onClick={onModify} title="Add a hole, shell, fillet or chamfer here">Modify…</button>}
      <button type="button" class="vx-x" aria-label="Clear the selection" onClick={onClose}>×</button>
    </div>
  );
}

export interface MeasureState { status: "idle" | "busy" | "done" | "error"; values: MeasureValue[]; error: string | null }

/** Measure: the picks and their values (exact, from the core; the metrics' own until it answers). */
export function MeasurePanel({ picks, state, onClear, onClose }: { picks: readonly Picked[]; state: MeasureState; onClear(): void; onClose(): void }): JSX.Element {
  const values = shownValues(state.values);
  return (
    <section class="vx-panel" data-testid="measure-panel" aria-label="Measure">
      <header class="vx-panel-head">
        <span class="label">Measure</span>
        {state.status === "busy" && <span class="faint vx-busy" role="status">measuring…</span>}
        <button type="button" class="vx-x" aria-label="Close measure" onClick={onClose}>×</button>
      </header>
      {picks.length === 0
        ? <p class="vx-hint faint">Click an edge, a face or a corner, then a second one to measure between them.</p>
        : (
          <>
            <ul class="vx-picks">
              {picks.map((p, i) => <li key={i}><span class="vx-n">{i + 1}</span>{labelFor(p)}</li>)}
            </ul>
            {values.length > 0 && (
              <dl class="vx-values" data-exact={state.status === "done" ? "" : undefined}>
                {values.map((v) => (
                  <div key={v.key} class="vx-row" data-value={v.key}><dt>{v.label}</dt><dd>{fmtValueOnly(v)}</dd></div>
                ))}
              </dl>
            )}
            {state.error && <p class="vx-error" role="alert">{state.error}</p>}
            <div class="vx-actions">
              <span class="faint vx-hint">{picks.length === 1 ? "Click a second feature" : "A click starts a new pair · Esc clears"}</span>
              <button type="button" class="btn btn-ghost" onClick={onClear}>Clear</button>
            </div>
          </>
        )}
    </section>
  );
}

/** Comment mode: what should change at the clicked spot; Send pins it for the agent. */
export function CommentBox({ at, bounds, where, busy, error, onSend, onCancel }: {
  at: At; bounds: Bounds; where: string; busy: boolean; error: string | null; onSend(text: string): void; onCancel(): void;
}): JSX.Element {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const send = (e: Event) => { e.preventDefault(); if (text.trim() && !busy) onSend(text.trim()); };
  return (
    <>
      <svg class="ops-leader" width={bounds.w} height={bounds.h} aria-hidden="true"><circle cx={at.x} cy={at.y} r="3" /></svg>
      <form class="vx-comment" style={place(at, 280, 150, bounds)} data-testid="comment-box" aria-label="Comment for the agent" onSubmit={send}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onCancel(); }
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(e);
        }}>
        <label class="vx-comment-label">
          <span class="faint">{where}</span>
          <textarea ref={ref} rows={3} value={text} placeholder="What should change here?" aria-label="Your comment"
            onInput={(e) => setText((e.target as HTMLTextAreaElement).value)} />
        </label>
        {error && <p class="vx-error" role="alert">{error}</p>}
        <div class="vx-actions">
          <button type="submit" class="btn" disabled={!text.trim() || busy} aria-busy={busy || undefined}>{busy ? "Sending…" : "Send to agent"}</button>
          <button type="button" class="btn btn-ghost" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </>
  );
}

/** Numbered pins on the model, and the dots of measure's corner picks. */
export function PinLayer({ pins, dots, hoverDot = null, toScreen, bounds, openPin, onOpen, onThread }: {
  pins: readonly Pin[]; dots: readonly Vec3[];
  /** E16: the corner a measure click would snap to, drawn big. */
  hoverDot?: Vec3 | null;
  toScreen: (p: Vec3) => [number, number]; bounds: Bounds;
  openPin: string | null; onOpen(key: string | null): void; onThread(pin: Pin): void;
}): JSX.Element {
  const open = pins.find((p) => p.key === openPin) ?? null;
  return (
    <div class="vx-pins">
      {dots.map((d, i) => {
        const s = toScreen(d);
        return <span key={`d${i}`} class="vx-dot" style={{ left: `${s[0]}px`, top: `${s[1]}px` }} aria-hidden="true" />;
      })}
      {hoverDot && (() => {
        const s = toScreen(hoverDot);
        return <span class="vx-dot vx-dot-hover" data-testid="hover-vertex" style={{ left: `${s[0]}px`, top: `${s[1]}px` }} aria-hidden="true" />;
      })()}
      {pins.map((p) => {
        const s = toScreen(p.point);      // one off the sheet is clipped by it (.vp overflow)
        return (
          <button key={p.key} type="button" class="vx-pin" style={{ left: `${s[0]}px`, top: `${s[1]}px` }} data-testid="pin" data-pin={p.n}
            aria-expanded={openPin === p.key} aria-label={`Comment ${p.n}: ${p.text}`} onClick={() => onOpen(openPin === p.key ? null : p.key)}>
            {p.n}
          </button>
        );
      })}
      {open && (() => {
        const s = toScreen(open.point);
        return (
          <div class="vx-pin-note" style={place({ x: s[0], y: s[1] }, 240, 80, bounds)} data-testid="pin-note" role="dialog" aria-label={`Comment ${open.n}`}>
            <p class="vx-pin-text">{open.text}</p>
            <div class="vx-actions">
              <button type="button" class="vx-link" onClick={() => onThread(open)}>Show in the thread</button>
              <button type="button" class="vx-x" aria-label="Close" onClick={() => onOpen(null)}>×</button>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
