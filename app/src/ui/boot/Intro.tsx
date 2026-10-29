// The welcome intro (first launch, and Profile → "Replay intro"). A 2.5–3.5s sequence: the grey
// Mises cube assembles from scattered small cubes (ui/boot/intro.ts), then "Welcome to Mises" types
// in under it in Iosevka, then the whole thing fades to the app. Skippable by click or any key, and
// under prefers-reduced-motion it becomes a 400ms fade of the finished cube and title. Both themes.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { ASSEMBLE_MS, createIntro as realCreateIntro, type IntroHandle } from "./introScene";
import { DragStrip } from "./DragStrip";
import "./boot.css";

export const TITLE_MS = 700;
export const HOLD_MS = 400;
export const FADE_MS = 400;
export const SKIP_FADE_MS = 160;

export interface IntroProps {
  onDone: () => void;
  /** Injected in tests so the sequence runs without a real WebGL context. */
  createIntro?: typeof realCreateIntro;
  /** Overrides prefers-reduced-motion (tests). */
  reducedMotion?: boolean;
}

function prefersReducedMotion(): boolean {
  try { return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false; } catch { return false; }
}

export function Intro({ onDone, createIntro = realCreateIntro, reducedMotion }: IntroProps): JSX.Element {
  const reduce = reducedMotion ?? prefersReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const handleRef = useRef<IntroHandle | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const finished = useRef(false);
  const [showTitle, setShowTitle] = useState(reduce);
  const [fading, setFading] = useState(false);
  /** The running fade's duration: the CSS transition and the unmount wait always match. */
  const [fadeMs, setFadeMs] = useState(0);

  // A single, idempotent end: fade, then hand control back to the app exactly once.
  function finish(ms: number): void {
    if (finished.current) return;
    finished.current = true;
    setFadeMs(ms);
    setFading(true);
    const t = setTimeout(() => onDone(), ms);
    timers.current.push(t);
  }

  function skip(): void {
    handleRef.current?.skip();
    setShowTitle(true);
    finish(SKIP_FADE_MS);
  }

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const at = (fn: () => void, ms: number) => { timers.current.push(setTimeout(fn, ms)); };

    let handle: IntroHandle | null = null;
    try {
      handle = createIntro(canvas, { onAssembled: () => setShowTitle(true) });
      handleRef.current = handle;
    } catch { /* no WebGL (rare): fall through to the reduced-motion timing below */ }

    const size = () => {
      const r = canvas.getBoundingClientRect();
      handle?.resize(Math.max(1, Math.round(r.width)), Math.max(1, Math.round(r.height)));
    };
    size();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(size) : null;
    ro?.observe(canvas);

    if (reduce || !handle) {
      handle?.skip();
      setShowTitle(true);
      at(() => finish(FADE_MS), 420); // a brief beat so the title registers, then a 400ms fade
    } else {
      handle.start();
      at(() => setShowTitle(true), ASSEMBLE_MS);      // backstop if onAssembled never fires
      at(() => finish(FADE_MS), ASSEMBLE_MS + TITLE_MS + HOLD_MS);
    }

    return () => {
      ro?.disconnect();
      for (const t of timers.current) clearTimeout(t);
      timers.current = [];
      handle?.dispose();
      handleRef.current = null;
    };
  }, []);

  return (
    <div class={`intro${fading ? " intro-fading" : ""}`} data-boot="intro" role="button" tabIndex={0}
      aria-label="Welcome — click or press any key to skip"
      style={{ ["--fade-ms" as string]: `${fading ? fadeMs : 0}ms` }}
      onClick={skip} onKeyDown={() => skip()}>
      <DragStrip />
      <div class="intro-stage">
        <canvas ref={canvasRef} class="intro-canvas" aria-hidden="true" />
        <div class={`intro-title${showTitle ? " is-on" : ""}${reduce ? " is-instant" : ""}`} aria-hidden={!showTitle}>
          Welcome to Mises
        </div>
      </div>
      <div class="intro-skip" aria-hidden="true">click or press any key to skip</div>
    </div>
  );
}
