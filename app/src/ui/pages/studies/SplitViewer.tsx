// Design | stress split: the part (model.glb, drafting look) next to the study's heatmap
// (result.glb, per-vertex von Mises colors) with a legend in MPa. Both halves use the shared viewer
// (ui/viewer/scene.ts + loadPart.ts); colors follow the theme.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { actions } from "../../../state/store";
import { loadPart, type PartScene } from "../../viewer/loadPart";
import { PartLoader } from "../../viewer/partLoader";
import { createViewer, type ViewerHandle } from "../../viewer/scene";
import { legendGradient, legendTicks, sig3 } from "./model";
import { loadStress, resolvedTheme, sceneColors, tintDesign, watchTheme, type StressLegend } from "./stressScene";
import "../../viewer/viewer.css";

type Glb = { bytes: ArrayBuffer; etag: string } | "not-modified";
export interface GlbSource { part(partId: string, etag?: string): Promise<Glb>; sim(simId: string): Promise<Glb> }

/** The bound core client (none bound yet: nothing loads, the sheet still renders). */
export const coreGlb: GlbSource = {
  part: (id, etag) => actions.client().partGlb(id, etag),
  sim: (id) => actions.client().simGlb(id),
};

const colors = () => sceneColors(resolvedTheme());

/** One sheet with a canvas; owns its viewer. `onTheme` re-tints when the palette changes. */
function useSheet(): { host: preact.RefObject<HTMLDivElement>; canvas: preact.RefObject<HTMLCanvasElement>; viewer: preact.RefObject<ViewerHandle | null> } {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewer = useRef<ViewerHandle | null>(null);
  useEffect(() => {
    const c = canvas.current;
    const h = host.current;
    if (!c || !h) return;
    let v: ViewerHandle | null = null;
    try { v = createViewer(c, { edgeColor: () => colors().edge }); } catch { v = null; } // no WebGL: numbers still show
    viewer.current = v;
    const measure = () => {
      const r = h.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) v?.resize(r.width, r.height);
    };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(h);
    return () => { ro?.disconnect(); v?.setPart(null); v?.dispose(); viewer.current = null; };
  }, []);
  return { host, canvas, viewer };
}

export function SplitViewer({ partId, mtime, simId, range, glb = coreGlb }: {
  partId: string; mtime: number; simId: string | null; range: [number, number] | null; glb?: GlbSource;
}): JSX.Element {
  const design = useSheet();
  const stress = useSheet();
  const designScene = useRef<PartScene | null>(null);
  const stressScene = useRef<PartScene | null>(null);
  const loader = useRef<PartLoader | null>(null);
  const [designState, setDesignState] = useState<"loading" | "loaded" | string>("loading");
  const [stressState, setStressState] = useState<"loading" | "loaded" | string>("loading");
  const [legend, setLegend] = useState<StressLegend | null>(null);

  // theme: faces of the design half + edges of both (setMode re-applies the viewer's style)
  useEffect(() => watchTheme(() => {
    if (designScene.current) tintDesign(designScene.current, colors());
    design.viewer.current?.setMode("shaded");
    stress.viewer.current?.setMode("shaded");
  }), []);

  useEffect(() => {
    loader.current ??= new PartLoader(() => ({ partGlb: (id: string, etag?: string) => glb.part(id, etag) }), loadPart);
    let live = true;
    void loader.current.load(partId, mtime).then((r) => {
      if (!live) { if (r.status === "loaded") r.scene.dispose(); return; }
      if (r.status === "error") { setDesignState(r.message); return; }
      if (r.status !== "loaded") return;
      tintDesign(r.scene, colors());
      const prev = designScene.current;
      designScene.current = r.scene;
      design.viewer.current?.setPart(r.scene);
      prev?.dispose();
      setDesignState("loaded");
    });
    return () => { live = false; };
  }, [partId, mtime]);

  useEffect(() => {
    if (simId === null) return;
    let live = true;
    setStressState("loading");
    void (async () => {
      try {
        const res = await glb.sim(simId);
        if (res === "not-modified" || !live) return;
        const { scene, legend: lg } = await loadStress(res.bytes, colors().edge);
        if (!live) { scene.dispose(); return; }
        const prev = stressScene.current;
        stressScene.current = scene;
        stress.viewer.current?.setPart(scene);
        prev?.dispose();
        setLegend(lg);
        setStressState("loaded");
      } catch (e) {
        if (live) setStressState(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { live = false; };
  }, [simId]);

  useEffect(() => () => {
    designScene.current?.dispose();
    stressScene.current?.dispose();
  }, []);

  const max = legend?.max ?? range?.[1] ?? null;
  const min = legend?.min ?? range?.[0] ?? 0;
  const units = legend?.units ?? "MPa";
  return (
    <div class="st-split" data-testid="split-viewer">
      <div class="vp st-sheet" ref={design.host}>
        <canvas ref={design.canvas} class="vp-canvas" data-testid="design-canvas" data-state={designState === "loaded" || designState === "loading" ? designState : "error"} />
        <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
        <span class="label vp-fig">FIG. 1 — DESIGN</span>
        {designState !== "loaded" && designState !== "loading" && <div class="st-sheet-msg text">{designState}</div>}
      </div>
      <div class="vp st-sheet" ref={stress.host}>
        <canvas ref={stress.canvas} class="vp-canvas" data-testid="stress-canvas" data-state={stressState === "loaded" || stressState === "loading" ? stressState : "error"} />
        <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
        <span class="label vp-fig">FIG. 2 — STRESS · VON MISES</span>
        {stressState !== "loaded" && stressState !== "loading" && <div class="st-sheet-msg text">{stressState}</div>}
      </div>
      {max !== null ? (
        <div class="st-legend" data-testid="legend" aria-label={`von Mises stress legend, 0 to ${sig3(max)} ${units}`}>
          <span class="label">{units}</span>
          <div class="st-legend-body">
            <div class="st-legend-bar" style={{ background: legendGradient() }} />
            <div class="st-legend-ticks">
              {legendTicks(min, max).reverse().map((t) => <span key={t}>{sig3(t)}</span>)}
            </div>
          </div>
        </div>
      ) : <div class="st-legend" aria-hidden="true" />}
    </div>
  );
}
