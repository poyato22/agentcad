// The build view's click tools (Plan 10), beside Select:
//   · Measure (E16, Onshape's rules): a click snaps to a vertex, an edge (a circle, an arc) or a
//     face of the part under it, as the part viewer does; each click adds to the selection, a pick
//     clicked again is dropped, empty space or Esc clears. Two picks are measured on the exact
//     B-rep of their posed parts (POST /measure/pair): the distance with ΔX / ΔY / ΔZ and the
//     angle, and Minimum / Center / Maximum when a circle, an arc or a cylinder is picked, drawn
//     on the model; one pick shows its own values, several edges or faces their total.
//   · Comment (E35): a click drops a pin on that instance; the note goes to the build's lead with
//     the instance and the world point. Pins (sent from here or from the part on its own) follow
//     the poses, the explode and the layout: each is stored in its part's own frame.
import { signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Vec3 } from "../../api/types";
import { partName } from "../../lib/names";
import { activeBuildId } from "../../state/builds";
import { actions, parts } from "../../state/store";
import type { MeasureDistances, MeasureValue } from "../../api/measure";
import { CommentBox, DimensionLine, DistanceModes, distanceMode, MeasureRows } from "../viewer/Overlays";
import { dimLabel, labelFor, measurable, pickTarget, provisionalValues, refOf, valuesForMode, type Picked } from "../viewer/interact";
import { addLocalPin, buildPins, type Pin } from "../viewer/pins";
import type { AssemblyController } from "./controller";
import { transformPoint } from "./kinematics";
import type { AssemblyHit, AssemblyScene, SceneMark } from "./scene";

export type AsmTool = "select" | "measure" | "comment";
/** The build view's tool (the pill's Measure / Comment; Esc goes back to Select). */
export const asmTool = signal<AsmTool>("select");

interface APick { iid: string; part: Vec3; normal: Vec3; picked: Picked }
interface AMeasured { status: "idle" | "busy" | "done" | "error"; values: MeasureValue[]; distances?: MeasureDistances; error: string | null }
const IDLE_M: AMeasured = { status: "idle", values: [], error: null };
const pickKey = (p: APick) => `${p.iid}|${JSON.stringify(refOf(p.picked))}`;
interface AComment { iid: string | null; part: Vec3; world: Vec3; at: { x: number; y: number }; where: string }

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const len = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const mm = (v: number) => `${nf.format(Math.abs(v) < 5e-3 ? 0 : v).replace(/^-/, "−")} mm`;

/** Two spots (assembled, world mm) and their surface normals → what the panel lists. */
export function measureBetween(a: { p: Vec3; n: Vec3 | null }, b: { p: Vec3; n: Vec3 | null }): { key: string; label: string; text: string }[] {
  const d = sub(b.p, a.p);
  const out = [
    { key: "distance", label: "Distance", text: mm(len(d)) },
    { key: "dx", label: "ΔX", text: mm(d[0]) }, { key: "dy", label: "ΔY", text: mm(d[1]) }, { key: "dz", label: "ΔZ", text: mm(d[2]) },
  ];
  if (a.n && b.n && len(a.n) > 1e-9 && len(b.n) > 1e-9) {
    const c = (a.n[0] * b.n[0] + a.n[1] * b.n[1] + a.n[2] * b.n[2]) / (len(a.n) * len(b.n));
    const deg = (Math.acos(Math.min(1, Math.max(-1, c))) * 180) / Math.PI;
    out.push({ key: "angle", label: "Angle", text: `${nf.format(deg)}°` });
  }
  return out;
}

export interface AssemblyTools {
  tool: AsmTool;
  /** A click on the canvas while a tool is on: true when the tool took it. */
  click(ev: PointerEvent): boolean;
  /** The points the overlay places each frame (picks, the hover spot, pins). */
  marks(): SceneMark[];
  /** The pin under a numbered flag was clicked. */
  openPin(key: string | null): void;
  /** Esc: true when it closed something of the tools. */
  escape(): boolean;
  overlay: JSX.Element | null;
}

export function useAssemblyTools(scene: AssemblyScene | null, ctl: AssemblyController, room: { w: number; h: number; x0: number }): AssemblyTools {
  const tool = asmTool.value;
  const [hover, setHover] = useState<AssemblyHit | null>(null);
  const [picks, setPicks] = useState<APick[]>([]);
  const [comment, setComment] = useState<AComment | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [measured, setMeasured] = useState<AMeasured>(IDLE_M);
  const mseq = useRef(0);
  const state = useRef({ hover, picks, comment, open });
  state.current = { hover, picks, comment, open };

  // the hover preview, only while a tool is on
  useEffect(() => {
    if (!scene || tool === "select") { setHover(null); scene?.setHovered(null); return; }
    const off = scene.onHover((h) => {
      setHover((prev) => (prev?.instanceId === h?.instanceId && prev?.point.every((c, i) => Math.abs(c - (h?.point[i] ?? NaN)) < 1e-3) ? prev : h));
      scene.setHovered(h?.instanceId ?? null);
    });
    return () => { off(); scene.setHovered(null); };
  }, [scene, tool]);
  useEffect(() => { if (tool !== "measure") setPicks([]); if (tool === "measure") { setComment(null); setError(null); } }, [tool]);

  const toPart = (h: AssemblyHit): { part: Vec3; normal: Vec3 } | null => {
    const part = scene?.worldToPart(h.instanceId, h.point);
    const tip = scene?.worldToPart(h.instanceId, [h.point[0] + h.normal[0], h.point[1] + h.normal[1], h.point[2] + h.normal[2]]);
    return part && tip ? { part, normal: sub(tip, part) } : null;
  };
  /** Where a part-frame spot sits assembled (the instance's pose; never the explode). */
  const assembled = (iid: string, p: Vec3): Vec3 | null => {
    const t = ctl.currentPose()[iid];
    return t ? transformPoint(t, p) : null;
  };
  const turned = (iid: string, n: Vec3): Vec3 | null => {
    const t = ctl.currentPose()[iid];
    return t ? sub(transformPoint(t, n), transformPoint(t, [0, 0, 0])) : null;
  };

  /** The instance's world box as drawn (assembled: its pose, never the explode), mm. */
  const drawnBox = (iid: string): { min: Vec3; max: Vec3 } | null => {
    const b = scene?.partBox(iid);
    if (!b) return null;
    const lo: Vec3 = [Infinity, Infinity, Infinity];
    const hi: Vec3 = [-Infinity, -Infinity, -Infinity];
    for (const x of [b.min[0], b.max[0]]) for (const y of [b.min[1], b.max[1]]) for (const z of [b.min[2], b.max[2]]) {
      const w = assembled(iid, [x, y, z]);
      if (!w) return null;
      for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], w[k]); hi[k] = Math.max(hi[k], w[k]); }
    }
    const r2 = (v: number) => Math.round(v * 100) / 100;
    return { min: lo.map(r2) as Vec3, max: hi.map(r2) as Vec3 };
  };

  /** The instance's part metrics (the part viewer's faces, edges and their ids). */
  const metricsOf = (iid: string) => {
    const inst = ctl.instance(iid);
    return inst ? parts.peek().find((p) => p.part_id === inst.part_id) ?? null : null;
  };
  /** A hit → what a measure click takes, by the part viewer's rules (a vertex within a few px,
   *  else an edge, else the face), in the part's own frame; a bare spot when it has no metrics. */
  const snap = (h: AssemblyHit): APick | null => {
    const local = toPart(h);
    if (!local || !scene) return null;
    const toScreen = (p: Vec3): [number, number] => {
      const w = scene.partToWorld(h.instanceId, p);
      const c = w ? scene.toCanvas(w) : { x: NaN, y: NaN };
      return [c.x, c.y];
    };
    const part = metricsOf(h.instanceId);
    const picked = pickTarget(part?.metrics ?? null, { partId: part?.part_id ?? "", point: local.part, normal: local.normal }, toScreen)
      ?? { kind: "point" as const, point: local.part, face: null };
    return { iid: h.instanceId, ...local, picked };
  };
  /** Where a pick's dot sits in its part's frame: a vertex itself, else the clicked spot. */
  const dotOf = (p: APick): Vec3 => (p.picked.kind === "vertex" || p.picked.kind === "point" ? p.picked.point : p.part);

  /** H1: a click anywhere offers the inline Comment bubble at the click: on a part (pinned to the
   *  instance) or on empty space (pinned to the world point). */
  function offerComment(ev: PointerEvent, h: AssemblyHit | null): void {
    if (!scene) return;
    const r = (ev.target as HTMLElement | null)?.getBoundingClientRect?.() ?? { left: 0, top: 0 };
    const at = { x: ev.clientX - r.left, y: ev.clientY - r.top };
    setError(null);
    if (!h) {
      const w = scene.pointOnView(ev.clientX, ev.clientY);
      setComment({ iid: null, part: w, world: w, at, where: "Empty space" });
      return;
    }
    const local = toPart(h);
    if (!local) return;
    const inst = ctl.instance(h.instanceId);
    const part = inst ? parts.peek().find((p) => p.part_id === inst.part_id) : undefined;
    setComment({
      iid: h.instanceId, part: local.part, world: assembled(h.instanceId, local.part) ?? h.point, at,
      where: `${inst?.name ?? "Part"}${part && partName(part) !== inst?.name ? ` · ${partName(part)}` : ""}`,
    });
  }

  function click(ev: PointerEvent): boolean {
    if (!scene) return false;
    const h = scene.pickAt(ev.clientX, ev.clientY);
    if (tool === "select") { offerComment(ev, h); return false; } // the part is selected as well
    if (tool === "comment" && !h) { setComment(null); return true; }
    if (tool === "measure" && !h) { setPicks([]); return true; }   // Onshape: empty space clears
    if (!h) return true;
    if (tool === "measure") {
      const pk = snap(h);
      if (!pk) return true;
      const k = pickKey(pk);
      setPicks((prev) => (prev.some((x) => pickKey(x) === k) ? prev.filter((x) => pickKey(x) !== k) : [...prev, pk]));
      return true;
    }
    offerComment(ev, h);
    return true;
  }

  async function send(text: string): Promise<void> {
    const c = state.current.comment;
    const inst = c?.iid ? ctl.instance(c.iid) : null;
    const build = ctl.projectId.peek() ?? activeBuildId.peek();
    if (!c || (c.iid && !inst)) return;
    if (!build) { setError("Open a build first: the comment goes to its agent."); return; }
    setSending(true);
    setError(null);
    const r6 = (v: number) => Math.round(v * 1e4) / 1e4;
    const point = c.part.map(r6) as Vec3;
    // P11 F4: the instance's box as drawn (its mesh's box through its pose), so the core can
    // tell the agent when what the user sees is not where the model is
    const world_bbox = c.iid ? drawnBox(c.iid) : null;
    try {
      const world = c.world.map(r6) as Vec3;
      const res = await actions.client().sendFeedback(build, inst && c.iid ? {
        part_id: inst.part_id, point, text, instance_id: c.iid, world_point: world,
        ...(world_bbox ? { world_bbox } : {}),
      } : { part_id: "", point: world, text, world_point: world });
      addLocalPin({ buildId: build, runId: res.run_id, stepId: res.step_id, partId: inst?.part_id ?? "", point, faceId: null, text, ts: Date.now() / 1000,
        instanceId: c.iid, worldPoint: c.iid ? null : world });
      actions.selectRun(res.run_id);
      setComment(null);
      asmTool.value = "select";
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  const ids = new Set((ctl.data.value?.instances ?? []).map((i) => i.id));
  const pins: Pin[] = buildPins.value.filter((p) => (p.instanceId && ids.has(p.instanceId)) || p.worldPoint);

  function marks(): SceneMark[] {
    const out: SceneMark[] = [];
    const s = state.current;
    if (!scene) return out;
    s.picks.forEach((p, i) => { const w = scene.partToWorld(p.iid, dotOf(p)); if (w) out.push({ key: `${i}`, point: w, text: `${i + 1}`, kind: "pick" }); });
    if (s.hover) {
      // the spot a click would take: a vertex it would snap to, else the point under the pointer
      const pk = tool === "measure" ? snap(s.hover) : null;
      const w = pk && pk.picked.kind === "vertex" ? scene.partToWorld(pk.iid, pk.picked.point) : null;
      out.push({ key: "h", point: w ?? s.hover.point, text: "", kind: "hover" });
    }
    for (const p of pins) { const w = p.worldPoint ?? scene.partToWorld(p.instanceId as string, p.point); if (w) out.push({ key: p.key, point: w, text: `${p.n}`, kind: "pin" }); }
    return out;
  }

  function escape(): boolean {
    const s = state.current;
    if (s.open) { setOpen(null); return true; }
    if (s.comment) { setComment(null); return true; }
    if (s.picks.length) { setPicks([]); return true; }
    if (tool !== "select") { asmTool.value = "select"; return true; }
    return false;
  }

  // exact values from the core: one pick on its part; two on their posed parts (the assembled
  // pose, never the explode); several edges / faces their total from the metrics
  useEffect(() => {
    const seq = ++mseq.current;
    if (tool !== "measure" || picks.length === 0) { setMeasured(IDLE_M); return; }
    const sel = picks.map((p) => p.picked);
    if (picks.length > 2 || !measurable(sel)) {
      const total = sel.every((p) => p.kind === "edge")
        ? [{ key: "total_length", label: "Total length", value: sel.reduce((t, p) => t + (p.kind === "edge" ? p.edge.length : 0), 0), unit: "mm" }]
        : sel.every((p) => p.kind === "face")
          ? [{ key: "total_area", label: "Total area", value: sel.reduce((t, p) => t + (p.kind === "face" ? p.face.area : 0), 0), unit: "mm²" }] : [];
      setMeasured({ status: "done", values: total, error: null });
      return;
    }
    const inst = picks.map((p) => ctl.instance(p.iid));
    const poses = picks.map((p) => ctl.currentPose()[p.iid]);
    let client;
    try { client = actions.client(); } catch { setMeasured(IDLE_M); return; }
    if (picks.length === 1) {
      const p = picks[0];
      setMeasured({ status: "busy", values: provisionalValues([p.picked]), error: null });
      if (p.picked.kind === "vertex" || p.picked.kind === "point") {
        // a vertex's coordinates in the assembly's frame (Onshape shows where it sits assembled)
        const w = assembled(p.iid, p.picked.point);
        setMeasured({ status: "done", values: w ? provisionalValues([{ kind: "vertex", point: w }]) : [], error: null });
        return;
      }
      if (!inst[0]) return;
      void client.measurePart(inst[0].part_id, [refOf(p.picked)]).then(
        (r) => { if (seq === mseq.current) setMeasured({ status: "done", values: r.values, error: null }); },
        (e: unknown) => { if (seq === mseq.current) setMeasured({ status: "error", values: provisionalValues([p.picked]), error: `Couldn't measure exactly: ${e instanceof Error ? e.message : String(e)}` }); });
      return;
    }
    const [a, b] = picks.map((p) => ({ p: assembled(p.iid, dotOf(p)), n: turned(p.iid, p.normal) }));
    // until the core answers: the two spots (a vertex is exact; an edge or a face is where it was clicked)
    const rough: MeasureValue[] = a.p && b.p ? [{ key: "distance", label: "Distance (clicked spots)", value: len(sub(b.p, a.p)), unit: "mm" }] : [];
    setMeasured({ status: "busy", values: rough, error: null });
    if (!inst[0] || !inst[1] || !poses[0] || !poses[1]) return;
    void client.measurePair(
      { part_id: inst[0].part_id, ref: refOf(picks[0].picked), pose: poses[0] },
      { part_id: inst[1].part_id, ref: refOf(picks[1].picked), pose: poses[1] },
    ).then(
      (r) => { if (seq === mseq.current) setMeasured({ status: "done", values: r.values, distances: r.distances, error: null }); },
      (e: unknown) => { if (seq === mseq.current) setMeasured({ status: "error", values: rough, error: `Couldn't measure exactly: ${e instanceof Error ? e.message : String(e)}` }); });
  }, [picks, tool]);

  const values = valuesForMode(measured.values, measured.distances, distanceMode.value);
  const span = measured.distances ? measured.distances[distanceMode.value] ?? measured.distances.min : null;
  const pinOpen = pins.find((p) => p.key === open) ?? null;
  const name = (iid: string) => ctl.instance(iid)?.name ?? "part";
  const overlay = (
    <>
      {tool === "measure" && (
        <section class="vx-panel asm-measure" data-testid="assembly-measure" aria-label="Measure">
          <header class="vx-panel-head">
            <span class="label">Measure</span>
            <button type="button" class="vx-x" aria-label="Close measure" onClick={() => { asmTool.value = "select"; }}>×</button>
          </header>
          {picks.length === 0 ? <p class="vx-hint faint">Click a vertex, an edge, a circle or a face; click more to measure between them.</p> : (
            <>
              <ul class="vx-picks">{picks.map((p, i) => <li key={i}><span class="vx-n">{i + 1}</span>{name(p.iid)} · {labelFor(p.picked)}</li>)}</ul>
              {measured.status === "busy" && <span class="faint vx-busy" role="status">measuring…</span>}
              <DistanceModes distances={measured.distances} />
              {values.length > 0 && <MeasureRows values={values} exact={measured.status === "done"} />}
              {picks.length > 2 && !measurable(picks.map((p) => p.picked)) && <p class="vx-hint faint">With more than two picks, pick only edges (total length) or only faces (total area).</p>}
              {measured.error && <p class="vx-error" role="alert">{measured.error}</p>}
              <div class="vx-actions">
                <span class="faint vx-hint">{picks.length === 1 ? "Click a second item" : "Click a pick again to drop it · Esc or empty space clears"}</span>
                <button type="button" class="btn btn-ghost" onClick={() => setPicks([])}>Clear</button>
              </div>
            </>
          )}
        </section>
      )}
      {tool === "measure" && scene && picks.length === 2 && span && (
        <DimensionLine span={span} bounds={room} label={dimLabel(span)}
          toScreen={(p) => { const c = scene.toCanvas(p); return [c.x, c.y]; }} />
      )}
      {tool === "comment" && !comment && <div class="vx-mode-hint label" role="status">Click a part where something should change</div>}
      {comment && (
        <CommentBox key={`${comment.at.x},${comment.at.y}`} at={comment.at} bounds={room} where={comment.where} busy={sending} error={error}
          onSend={(t) => { void send(t); }} onCancel={() => { setComment(null); setError(null); }} />
      )}
      {pinOpen && scene && (() => {
        const w = pinOpen.worldPoint ?? scene.partToWorld(pinOpen.instanceId as string, pinOpen.point);
        const s = w ? scene.toCanvas(w) : { x: 0, y: 0 };
        return (
          <div class="vx-pin-note" style={{ left: `${Math.min(Math.max(room.x0 + 8, s.x + 14), room.w - 248)}px`, top: `${Math.min(Math.max(8, s.y + 4), room.h - 90)}px` }}
            data-testid="pin-note" role="dialog" aria-label={`Comment ${pinOpen.n}`}>
            <p class="vx-pin-text">{pinOpen.text}</p>
            <div class="vx-actions">
              <button type="button" class="vx-link" onClick={() => { actions.selectRun(pinOpen.runId); setOpen(null); }}>Show in the thread</button>
              <button type="button" class="vx-x" aria-label="Close" onClick={() => setOpen(null)}>×</button>
            </div>
          </div>
        );
      })()}
    </>
  );
  return { tool, click, marks, openPin: setOpen, escape, overlay };
}
