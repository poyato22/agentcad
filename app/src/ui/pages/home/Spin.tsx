// Home's hover turntable: pointing at a build's cover spins its model. The core renders the cover
// from 16 angles into one strip (GET /projects/{id}/spin.png); the first hover fetches it (the
// still cover shows meanwhile) and steps through its frames while the pointer stays.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

/** Frame aspect (16:10, the cover's) and step time. */
const ASPECT = 16 / 10;
export const SPIN_STEP_MS = 90;

interface Strip { url: string; frames: number }
const strips = new Map<string, Promise<Strip | null>>();

/** The strip for `key` (a build and its last edit), fetched once. */
export function loadStrip(key: string, load: () => Promise<Blob>): Promise<Strip | null> {
  let p = strips.get(key);
  if (!p) {
    p = load().then((blob) => new Promise<Strip | null>((resolve) => {
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => resolve({ url, frames: Math.max(1, Math.round(img.naturalWidth / (img.naturalHeight * ASPECT))) });
      img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
      img.src = url;
    })).catch(() => null);
    strips.set(key, p);
    void p.then((s) => { if (!s) strips.delete(key); });     // a failure is tried again next hover
  }
  return p;
}

function reducedMotion(): boolean {
  try { return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false; } catch { return false; }
}

/** The spinning model over the cover while `active` (the tile is hovered). */
export function Spin({ active, cacheKey, load }: { active: boolean; cacheKey: string; load: (() => Promise<Blob>) | null }): JSX.Element | null {
  const [strip, setStrip] = useState<Strip | null>(null);
  const [frame, setFrame] = useState(0);
  const want = useRef(active);
  want.current = active;
  useEffect(() => {
    if (!active || !load || reducedMotion()) return;
    let live = true;
    // a brief pause first: sweeping the pointer across the grid never fetches every build
    const t = setTimeout(() => { void loadStrip(cacheKey, load).then((s) => { if (live && want.current) setStrip(s); }); }, 150);
    return () => { live = false; clearTimeout(t); };
  }, [active, cacheKey, load === null]);
  useEffect(() => {
    if (!active || !strip) { setFrame(0); return; }
    const id = setInterval(() => setFrame((f) => (f + 1) % strip.frames), SPIN_STEP_MS);
    return () => clearInterval(id);
  }, [active, strip]);
  if (!active || !strip) return null;
  const pos = strip.frames > 1 ? (frame / (strip.frames - 1)) * 100 : 0;
  return (
    <div class="tile-spin" aria-hidden="true" data-testid="tile-spin" data-frame={frame}
      style={{ backgroundImage: `url(${strip.url})`, backgroundSize: `${strip.frames * 100}% 100%`, backgroundPosition: `${pos}% 0` }} />
  );
}

/** Tests. */
export function resetSpins(): void { strips.clear(); }
