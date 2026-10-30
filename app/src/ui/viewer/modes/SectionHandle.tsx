// The section plane's drag handle: a cutting-plane mark ("A") on the sheet where the plane crosses
// the middle of the model, with its arrow pointing the way you look at the cut. Dragging it slides
// the plane along its normal; arrow keys nudge it (Shift: ten times as far). It follows the camera.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import * as THREE from "three";
import type { ModeHost } from "../scene";
import { restBox } from "./controller";
import { sectionAt, sectionRange, unit } from "./section";
import type { SectionMode } from "./state";

function toScreen(host: ModeHost, p: THREE.Vector3): [number, number] {
  host.camera.updateMatrixWorld(true);
  const v = p.clone().project(host.camera);
  const w = host.canvas.clientWidth || host.canvas.width || 1;
  const h = host.canvas.clientHeight || host.canvas.height || 1;
  return [((v.x + 1) / 2) * w, ((1 - v.y) / 2) * h];
}

export interface HandleGeometry { x: number; y: number; angle: number; mmPerPx: number; range: [number, number]; at: number }

/** Where the handle sits and how a drag converts to millimetres along the normal. */
export function handleGeometry(host: ModeHost, s: SectionMode): HandleGeometry | null {
  const box = restBox(host.parts());
  if (!box) return null;
  const n = unit(s.normal);
  const at = sectionAt(s, box);
  const c = box.getCenter(new THREE.Vector3());
  const onPlane = c.clone().addScaledVector(n, at - n.dot(c));
  const range = sectionRange(box, n);
  const len = Math.max((range[1] - range[0]) / 4, 1e-3);
  const [x, y] = toScreen(host, onPlane);
  const [x1, y1] = toScreen(host, onPlane.clone().addScaledVector(n, len));
  const px = Math.hypot(x1 - x, y1 - y);
  return { x, y, angle: Math.atan2(y1 - y, x1 - x), mmPerPx: px > 2 ? len / px : 0, range, at };
}

export function SectionHandle({ host, section, onAt }: { host: ModeHost; section: SectionMode; onAt(at: number): void }): JSX.Element | null {
  const [, setTick] = useState(0);
  const drag = useRef<{ x: number; y: number; at: number; g: HandleGeometry } | null>(null);
  useEffect(() => {
    const on = () => setTick((t) => t + 1);
    host.controls.addEventListener("change", on);
    const off = host.onRestyle(on);
    return () => { host.controls.removeEventListener("change", on); off(); };
  }, [host]);
  const g = handleGeometry(host, section);
  if (!g) return null;
  const clamp = (v: number) => Math.min(g.range[1], Math.max(g.range[0], v));
  const step = Math.max((g.range[1] - g.range[0]) / 100, 0.01);

  function onDown(e: PointerEvent): void {
    if (e.button !== 0 || !g) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, at: g.at, g };
  }
  function onMove(e: PointerEvent): void {
    const d = drag.current;
    if (!d || d.g.mmPerPx === 0) return;
    // the drag projected on the arrow's screen direction
    const along = (e.clientX - d.x) * Math.cos(d.g.angle) + (e.clientY - d.y) * Math.sin(d.g.angle);
    onAt(clamp(d.at + along * d.g.mmPerPx));
  }
  function onUp(): void { drag.current = null; }
  function onKey(e: KeyboardEvent): void {
    const dir = e.key === "ArrowUp" || e.key === "ArrowRight" ? 1 : e.key === "ArrowDown" || e.key === "ArrowLeft" ? -1 : 0;
    if (!dir || !g) return;
    e.preventDefault();
    onAt(clamp(g.at + dir * step * (e.shiftKey ? 10 : 1)));
  }

  return (
    <div class="vm-handle" data-testid="section-handle" role="slider" tabIndex={0}
      aria-label="Section plane position (drag, or arrow keys)" aria-valuemin={g.range[0]} aria-valuemax={g.range[1]} aria-valuenow={Number(g.at.toFixed(2))}
      aria-valuetext={`${g.at.toFixed(1)} mm`}
      style={{ left: `${g.x}px`, top: `${g.y}px` }}
      onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onKeyDown={onKey}>
      <span class="vm-handle-arrow" style={{ transform: `rotate(${g.angle}rad)` }} aria-hidden="true" />
      <span class="vm-handle-mark" aria-hidden="true">A</span>
    </div>
  );
}
