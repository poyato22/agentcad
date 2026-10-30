// The drawing sheet: CSS drafting grid + Three.js canvas + corner ticks + figure label + tool strip + title block.
// Model | Code tabs: the model (this part, or the whole build) or the part's script.
// Click the model to add a feature where you clicked (hole / shell on a face, fillet / chamfer on
// an edge); in the build view a click on another part selects it. Each part shows in its color.
import { computed, signal, type Signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Vector3 } from "three";
import type { PartEdge } from "../../api/builds";
import type { PartFace, PartSummary } from "../../api/types";
import { activePart } from "../../state/selection";
import { actions, inflight, parts, runs, steps } from "../../state/store";
import { runLabel as formatRunLabel } from "../../lib/names";
import { OpsMenu, type OpsTarget } from "../editor/ops";
import { SourceEditor } from "../editor/SourceEditor";
import { hiddenParts } from "../left/visibility";
import type { PartScene } from "./loadPart";
import { PartLoader } from "./partLoader";
import { edgeEnds, matchFace, nearestEdge } from "./pick";
import { createViewer, type DesignViewerHandle, type PickMarker } from "./scene";
import { TitleBlock } from "./TitleBlock";
import { ViewerTools, type ViewMode, type ViewScope } from "./ViewerTools";
import { ModeSwitch } from "../assembly/mode";
import "./viewer.css";

/** The part on the sheet: the selected one, else the first part (state/selection.ts). */
export { activePart };

/** Model or Code (the part's script). */
export const viewTab: Signal<"model" | "code"> = signal<"model" | "code">("model");
/** This part only, or every visible part of the build. */
export const viewScope: Signal<ViewScope> = signal<ViewScope>("part");

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

/** The parts on the sheet: the focused one, or (build view) every visible part with a model. */
export function shownParts(list: readonly PartSummary[], focus: PartSummary | null, scope: ViewScope, hidden: ReadonlySet<string>): PartSummary[] {
  if (scope === "part") return focus ? [focus] : [];
  return list.filter((p) => p.metrics !== null && (p.part_id === focus?.part_id || !hidden.has(p.part_id)));
}

interface OpenOps { seq: number; partId: string; target: OpsTarget; at: { x: number; y: number } | null; keyboard: boolean; baseVersion: number }

/** The blue marker for an ops target. */
function markerFor(part: PartSummary, face: PartFace | null, edge: PartEdge | null): PickMarker {
  if (edge) {
    const ends = edgeEnds(edge);
    return ends ? { kind: "edge", a: ends[0], b: ends[1] } : null;
  }
  if (face?.normal) {
    const size = part.metrics?.bbox.size ?? [1, 1, 1];
    const diag = Math.hypot(size[0], size[1], size[2]);
    return { kind: "face", partId: part.part_id, normal: face.normal, point: face.center, tol: Math.max(0.01, diag * 0.002) };
  }
  return null;
}

export function Viewer(): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<DesignViewerHandle | null>(null);
  const scenes = useRef(new Map<string, PartScene>());
  const loaders = useRef(new Map<string, PartLoader>());
  const down = useRef<{ x: number; y: number } | null>(null);
  /** Each pick opens a fresh callout (its own op, face / edge and fields). */
  const pickSeq = useRef(0);
  const [mode, setMode] = useState<ViewMode>("shaded");
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);
  const [ops, setOps] = useState<OpenOps | null>(null);
  const [bounds, setBounds] = useState({ w: 1, h: 1 });

  const part = activePart.value;
  const partId = part?.part_id ?? null;
  const scope = viewScope.value;
  const tab = viewTab.value;
  const highlight = highlighted.value;
  const byLabel = changingRunLabel.value;
  const shown = shownParts(parts.value, part, scope, hiddenParts.value);
  const loadSig = shown.map((p) => `${p.part_id}@${p.mtime}`).join("|");
  const colorSig = shown.map((p) => `${p.part_id}:${p.color ?? ""}`).join("|");

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let handle: DesignViewerHandle | null = null;
    try {
      handle = createViewer(canvas);
    } catch {
      handle = null; // no WebGL: the sheet, title block and metrics still work
    }
    viewerRef.current = handle;
    const measure = () => {
      const r = host.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { handle?.resize(r.width, r.height); setBounds({ w: r.width, h: r.height }); }
    };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(host);
    return () => {
      ro?.disconnect();
      handle?.setPart(null);
      for (const s of scenes.current.values()) s.dispose();
      scenes.current.clear();
      handle?.dispose();
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => { viewerRef.current?.setMode(mode); }, [mode]);
  useEffect(() => { viewerRef.current?.setHighlight(highlight); }, [highlight]);

  /** Put the loaded parts on the sheet. */
  function compose(refit: boolean): void {
    const list = shownParts(parts.peek(), activePart.peek(), viewScope.peek(), hiddenParts.peek());
    const focus = activePart.peek()?.part_id ?? null;
    const items = list.flatMap((p) => {
      const sc = scenes.current.get(p.part_id);
      return sc ? [{ id: p.part_id, scene: sc, color: p.color ?? null, focus: p.part_id === focus }] : [];
    });
    viewerRef.current?.setParts(items, refit, viewScope.peek() === "build");
    const f = focus ? scenes.current.get(focus) : undefined;
    if (f) {
      const s = f.bbox.getSize(new Vector3());
      setSize(`${s.x.toFixed(2)},${s.y.toFixed(2)},${s.z.toFixed(2)}`);
    } else setSize(null);
  }

  // load what is shown (each part only when its mtime changed), drop what no longer is
  useEffect(() => {
    const want = new Map(shown.map((p) => [p.part_id, p.mtime]));
    for (const id of [...scenes.current.keys()]) {
      if (want.has(id)) continue;
      scenes.current.get(id)?.dispose();
      scenes.current.delete(id);
      loaders.current.get(id)?.reset();
      loaders.current.delete(id);
    }
    if (want.size === 0) { compose(false); setError(null); return; }
    try { actions.client(); } catch { return; } // no core bound yet
    for (const [id, mtime] of want) {
      // the client is looked up per fetch: the session rebinds a new one after a token rotation
      let loader = loaders.current.get(id);
      if (!loader) { loader = new PartLoader(() => actions.client()); loaders.current.set(id, loader); }
      void loader.load(id, mtime).then((r) => {
        const isFocus = id === activePart.peek()?.part_id;
        if (r.status === "error") { if (isFocus) setError(r.message); return; }
        if (r.status !== "loaded") { if (isFocus) setError(null); return; }
        if (!loaders.current.has(id)) { r.scene.dispose(); return; }   // no longer shown
        const prev = scenes.current.get(id);
        scenes.current.set(id, r.scene);              // the previous model stayed on screen until now
        compose(isFocus || viewScope.peek() === "build");
        prev?.dispose();
        if (isFocus) setError(null);
      });
    }
    compose(false);
  }, [loadSig]);

  // focus / scope changes refit; color changes repaint
  useEffect(() => { compose(true); setOps(null); }, [partId, scope]);
  useEffect(() => { compose(false); }, [colorSig]);

  // the pick marker follows the open ops target; nothing is marked without it
  useEffect(() => { if (!ops) viewerRef.current?.setMarker(null); }, [ops]);

  function onPointerUp(e: PointerEvent): void {
    const d = down.current;
    down.current = null;
    if (!d || e.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4) return;   // a drag orbits
    const v = viewerRef.current;
    const host = hostRef.current;
    if (!v || !host || tab !== "model") return;
    const r = host.getBoundingClientRect();
    const x = e.clientX - r.left; const y = e.clientY - r.top;
    const hit = v.pick(x, y);
    if (!hit) { setOps(null); return; }
    const target = parts.peek().find((p) => p.part_id === hit.partId);
    if (!target) return;
    if (hit.partId !== activePart.peek()?.part_id) { actions.selectPart(hit.partId); return; }
    const m = target.metrics;
    const sizeV = m?.bbox.size ?? [1, 1, 1];
    const diag = Math.hypot(sizeV[0], sizeV[1], sizeV[2]);
    const face = m?.face_list ? matchFace(m.face_list, hit.point, hit.normal, Math.max(0.05, diag * 0.01)) : null;
    const edge = m?.edge_list ? nearestEdge(m.edge_list, hit.point, (p) => v.toScreen(p), 10) : null;
    // the version of the metrics the pick was resolved from (the same row), never a newer one
    // only heard of: face / edge ids name one version's topology, so a stale pick gets a 409
    setOps({ seq: ++pickSeq.current, partId: target.part_id, target: { face, edge, point: hit.point }, at: { x, y }, keyboard: false, baseVersion: target.version ?? 0 });
  }

  const opsPart = ops ? parts.value.find((p) => p.part_id === ops.partId) ?? null : null;
  const canEdit = part !== null && part.metrics !== null && (part.kind ?? "part") === "part";

  return (
    <div class="vp" ref={hostRef} data-testid="viewer" data-tab={tab}>
      <canvas
        ref={canvasRef}
        class="vp-canvas"
        data-testid="viewer-canvas"
        data-bbox-size={size ?? undefined}
        data-highlight={highlight ? "on" : "off"}
        data-mode={mode}
        data-scope={scope}
        data-shown={shown.map((p) => `${p.part_id}${p.color ? `:${p.color}` : ""}`).join(" ") || undefined}
        onPointerDown={(e) => { if (e.button === 0) down.current = { x: e.clientX, y: e.clientY }; }}
        onPointerUp={onPointerUp}
      />
      <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
      {/* one row in the sheet corner: Part | Assembly first (U2b), then Model | Code — above the Code view (M-1) */}
      <div class="vp-corner">
        <ModeSwitch />
        <div class="vp-tabs" role="tablist" aria-label="Show">
          {(["model", "code"] as const).map((t) => (
            <button key={t} type="button" role="tab" class="vp-tab" aria-selected={tab === t} data-selected={tab === t ? "" : undefined} tabIndex={tab === t ? 0 : -1}
              onClick={() => { viewTab.value = t; }}
              onKeyDown={(e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); viewTab.value = tab === "model" ? "code" : "model"; (e.currentTarget.parentElement?.querySelector(`[aria-selected=false]`) as HTMLElement | null)?.focus(); } }}>
              {t === "model" ? "Model" : "Code"}
            </button>
          ))}
        </div>
      </div>
      <span class="label vp-fig">{scope === "build" ? "FIG. 1 — BUILD" : "FIG. 1 — DESIGN"}</span>
      <ViewerTools mode={mode} onMode={setMode} scope={scope} onScope={(s) => { viewScope.value = s; }}
        canEdit={canEdit && tab === "model"}
        onEdit={() => { if (part) setOps({ seq: ++pickSeq.current, partId: part.part_id, target: { face: null, edge: null, point: null }, at: null, keyboard: true, baseVersion: part.version ?? 0 }); }} />
      {part === null && <div class="vp-empty label">NO PART · ask an agent to create one</div>}
      {ops && opsPart && tab === "model" && (
        <OpsMenu key={ops.seq} part={opsPart} target={ops.target} baseVersion={ops.baseVersion} keyboard={ops.keyboard} at={ops.at} bounds={bounds}
          onTarget={(f, ed) => viewerRef.current?.setMarker(markerFor(opsPart, f, ed))}
          onClose={() => { setOps(null); canvasRef.current?.focus(); }} />
      )}
      {tab === "code" && (part
        ? <SourceEditor key={part.part_id} part={part} />
        : <div class="src src-empty"><p class="faint">Select a part to see its script.</p></div>)}
      <TitleBlock part={part} runLabel={byLabel} error={error} />
    </div>
  );
}
