// The build view's click tools (Plan 10), beside Select:
//   · Measure (E16): hovering tints the part under the pointer and shows the spot a click takes
//     (a big dot); two clicks measure between them — the distance, its X / Y / Z and the angle
//     between the two surfaces — in the assembled pose; a third starts a new pair; Esc clears.
//     (The part viewer measures exact B-rep edges and faces; here it is any two spots on any parts.)
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
import { CommentBox } from "../viewer/Overlays";
import { addLocalPin, buildPins, type Pin } from "../viewer/pins";
import type { AssemblyController } from "./controller";
import { transformPoint } from "./kinematics";
import type { AssemblyHit, AssemblyScene, SceneMark } from "./scene";

export type AsmTool = "select" | "measure" | "comment";
/** The build view's tool (the pill's Measure / Comment; Esc goes back to Select). */
export const asmTool = signal<AsmTool>("select");

interface APick { iid: string; part: Vec3; normal: Vec3 }
interface AComment { iid: string; part: Vec3; world: Vec3; at: { x: number; y: number }; where: string }

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
  useEffect(() => { if (tool !== "measure") setPicks([]); if (tool !== "comment") { setComment(null); setError(null); } }, [tool]);

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

  function click(ev: PointerEvent): boolean {
    if (tool === "select" || !scene) return false;
    const h = scene.pickAt(ev.clientX, ev.clientY);
    if (!h) { if (tool === "comment") setComment(null); return true; }
    const local = toPart(h);
    if (!local) return true;
    if (tool === "measure") {
      const pk = { iid: h.instanceId, ...local };
      setPicks((prev) => (prev.length >= 2 ? [pk] : [...prev, pk]));
      return true;
    }
    const inst = ctl.instance(h.instanceId);
    const part = inst ? parts.peek().find((p) => p.part_id === inst.part_id) : undefined;
    const r = (ev.target as HTMLElement | null)?.getBoundingClientRect?.() ?? { left: 0, top: 0 };
    setError(null);
    setComment({
      iid: h.instanceId, part: local.part, world: assembled(h.instanceId, local.part) ?? h.point,
      at: { x: ev.clientX - r.left, y: ev.clientY - r.top },
      where: `${inst?.name ?? "Part"}${part && partName(part) !== inst?.name ? ` · ${partName(part)}` : ""}`,
    });
    return true;
  }

  async function send(text: string): Promise<void> {
    const c = state.current.comment;
    const inst = c ? ctl.instance(c.iid) : null;
    const build = ctl.projectId.peek() ?? activeBuildId.peek();
    if (!c || !inst) return;
    if (!build) { setError("Open a build first: the comment goes to its agent."); return; }
    setSending(true);
    setError(null);
    const r6 = (v: number) => Math.round(v * 1e4) / 1e4;
    const point = c.part.map(r6) as Vec3;
    try {
      const res = await actions.client().sendFeedback(build, { part_id: inst.part_id, point, text, instance_id: c.iid, world_point: c.world.map(r6) as Vec3 });
      addLocalPin({ buildId: build, runId: res.run_id, stepId: res.step_id, partId: inst.part_id, point, faceId: null, text, ts: Date.now() / 1000, instanceId: c.iid });
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
  const pins: Pin[] = buildPins.value.filter((p) => p.instanceId && ids.has(p.instanceId));

  function marks(): SceneMark[] {
    const out: SceneMark[] = [];
    const s = state.current;
    if (!scene) return out;
    s.picks.forEach((p, i) => { const w = scene.partToWorld(p.iid, p.part); if (w) out.push({ key: `${i}`, point: w, text: `${i + 1}`, kind: "pick" }); });
    if (s.hover) out.push({ key: "h", point: s.hover.point, text: "", kind: "hover" });
    for (const p of pins) { const w = scene.partToWorld(p.instanceId as string, p.point); if (w) out.push({ key: p.key, point: w, text: `${p.n}`, kind: "pin" }); }
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

  let values: { key: string; label: string; text: string }[] = [];
  if (picks.length === 2) {
    const [a, b] = picks.map((p) => ({ p: assembled(p.iid, p.part), n: turned(p.iid, p.normal) }));
    if (a.p && b.p) values = measureBetween({ p: a.p, n: a.n }, { p: b.p, n: b.n });
  }
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
          {picks.length === 0 ? <p class="vx-hint faint">Click a spot on a part, then a second one to measure between them.</p> : (
            <>
              <ul class="vx-picks">{picks.map((p, i) => <li key={i}><span class="vx-n">{i + 1}</span>{name(p.iid)}</li>)}</ul>
              {values.length > 0 && (
                <dl class="vx-values" data-exact="">
                  {values.map((v) => <div key={v.key} class="vx-row" data-value={v.key}><dt>{v.label}</dt><dd>{v.text}</dd></div>)}
                </dl>
              )}
              <div class="vx-actions">
                <span class="faint vx-hint">{picks.length === 1 ? "Click a second spot" : "A click starts a new pair · Esc clears"}</span>
                <button type="button" class="btn btn-ghost" onClick={() => setPicks([])}>Clear</button>
              </div>
            </>
          )}
        </section>
      )}
      {tool === "comment" && !comment && <div class="vx-mode-hint label" role="status">Click a part where something should change</div>}
      {tool === "comment" && comment && (
        <CommentBox key={`${comment.at.x},${comment.at.y}`} at={comment.at} bounds={room} where={comment.where} busy={sending} error={error}
          onSend={(t) => { void send(t); }} onCancel={() => { setComment(null); setError(null); }} />
      )}
      {pinOpen && scene && (() => {
        const w = scene.partToWorld(pinOpen.instanceId as string, pinOpen.point);
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
