// A small live 3D view of some parts (a presented view in the thread, the immersive view): its own
// WebGL canvas, parts loaded through the same GLB loader as the sheet, a camera from the view spec
// and an optional orbit. It owns its WebGL context and gives it back on unmount (after handing a
// last frame to `onSnapshot`), so callers can cap how many are alive at once.
import { signal, type Signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import * as THREE from "three";
import { actions, parts } from "../../../state/store";
import type { PartScene } from "../loadPart";
import { PartLoader } from "../partLoader";
import { createViewer, type DesignViewerHandle, type ModeHost, type RendererLike } from "../scene";
import { dirFromAzEl, frameView, Orbiter, viewDirection, zoomForDistance } from "./camera";
import { restBox } from "./controller";
import type { ViewCamera } from "./viewSpec";

/** How many live views hold a WebGL context right now (tests and the cards' cap read it). */
export const liveContexts: Signal<number> = signal(0);

export type RendererFactory = (canvas: HTMLCanvasElement) => RendererLike & { forceContextLoss?(): void };

function defaultRenderer(canvas: HTMLCanvasElement): RendererLike & { forceContextLoss(): void } {
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true, stencil: true });
}

export interface LiveViewProps {
  partIds: readonly string[];
  camera: ViewCamera | "auto";
  /** The explode the view will settle at (the frame leaves room for it). */
  explode: number;
  orbit: { duration_s: number; turns: number } | null;
  /** Orbit runs while true (callers pass false under reduced motion until the user presses play). */
  playing: boolean;
  label: string;
  onHost?(h: ModeHost | null): void;
  /** Every part has loaded (or failed). */
  onLoaded?(): void;
  /** The last frame, as a PNG data URL, just before the context is released. */
  onSnapshot?(url: string): void;
  createRenderer?: RendererFactory;
  class?: string;
}

export function LiveView(p: LiveViewProps): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewer = useRef<DesignViewerHandle | null>(null);
  const host = useRef<ModeHost | null>(null);
  const orbiter = useRef<Orbiter | null>(null);
  const framed = useRef(false);
  const props = useRef(p);
  props.current = p;
  const [state, setState] = useState<"loading" | "ready" | "error" | "no-webgl">("loading");
  const ids = p.partIds.join("|");

  function frame(): void {
    const h = host.current;
    if (!h) return;
    const box = restBox(h.parts());
    if (!box) return;
    const cam = props.current.camera;
    const dir = cam === "auto" ? viewDirection("iso") : dirFromAzEl(cam.azimuth, cam.elevation);
    const zoom = (cam !== "auto" && cam.distance ? zoomForDistance(box, cam.distance) : 1) / (1 + props.current.explode * 0.8);
    frameView(h, dir, box, zoom);
  }

  useEffect(() => {
    const canvas = canvasRef.current;
    const el = hostRef.current;
    if (!canvas || !el) return;
    let gl: (RendererLike & { forceContextLoss?(): void }) | null = null;
    let handle: DesignViewerHandle;
    try {
      handle = createViewer(canvas, { createRenderer: (c) => { gl = (props.current.createRenderer ?? defaultRenderer)(c); return gl; } });
    } catch {
      setState("no-webgl");
      return;
    }
    liveContexts.value = liveContexts.peek() + 1;
    viewer.current = handle;
    const h = handle.modeHost?.() ?? null;
    host.current = h;
    if (h) orbiter.current = new Orbiter(h);
    props.current.onHost?.(h);
    const measure = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { handle.resize(r.width, r.height); if (framed.current) frame(); }
    };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);

    const scenes = new Map<string, PartScene>();
    let alive = true;
    const want = [...props.current.partIds];
    const compose = () => {
      const byId = new Map(parts.peek().map((x) => [x.part_id, x]));
      handle.setParts(want.flatMap((id) => {
        const sc = scenes.get(id);
        return sc ? [{ id, scene: sc, color: byId.get(id)?.color ?? null, focus: false }] : [];
      }), false, true);
    };
    let left = want.length;
    let failed = 0;
    const settle = () => {
      if (--left > 0 || !alive) return;
      compose();
      frame();
      framed.current = true;
      setState(failed === want.length ? "error" : "ready");
      props.current.onLoaded?.();
    };
    for (const id of want) {
      const mtime = parts.peek().find((x) => x.part_id === id)?.mtime ?? 0;
      let loader: PartLoader;
      try { loader = new PartLoader(() => actions.client()); } catch { failed++; settle(); continue; }
      void loader.load(id, mtime).then((r) => {
        if (!alive) { if (r.status === "loaded") r.scene.dispose(); return; }
        if (r.status === "loaded") scenes.set(id, r.scene); else failed++;
        settle();
      });
    }
    if (!want.length) { setState("error"); }

    return () => {
      alive = false;
      orbiter.current?.stop();
      orbiter.current = null;
      try {
        const url = canvas.toDataURL("image/png");
        if (url.startsWith("data:image/png")) props.current.onSnapshot?.(url);
      } catch { /* no 2D readback (tests): keep the previous still */ }
      props.current.onHost?.(null);
      host.current = null;
      ro?.disconnect();
      handle.setParts([], false);
      for (const s of scenes.values()) s.dispose();
      handle.dispose();
      gl?.forceContextLoss?.();
      viewer.current = null;
      framed.current = false;
      liveContexts.value = Math.max(0, liveContexts.peek() - 1);
    };
  }, [ids]);

  // the orbit follows `playing` (and starts only once the parts are framed)
  useEffect(() => {
    const o = orbiter.current;
    if (!o) return;
    if (p.playing && p.orbit && state === "ready") o.start(p.orbit.turns, p.orbit.duration_s, { force: true });
    else o.stop();
    return () => o.stop();
  }, [p.playing, p.orbit?.turns, p.orbit?.duration_s, state]);

  return (
    <div class={`lv${p.class ? ` ${p.class}` : ""}`} ref={hostRef} data-testid="live-view" data-state={state} data-orbit={p.playing && p.orbit ? "playing" : "paused"}>
      <canvas ref={canvasRef} class="lv-canvas" role="img" aria-label={p.label} />
      {state === "loading" && <span class="lv-note label" aria-hidden="true">loading</span>}
      {state === "error" && <span class="lv-note text faint">Could not load these parts.</span>}
      {state === "no-webgl" && <span class="lv-note text faint">3D is not available here.</span>}
    </div>
  );
}
