// Motion for the view modes: one clock (tests drive a fake one), the reduced-motion check and a
// tween. Under prefers-reduced-motion every change lands at once (no glide, no auto-orbit).

export interface Clock {
  now(): number;
  frame(cb: () => void): number;
  cancel(id: number): void;
}

export const realClock: Clock = {
  now: () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
  frame: (cb) => (typeof requestAnimationFrame === "function" ? requestAnimationFrame(cb) : (setTimeout(cb, 16) as unknown as number)),
  cancel: (id) => { if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(id); else clearTimeout(id); },
};

export function prefersReducedMotion(): boolean {
  try { return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false; } catch { return false; }
}

export function easeInOutCubic(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
}

/** Calls `step(v)` from `from` to `to` over `ms` (eased), one call per frame, the last with `to`
 *  exactly. Returns a cancel function. Reduced motion (or ms <= 0): one call with `to`. */
export function tween(from: number, to: number, ms: number, step: (v: number) => void, clock: Clock = realClock, reduced = prefersReducedMotion()): () => void {
  if (reduced || ms <= 0 || from === to) { step(to); return () => {}; }
  const t0 = clock.now();
  let id = 0;
  let live = true;
  const tick = () => {
    if (!live) return;
    const t = (clock.now() - t0) / ms;
    if (t >= 1) { live = false; step(to); return; }
    step(from + (to - from) * easeInOutCubic(t));
    id = clock.frame(tick);
  };
  id = clock.frame(tick);
  return () => { live = false; clock.cancel(id); };
}
