// A reference image (a photo, a three-view sheet) laid over the model, pinned to one standard
// view. Pinning moves the camera to that view; the image only shows while the camera is there, so
// orbiting away never leaves a misaligned picture on the sheet ("Back to front view" returns).
// The image stays on this Mac: it is read into a blob: URL and never sent anywhere.
import { signal, type Signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ModeHost } from "../scene";
import { currentView, frameView, liveBox, viewDirection, type ViewName } from "./camera";

export interface Reference {
  url: string; name: string; view: ViewName; opacity: number; scale: number;
  /** Offset from the sheet's middle, in px. */
  dx: number; dy: number;
}

export const reference: Signal<Reference | null> = signal<Reference | null>(null);

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

/** A picked file becomes the reference (images only; the previous blob URL is released). */
export function setReferenceFile(file: File, view: ViewName = reference.peek()?.view ?? "front"): boolean {
  if (!IMAGE_TYPES.includes(file.type)) return false;
  const prev = reference.peek();
  if (prev) URL.revokeObjectURL(prev.url);
  reference.value = { url: URL.createObjectURL(file), name: file.name, view, opacity: prev?.opacity ?? 0.5, scale: prev?.scale ?? 1, dx: 0, dy: 0 };
  return true;
}

export function clearReference(): void {
  const prev = reference.peek();
  if (prev) URL.revokeObjectURL(prev.url);
  reference.value = null;
}

export function patchReference(p: Partial<Reference>): void {
  const r = reference.peek();
  if (r) reference.value = { ...r, ...p };
}

/** Moves the camera to the reference's view. */
export function goToView(host: ModeHost, view: ViewName): void {
  const box = liveBox(host.parts());
  if (box) frameView(host, viewDirection(view), box);
}

export function ReferenceOverlay({ host }: { host: ModeHost | null }): JSX.Element | null {
  const r = reference.value;
  const [inView, setInView] = useState(true);
  const drag = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null);
  useEffect(() => {
    if (!host) return;
    const on = () => setInView(reference.peek() ? currentView(host.camera, host.controls.target) === reference.peek()?.view : true);
    on();
    host.controls.addEventListener("change", on);
    return () => host.controls.removeEventListener("change", on);
  }, [host, r?.view]);
  if (!r) return null;
  if (!inView) {
    return (
      <button type="button" class="vm-ref-away vt-btn" data-testid="reference-away" onClick={() => { if (host) { goToView(host, r.view); setInView(true); } }}>
        {`Reference hidden · back to ${r.view} view`}
      </button>
    );
  }
  return (
    <div class="vm-ref" data-testid="reference-overlay" data-view={r.view}
      style={{ opacity: r.opacity, transform: `translate(calc(-50% + ${r.dx}px), calc(-50% + ${r.dy}px)) scale(${r.scale})` }}>
      <img src={r.url} alt={`Reference: ${r.name}`} draggable={false} />
      <span class="vm-ref-grip" title="Drag to line the reference up" aria-hidden="true"
        onPointerDown={(e) => { e.preventDefault(); e.stopPropagation(); (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, dx: r.dx, dy: r.dy }; }}
        onPointerMove={(e) => { const d = drag.current; if (d) patchReference({ dx: d.dx + (e.clientX - d.x), dy: d.dy + (e.clientY - d.y) }); }}
        onPointerUp={() => { drag.current = null; }} />
    </div>
  );
}
