// The drawing sheet: CSS drafting grid + Three.js canvas + corner ticks + figure label + tool strip + title block.
import { computed } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Vector3 } from "three";
import { activePart } from "../../state/selection";
import { actions, inflight, runs, steps } from "../../state/store";
import { runLabel as formatRunLabel } from "../../lib/names";
import type { PartScene } from "./loadPart";
import { PartLoader } from "./partLoader";
import { createViewer, type ViewerHandle } from "./scene";
import { TitleBlock } from "./TitleBlock";
import { ViewerTools, type ViewMode } from "./ViewerTools";
import "./viewer.css";

/** The part on the sheet: the selected one, else the first part (state/selection.ts). */
export { activePart };

/** True while an agent tool call on the active part is in flight (edges turn blue). */
export const highlighted = computed<boolean>(() => {
  const id = activePart.value?.part_id;
  if (!id) return false;
  for (const call of inflight.value.values()) {
    if (call.name.startsWith("mirror_part_") && call.input["part_id"] === id) return true;
  }
  return false;
});

/** Label of the run that is changing the part now, else the run behind its latest diff / create, else null ("you"). */
export const changingRunLabel = computed<string | null>(() => {
  const id = activePart.value?.part_id;
  if (!id) return null;
  const all = [...runs.value.values()];
  let runId: string | null = null;
  for (const call of inflight.value.values()) {
    if (call.name.startsWith("mirror_part_") && call.input["part_id"] === id) runId = call.run_id;
  }
  if (runId === null) {
    let best = -1;
    for (const list of steps.value.values()) {
      for (const s of list) {
        // a set_vars diff, or the create that built the part (its result step is a tool_result)
        const builds = s.kind === "diff" || (s.kind === "tool_result" && s.payload["name"] === "mirror_part_create");
        if (!builds || s.id <= best) continue;
        const out = s.payload["output"];
        const data = typeof out === "object" && out !== null ? (out as Record<string, unknown>)["data"] : null;
        if (typeof data === "object" && data !== null && (data as Record<string, unknown>)["part_id"] === id) { best = s.id; runId = s.run_id; }
      }
    }
  }
  const run = runId === null ? undefined : runs.value.get(runId);
  return run ? formatRunLabel(run, all) : null;
});

export function Viewer(): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<ViewerHandle | null>(null);
  const sceneRef = useRef<PartScene | null>(null);
  const loaderRef = useRef<PartLoader | null>(null);
  const [mode, setMode] = useState<ViewMode>("shaded");
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);

  const part = activePart.value;
  const partId = part?.part_id ?? null;
  const mtime = part?.mtime ?? 0;
  const highlight = highlighted.value;
  const byLabel = changingRunLabel.value;

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let handle: ViewerHandle | null = null;
    try {
      handle = createViewer(canvas);
    } catch {
      handle = null; // no WebGL: the sheet, title block and metrics still work
    }
    viewerRef.current = handle;
    const measure = () => {
      const r = host.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) handle?.resize(r.width, r.height);
    };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(host);
    return () => {
      ro?.disconnect();
      handle?.setPart(null);
      sceneRef.current?.dispose();
      sceneRef.current = null;
      handle?.dispose();
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => { viewerRef.current?.setMode(mode); }, [mode]);
  useEffect(() => { viewerRef.current?.setHighlight(highlight); }, [highlight]);

  useEffect(() => {
    if (partId === null) {
      loaderRef.current?.reset();
      viewerRef.current?.setPart(null);
      sceneRef.current?.dispose();
      sceneRef.current = null;
      setError(null);
      setSize(null);
      return;
    }
    try { actions.client(); } catch { return; } // no core bound yet
    // the client is looked up per fetch: the session rebinds a new one after a token rotation
    loaderRef.current ??= new PartLoader(() => actions.client());
    void loaderRef.current.load(partId, mtime).then((r) => {
      if (r.status === "error") { setError(r.message); return; }
      if (r.status !== "loaded") { setError(null); return; }
      const prev = sceneRef.current;
      sceneRef.current = r.scene;
      viewerRef.current?.setPart(r.scene); // the previous model stayed on screen until now
      prev?.dispose();
      const s = r.scene.bbox.getSize(new Vector3());
      setSize(`${s.x.toFixed(2)},${s.y.toFixed(2)},${s.z.toFixed(2)}`);
      setError(null);
    });
  }, [partId, mtime]);

  return (
    <div class="vp" ref={hostRef} data-testid="viewer">
      <canvas
        ref={canvasRef}
        class="vp-canvas"
        data-testid="viewer-canvas"
        data-bbox-size={size ?? undefined}
        data-highlight={highlight ? "on" : "off"}
        data-mode={mode}
      />
      <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
      <span class="label vp-fig">FIG. 1 — DESIGN</span>
      <ViewerTools mode={mode} onMode={setMode} />
      {part === null && <div class="vp-empty label">NO PART · ask an agent to create one</div>}
      <TitleBlock part={part} runLabel={byLabel} error={error} />
    </div>
  );
}
