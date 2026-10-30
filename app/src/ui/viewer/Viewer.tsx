// The drawing sheet: CSS drafting grid + Three.js canvas + corner ticks + figure label + tool strip + title block.
// Model | Code (View ▸ Part script): the model (this part, or the whole build) or the part's script.
// A click on the model says what it hit ("Face · planar · 1,200 mm²"); "Modify…" there (or View ▸
// Add a feature…) adds a feature on it (hole / shell on a face, fillet / chamfer on an edge).
// View ▸ Measure (M) shows exact values from the core; View ▸ Comment pins feedback on the model
// for the build's agent. In the build view a click on another part selects it. Each part shows in its color.
import { computed, effect, signal, type Signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Vector3 } from "three";
import type { PartEdge } from "../../api/builds";
import type { PartFace, PartSummary } from "../../api/types";
import { activePart } from "../../state/selection";
import { activeBuildId } from "../../state/builds";
import { actions, inflight, parts, runs, steps } from "../../state/store";
import { CoreError } from "../../api/client";
import type { Vec3 } from "../../api/types";
import { partName as formatPartName, runLabel as formatRunLabel } from "../../lib/names";
import { OpsMenu, type OpsTarget } from "../editor/ops";
import { SourceEditor } from "../editor/SourceEditor";
import { hiddenParts } from "../left/visibility";
import type { PartScene } from "./loadPart";
import { PartLoader } from "./partLoader";
import { edgeEnds, matchFace, nearestEdge } from "./pick";
import { circleOf, labelFor, pickTarget, provisionalValues, refOf, togglePick, type Picked } from "./interact";
import { CommentBox, MeasurePanel, PinLayer, SelectionLabel, type MeasureState } from "./Overlays";
import { addLocalPin, buildPins, type Pin } from "./pins";
import { modifyRequest, takeModify, viewerTool, type ViewerTool } from "./tool";
import { modalOpen } from "../pages/router";
import { createViewer, type DesignViewerHandle, type PickMarker } from "./scene";
import { TitleBlock } from "./TitleBlock";
import { ViewerTools, type ViewMode, type ViewScope } from "./ViewerTools";
import { AllPartsToggle } from "./Overview";
import { SkeletonPanel } from "./SkeletonPanel";
import { ModeSwitch } from "../assembly/mode";
import { materialOf } from "../../lib/appearance";
import { backgroundStyle, edgeInk, isLight, viewerBackground } from "./background";
import { BackgroundMenu } from "./BackgroundMenu";
import { DesignViewModes, designModeItems } from "./modes";
import { explodeOffset } from "./modes/explode";
import type { ModeHost } from "./scene";
import "./viewer.css";
import { addPartMenuItem, measureHook } from "../left/partMenuHooks";
import { setDesignMode } from "../assembly/mode";
import { requestModify } from "./tool";

// U2a's part actions in U8's part menu (the one place a part's actions live): Measure opens the
// viewer's measure tool on the part; Modify… opens the ops form (hole / shell / fillet / chamfer).
measureHook.value = (ids) => { if (ids[0]) actions.selectPart(ids[0]); setDesignMode("part"); viewerTool.value = "measure"; };
addPartMenuItem({
  label: "Modify…", single: true,
  disabled: (ids) => { const p = parts.peek().find((x) => x.part_id === ids[0]); return !p || p.metrics === null || (p.kind ?? "part") !== "part"; },
  run: (ids) => { const id = ids[0]; if (!id) return; actions.selectPart(id); setDesignMode("part"); requestModify(id); },
});

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

/** A pin's point where its body is now (explode moves bodies; the pin's point is at rest). */
function followExplode(p: Pin, host: ModeHost | null): Pin {
  const part = host?.parts().find((x) => x.id === p.partId);
  if (!part) return p;
  const off = explodeOffset(part.scene.object, new Vector3(...p.point));
  return off.lengthSq() === 0 ? p : { ...p, point: [p.point[0] + off.x, p.point[1] + off.y, p.point[2] + off.z] };
}

/** The parts on the sheet: the focused one, or (build view) every visible part with a model. */
export function shownParts(list: readonly PartSummary[], focus: PartSummary | null, scope: ViewScope, hidden: ReadonlySet<string>): PartSummary[] {
  // the layout skeleton (G3) has no model: it is never loaded as a GLB (I4b)
  if (scope === "part") return focus && focus.kind !== "skeleton" ? [focus] : [];
  return list.filter((p) => p.metrics !== null && p.kind !== "skeleton" && (p.part_id === focus?.part_id || !hidden.has(p.part_id)));
}

interface OpenOps { seq: number; partId: string; target: OpsTarget; at: { x: number; y: number } | null; keyboard: boolean; baseVersion: number }

/** The blue marker of a pick (a vertex or point is a dot in the overlay instead). */
function markerOfPick(part: PartSummary, p: Picked): PickMarker {
  if (p.kind === "face") return markerFor(part, p.face, null);
  if (p.kind !== "edge") return null;
  const ring = circleOf(p.edge, part.metrics?.holes);
  return ring ? { kind: "ring", ...ring } : markerFor(part, null, p.edge);
}

/** Typing in a field never switches the viewer's tool. */
function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

interface Chip { seq: number; partId: string; picked: Picked; point: Vec3; at: { x: number; y: number }; baseVersion: number }
interface Comment { partId: string; point: Vec3; faceId: string | null; where: string; at: { x: number; y: number } }
const IDLE: MeasureState = { status: "idle", values: [], error: null };

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

/** Models kept off the sheet for a quick re-select (GPU memory: a few MB each at most). */
export const PARKED_MAX = 24;

export function Viewer(): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<DesignViewerHandle | null>(null);
  const scenes = useRef(new Map<string, PartScene>());
  const loaders = useRef(new Map<string, PartLoader>());
  /** Models no longer on the sheet, kept (newest last) so selecting the part again shows it at once
   *  instead of refetching and re-parsing its GLB (U8: the lag the user felt on every click). */
  const parked = useRef(new Map<string, { scene: PartScene; loader: PartLoader }>());
  const down = useRef<{ x: number; y: number } | null>(null);
  /** Each pick opens a fresh callout (its own op, face / edge and fields). */
  const pickSeq = useRef(0);
  const [mode, setMode] = useState<ViewMode>("shaded");
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);
  const [ops, setOps] = useState<OpenOps | null>(null);
  const [bgOpen, setBgOpen] = useState(false);   // View ▸ Background… (U8's picker)
  const [bounds, setBounds] = useState({ w: 1, h: 1 });
  const [chip, setChip] = useState<Chip | null>(null);
  const [picks, setPicks] = useState<Picked[]>([]);
  const [measured, setMeasured] = useState<MeasureState>(IDLE);
  const measureSeq = useRef(0);
  const [comment, setComment] = useState<Comment | null>(null);
  const [sending, setSending] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const [openPin, setOpenPin] = useState<string | null>(null);
  const [, setFrame] = useState(0);
  const tool = viewerTool.value;
  const pins = buildPins.value;
  const [modeHost, setModeHost] = useState<ModeHost | null>(null);

  const part = activePart.value;
  const partId = part?.part_id ?? null;
  const scope = viewScope.value;
  const tab = viewTab.value;
  const highlight = highlighted.value;
  const byLabel = changingRunLabel.value;
  const shown = shownParts(parts.value, part, scope, hiddenParts.value);
  const loadSig = shown.map((p) => `${p.part_id}@${p.mtime}`).join("|");
  const colorSig = shown.map((p) => `${p.part_id}:${p.color ?? ""}:${JSON.stringify(p.appearance ?? null)}`).join("|");
  const bg = viewerBackground.value;

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let handle: DesignViewerHandle | null = null;
    try {
      // edges follow the background's lightness when it isn't the theme's sheet (R21)
      handle = createViewer(canvas, { edgeColor: () => edgeInk(viewerBackground.peek()) });
    } catch {
      handle = null; // no WebGL: the sheet, title block and metrics still work
    }
    viewerRef.current = handle;
    if (handle && import.meta.env.MODE !== "production") {
      // e2e probe (like assembly's __misesAssembly): a model point (mm) → client pixels, so a
      // test can click a known corner (two-point measure)
      const h = handle;
      (window as unknown as { __misesViewer?: { project(p: Vec3): { x: number; y: number } } }).__misesViewer = {
        project: (p) => { const r = host.getBoundingClientRect(); const [x, y] = h.toScreen(p); return { x: r.left + x, y: r.top + y }; },
      };
    }
    // HTML overlays (pins, measure dots) re-place after each frame; at most one render per frame
    const offView = handle?.onView?.(() => setFrame((n) => n + 1));
    setModeHost(handle?.modeHost?.() ?? null);
    const measure = () => {
      const r = host.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { handle?.resize(r.width, r.height); setBounds({ w: r.width, h: r.height }); }
    };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(host);
    return () => {
      ro?.disconnect();
      offView?.();
      handle?.setPart(null);
      for (const s of scenes.current.values()) s.dispose();
      scenes.current.clear();
      for (const p of parked.current.values()) p.scene.dispose();
      parked.current.clear();
      setModeHost(null);
      handle?.dispose();
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => { viewerRef.current?.setMode(mode); }, [mode]);
  useEffect(() => { viewerRef.current?.setHighlight(highlight); }, [highlight]);
  useEffect(() => { viewerRef.current?.restyle?.(); }, [bg]);

  /** Put the loaded parts on the sheet. */
  function compose(refit: boolean): void {
    const list = shownParts(parts.peek(), activePart.peek(), viewScope.peek(), hiddenParts.peek());
    const focus = activePart.peek()?.part_id ?? null;
    const items = list.flatMap((p) => {
      const sc = scenes.current.get(p.part_id);
      return sc ? [{ id: p.part_id, scene: sc, color: p.color ?? null, focus: p.part_id === focus, material: materialOf(p) }] : [];
    });
    viewerRef.current?.setParts(items, refit, viewScope.peek() === "build");
    const f = focus ? scenes.current.get(focus) : undefined;
    if (f) {
      const s = f.bbox.getSize(new Vector3());
      setSize(`${s.x.toFixed(2)},${s.y.toFixed(2)},${s.z.toFixed(2)}`);
      // after the frame that draws it (this callback runs after the viewer's own): the click-to-
      // model latency the parts perf spec measures
      if (typeof performance !== "undefined" && typeof requestAnimationFrame === "function") {
        requestAnimationFrame(() => { try { performance.mark("mises:viewer-settled", { detail: focus }); } catch { /* no marks */ } });
      }
    } else setSize(null);
  }

  function park(id: string, sc: PartScene, ld: PartLoader): void {
    parked.current.delete(id);
    parked.current.set(id, { scene: sc, loader: ld });
    while (parked.current.size > PARKED_MAX) {
      const [old, p] = parked.current.entries().next().value as [string, { scene: PartScene; loader: PartLoader }];
      parked.current.delete(old);
      p.scene.dispose();
      p.loader.reset();
    }
  }

  // load what is shown (each part only when its mtime changed), park what no longer is
  useEffect(() => {
    const want = new Map(shown.map((p) => [p.part_id, p.mtime]));
    for (const id of [...scenes.current.keys()]) {
      if (want.has(id)) continue;
      const sc = scenes.current.get(id) as PartScene;
      const ld = loaders.current.get(id);
      scenes.current.delete(id);
      loaders.current.delete(id);
      if (ld) park(id, sc, ld);
      else sc.dispose();
    }
    for (const id of want.keys()) {
      const p = parked.current.get(id);
      if (!p || scenes.current.has(id)) continue;
      parked.current.delete(id);
      scenes.current.set(id, p.scene);              // its loader answers "unchanged" while the mtime holds
      loaders.current.set(id, p.loader);
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
  useEffect(() => { compose(true); setOps(null); setChip(null); }, [partId, scope]);
  useEffect(() => { compose(false); }, [colorSig]);

  // picks name one build's faces / edges: a rebuilt model (or another part) starts measure afresh.
  // Keyed on mtime, not version: a list that only now learns the version is no rebuild (a pick
  // made on an older version still gets the core's 409 and a "pick again").
  useEffect(() => { setPicks([]); }, [partId, part?.mtime]);

  // a new tool starts clean, at once: a click right after the switch (before an after-paint
  // effect would run) must not be wiped
  useEffect(() => {
    let last = viewerTool.peek();
    return effect(() => {
      const t = viewerTool.value;
      if (t === last) return;
      last = t;
      setChip(null); setPicks([]); setComment(null); setCommentError(null); setOps(null);
    });
  }, []);

  // "Modify…" from the part's menu: the ops form for that part (choose the face / edge in it)
  const modify = modifyRequest.value;
  useEffect(() => {
    const req = takeModify();
    if (!req) return;
    const p = parts.peek().find((x) => x.part_id === req.partId);
    if (!p || p.metrics === null || (p.kind ?? "part") !== "part") return;
    viewerTool.value = "select";
    viewTab.value = "model";
    setChip(null);
    setOps({ seq: ++pickSeq.current, partId: p.part_id, target: { face: null, edge: null, point: null }, at: null, keyboard: true, baseVersion: p.version ?? 0 });
  }, [modify?.seq]);

  // the blue markers: the open ops target (OpsMenu sets it), else measure's picks, else the
  // selection label's pick; nothing is marked without one
  useEffect(() => {
    const v = viewerRef.current;
    if (!v || ops) return;
    const focus = activePart.peek();
    const list = !focus ? [] : tool === "measure" ? picks : chip && chip.partId === focus.part_id ? [chip.picked] : [];
    const markers = focus ? list.map((p) => markerOfPick(focus, p)).filter((m) => m !== null) : [];
    if (v.setMarkers) v.setMarkers(markers); else v.setMarker(markers[0] ?? null);
  }, [ops, picks, chip, tool, partId]);

  // Measure opened on a part: the core loads its shape in a warm worker before the first pick
  // (a cold measure pays ~1.5 s of imports, a warm one milliseconds); best effort
  useEffect(() => {
    if (tool !== "measure" || !partId || part?.metrics === null) return;
    try { void actions.client().warmMeasure?.(partId).catch(() => undefined); } catch { /* no core bound */ }
  }, [tool, partId, part?.mtime]);

  // measure: the core's exact values for the picks (one pick shows its metrics values meanwhile)
  useEffect(() => {
    const seq = ++measureSeq.current;
    const focus = activePart.peek();
    if (tool !== "measure" || !focus || picks.length === 0) { setMeasured(IDLE); return; }
    setMeasured({ status: "busy", values: provisionalValues(picks), error: null });
    let client;
    try { client = actions.client(); } catch { return; }
    void client.measurePart(focus.part_id, picks.map(refOf), focus.version ?? undefined).then(
      (r) => { if (seq === measureSeq.current) setMeasured({ status: "done", values: r.values, error: null }); },
      (e: unknown) => {
        if (seq !== measureSeq.current) return;
        const msg = e instanceof CoreError && e.status === 409 ? "This part just changed. Pick again on the new version."
          : `Couldn't measure exactly: ${e instanceof Error ? e.message : String(e)}`;
        setMeasured({ status: "error", values: provisionalValues(picks), error: msg });
      },
    );
  }, [picks, tool]);

  // M toggles measure, Escape goes back to Select (never while typing in a field)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // (the viewer is mounted only on the Design page; a modal dialog keeps its own keys)
      if (e.defaultPrevented || typing(e.target) || viewTab.peek() !== "model" || modalOpen()) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "m" || e.key === "M") { e.preventDefault(); viewerTool.value = viewerTool.peek() === "measure" ? "select" : "measure"; }
      else if (e.key === "Escape" && viewerTool.peek() !== "select") { e.preventDefault(); viewerTool.value = "select"; }
      else if (e.key === "Escape") setChip(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function sendComment(text: string): Promise<void> {
    const c = comment;
    const build = activeBuildId.peek() ?? parts.peek().find((p) => p.part_id === c?.partId)?.project_id ?? null;
    if (!c) return;
    if (!build) { setCommentError("Open a build first: the comment goes to its agent."); return; }
    setSending(true);
    setCommentError(null);
    try {
      const r = await actions.client().sendFeedback(build, { part_id: c.partId, point: c.point, ...(c.faceId ? { face_id: c.faceId } : {}), text });
      addLocalPin({ buildId: build, runId: r.run_id, stepId: r.step_id, partId: c.partId, point: c.point, faceId: c.faceId, text, ts: Date.now() / 1000 });
      // the lead's thread shows the comment (queued there while the lead works)
      actions.selectRun(r.run_id);
      setComment(null);
      viewerTool.value = "select";
    } catch (e) {
      setCommentError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

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
    const mode: ViewerTool = viewerTool.peek();
    if (!hit) {
      setOps(null); setChip(null); setOpenPin(null);
      if (mode === "measure" && !e.shiftKey) setPicks([]);
      if (mode === "comment") setComment(null);
      return;
    }
    const target = parts.peek().find((p) => p.part_id === hit.partId);
    if (!target) return;
    const toScreen = (p: Vec3) => v.toScreen(p);
    if (mode === "comment") {
      const pk = pickTarget(target.metrics, hit, toScreen, { asPoint: true });
      const face = pk?.kind === "point" ? pk.face : null;
      setCommentError(null);
      setComment({ partId: target.part_id, point: hit.point, faceId: face?.id ?? null, at: { x, y },
        where: `${formatPartName(target)}${face ? ` · ${labelFor({ kind: "face", face })}` : ""}` });
      return;
    }
    if (hit.partId !== activePart.peek()?.part_id) { actions.selectPart(hit.partId); return; }
    if (mode === "measure") {
      const pk = pickTarget(target.metrics, hit, toScreen);
      if (pk) setPicks((prev) => togglePick(prev, pk, e.shiftKey));
      return;
    }
    if (!ops) {
      // Select: say what was clicked; "Modify…" on the label opens the feature ops there
      const pk = pickTarget(target.metrics, hit, toScreen);
      setChip(pk ? { seq: ++pickSeq.current, partId: target.part_id, picked: pk, point: hit.point, at: { x, y }, baseVersion: target.version ?? 0 } : null);
      return;
    }
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

  /** The label's "Modify…": the ops callout on that face / edge, at the click. */
  function modifyChip(c: Chip): void {
    const face = c.picked.kind === "face" ? c.picked.face : null;
    const edge = c.picked.kind === "edge" ? c.picked.edge : null;
    setChip(null);
    setOps({ seq: ++pickSeq.current, partId: c.partId, target: { face, edge, point: face ? c.point : null }, at: c.at, keyboard: false, baseVersion: c.baseVersion });
  }

  const v = viewerRef.current;
  const shownIds = new Set(shown.map((p) => p.part_id));
  // m11: a pin rides with its exploded body (it used to hang in mid-air at its rest point)
  const shownPins: Pin[] = tab === "model" ? pins.filter((p) => shownIds.has(p.partId)).map((p) => followExplode(p, modeHost)) : [];
  const dots: Vec3[] = tool === "measure" ? picks.flatMap((p) => (p.kind === "vertex" || p.kind === "point" ? [p.point] : [])) : [];

  return (
    <div class="vp" ref={hostRef} data-testid="viewer" data-tab={tab} style={backgroundStyle(bg)} data-bg-mode={bg.mode}
      data-bg={isLight(bg) === null ? undefined : isLight(bg) ? "light" : "dark"}>
      <canvas
        ref={canvasRef}
        class="vp-canvas"
        data-testid="viewer-canvas"
        data-bbox-size={size ?? undefined}
        data-highlight={highlight ? "on" : "off"}
        data-mode={mode}
        data-scope={scope}
        data-shown={shown.map((p) => `${p.part_id}${p.color ? `:${p.color}` : ""}`).join(" ") || undefined}
        data-tool={tool}
        onPointerDown={(e) => { if (e.button === 0) down.current = { x: e.clientX, y: e.clientY }; }}
        onPointerUp={onPointerUp}
      />
      <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
      {/* the sheet corner holds only Part | Assembly (U2b), above the Code view (M-1); the script
          opens from View ▸ Part script, and while it shows a "Back to the model" button sits here */}
      <div class="vp-corner">
        <ModeSwitch />
        {tab === "code" && (
          <button type="button" class="vt-btn vp-back" onClick={() => { viewTab.value = "model"; }}>Back to the model</button>
        )}
      </div>
      <span class="label vp-fig">{scope === "build" ? "FIG. 1 — BUILD" : "FIG. 1 — DESIGN"}</span>
      <ViewerTools mode={mode} onMode={setMode} scope={scope} onScope={(s) => { viewScope.value = s; }}
        code={tab === "code"} onCode={(on) => { viewTab.value = on ? "code" : "model"; }}
        lead={tab !== "code" ? <AllPartsToggle /> : null}
        canEdit={canEdit && tab === "model"}
        onEdit={() => { if (part) setOps({ seq: ++pickSeq.current, partId: part.part_id, target: { face: null, edge: null, point: null }, at: null, keyboard: true, baseVersion: part.version ?? 0 }); }}
        extra={[
          // U2a's click tools, in U2b's View menu: pressed again, back to Select
          { id: "measure", label: "Measure", hint: "M", checked: tool === "measure", disabled: !(part !== null && tab === "model"),
            onSelect: () => { viewerTool.value = viewerTool.peek() === "measure" ? "select" : "measure"; } },
          { id: "comment", label: "Comment on the model", checked: tool === "comment", disabled: !(part !== null && tab === "model"),
            onSelect: () => { viewerTool.value = viewerTool.peek() === "comment" ? "select" : "comment"; } },
          // G4's view modes (X-ray, section, explode, reference overlay) live in the same menu
          ...(part !== null && tab === "model" ? designModeItems() : []),
          { id: "background", label: "Background…", onSelect: () => setBgOpen(true) },
        ]} />
      {part !== null && part.kind !== "skeleton" && <DesignViewModes host={modeHost} focusId={partId} partIds={shown.map((p) => p.part_id)} />}
      {part === null && <div class="vp-empty label">NO PART · ask an agent to create one</div>}
      {part?.kind === "skeleton" && tab === "model" && <SkeletonPanel part={part} />}
      {tab === "model" && tool === "comment" && !comment && <div class="vx-mode-hint label" role="status">Click the model where something should change</div>}
      {tab === "model" && v && (shownPins.length > 0 || dots.length > 0) && (
        <PinLayer pins={shownPins} dots={dots} toScreen={(p) => v.toScreen(p)} bounds={bounds} openPin={openPin} onOpen={setOpenPin}
          onThread={(p) => { actions.selectRun(p.runId); setOpenPin(null); }} />
      )}
      {tab === "model" && tool === "select" && chip && !ops && (
        <SelectionLabel key={chip.seq} picked={chip.picked} at={chip.at} bounds={bounds}
          canModify={canEdit && chip.picked.kind !== "vertex" && chip.picked.kind !== "point"}
          onModify={() => modifyChip(chip)} onClose={() => setChip(null)} />
      )}
      {tab === "model" && tool === "measure" && part && (
        <MeasurePanel picks={picks} state={measured} onClear={() => setPicks([])} onClose={() => { viewerTool.value = "select"; }} />
      )}
      {tab === "model" && tool === "comment" && comment && (
        <CommentBox key={`${comment.at.x},${comment.at.y}`} at={comment.at} bounds={bounds} where={comment.where} busy={sending} error={commentError}
          onSend={(t) => { void sendComment(t); }} onCancel={() => { setComment(null); setCommentError(null); }} />
      )}
      {ops && opsPart && tab === "model" && (
        <OpsMenu key={ops.seq} part={opsPart} target={ops.target} baseVersion={ops.baseVersion} keyboard={ops.keyboard} at={ops.at} bounds={bounds}
          onTarget={(f, ed) => viewerRef.current?.setMarker(markerFor(opsPart, f, ed))}
          onClose={() => { setOps(null); canvasRef.current?.focus(); }} />
      )}
      {tab === "code" && (part
        ? <SourceEditor key={part.part_id} part={part} />
        : <div class="src src-empty"><p class="faint">Select a part to see its script.</p></div>)}
      <BackgroundMenu open={bgOpen} onOpenChange={setBgOpen} />
      <TitleBlock part={part} runLabel={byLabel} error={error} />
    </div>
  );
}
