// The live density preview of a converging run (and the result once done): a small three.js
// view of the variant's preview.glb / result.glb, iso camera, drag to turn. The GLB is re-read
// whenever its version (the iteration it was written at) changes, with If-None-Match. Without
// WebGL (tests, a lost context) it says so instead of drawing.
import * as THREE from "three";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { loadPart, type PartScene } from "../viewer/loadPart";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG } from "../viewer/scene";

export type GlbLoader = (etag?: string) => Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified">;

interface View { renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.OrthographicCamera; part: PartScene | null; az: number; el: number }

function place(v: View): void {
  const az = THREE.MathUtils.degToRad(v.az); const el = THREE.MathUtils.degToRad(v.el);
  v.camera.position.set(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el)).multiplyScalar(100);
  if (v.part) fitOrtho(v.camera, v.part.bbox, 1, 1.1);
  v.renderer.render(v.scene, v.camera);
}

export function DensityPreview({ load, version, label }: { load: GlbLoader; version: string | number | null; label: string }): JSX.Element {
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<View | null>(null);
  const etag = useRef<string | undefined>(undefined);
  const [state, setState] = useState<"loading" | "ok" | "nogl" | "error">("loading");

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    try {
      const renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(c.clientWidth || 320, c.clientHeight || 240, false);
      const scene = new THREE.Scene();
      scene.add(new THREE.AmbientLight(0xffffff, 1.1));
      const key = new THREE.DirectionalLight(0xffffff, 1.6);
      key.position.set(-0.4, -0.6, 1);
      scene.add(key);
      const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
      camera.up.set(0, 0, 1);
      view.current = { renderer, scene, camera, part: null, az: ISO_AZIMUTH_DEG, el: ISO_ELEVATION_DEG };
    } catch {
      setState("nogl");
    }
    return () => {
      view.current?.part?.dispose();
      view.current?.renderer.dispose();
      view.current = null;
    };
  }, []);

  useEffect(() => {
    let live = true;
    const v = view.current;
    if (!v) return;
    void load(etag.current).then(async (r) => {
      if (!live || r === "not-modified") return;
      etag.current = r.etag;
      const part = await loadPart(r.bytes);
      if (!live || view.current !== v) { part.dispose(); return; }
      v.part?.dispose();
      v.part = part;
      v.scene.add(part.object);
      place(v);
      setState("ok");
    }).catch(() => { if (live) setState((s) => (s === "ok" ? s : "error")); });
    return () => { live = false; };
  }, [load, version]);

  const drag = useRef<{ x: number; y: number } | null>(null);
  const onDown = (e: PointerEvent) => { drag.current = { x: e.clientX, y: e.clientY }; (e.currentTarget as Element).setPointerCapture?.(e.pointerId); };
  const onMove = (e: PointerEvent) => {
    const v = view.current;
    if (!drag.current || !v) return;
    v.az -= (e.clientX - drag.current.x) * 0.5;
    v.el = Math.max(-85, Math.min(85, v.el + (e.clientY - drag.current.y) * 0.5));
    drag.current = { x: e.clientX, y: e.clientY };
    place(v);
  };
  const onUp = () => { drag.current = null; };

  return (
    <div class="gd-preview" data-testid="gd-preview" data-state={state}>
      <canvas ref={canvas} aria-label={label} role="img" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} />
      {state === "loading" && <span class="gd-preview-note faint">Waiting for the first preview…</span>}
      {state === "nogl" && <span class="gd-preview-note faint">The 3D preview needs WebGL.</span>}
      {state === "error" && <span class="gd-preview-note faint">No preview yet.</span>}
    </div>
  );
}
