// Assembly mode of the Design page: the sheet with every part of the build's assembly posed by its
// joints, a gizmo to drag them, face picking for new joints, interference tags, and the panel.
import { effect } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AssemblyPart } from "../../api/assembly";
import { onCoreEvent } from "../../api/events";
import { isTrustedKey } from "../../lib/keys";
import { actions, parts as storeParts } from "../../state/store";
import type { PartScene } from "../viewer/loadPart";
import { PartLoader } from "../viewer/partLoader";
import "../viewer/viewer.css";
import { AssemblyPanel, fmtVolume, KIND_LABEL } from "./AssemblyPanel";
import { AssemblyController, valueAt, withComponent } from "./controller";
import { jointWorldFrame, transformPoint } from "./kinematics";
import { modalOpen } from "../pages/router";
import { ModeSwitch } from "./mode";
import { faceOptions, pickOption, type FaceOption } from "./pick";
import { createAssemblyScene, type AssemblyScene, type GizmoSpec, type OverlayLabel, type PickMarker } from "./scene";
import { onBuildSwitch } from "../../state/builds";
import "./assembly.css";

/** One per app: leaving assembly mode and coming back keeps the build loaded. */
export const assembly = new AssemblyController(() => actions.client());
onCoreEvent((ev) => assembly.onEvent(ev));
// A3's build tabs: switching builds re-opens the assembly on the new build (once it was opened)
onBuildSwitch((id) => { if (assembly.projectId.peek() !== null && assembly.projectId.peek() !== id) void assembly.open(id); });

/** What the gizmo is moving right now (read by the scene callbacks). */
type GizmoTarget = { kind: "free"; id: string } | { kind: "joint"; jointId: string; component: number } | null;

function bboxSize(p: AssemblyPart | null): number {
  const s = p?.metrics?.bbox.size;
  return s ? Math.hypot(s[0], s[1], s[2]) : 100;
}

export function AssemblyView(): JSX.Element {
  const ctl = assembly;
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<AssemblyScene | null>(null);
  const target = useRef<GizmoTarget>(null);
  const templates = useRef(new Map<string, { loader: PartLoader; scene: PartScene | null; mtime: number }>());
  const [templateTick, setTemplateTick] = useState(0);
  const [labels, setLabels] = useState<OverlayLabel[]>([]);
  const [noGl, setNoGl] = useState(false);

  const data = ctl.data.value;
  const draft = ctl.draft.value;

  // the scene: created once per mount
  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let scene: AssemblyScene | null = null;
    const optionFor = (instanceId: string, p: [number, number, number], n: [number, number, number]): FaceOption | null => {
      const inst = ctl.instance(instanceId);
      const part = inst ? ctl.part(inst.part_id) : null;
      if (!inst) return null;
      return pickOption(faceOptions(part?.metrics), part?.metrics, p, n, bboxSize(part));
    };
    try {
      scene = createAssemblyScene(canvas, {
        onSelect: (id) => ctl.select(id),
        onPick: (id, p, n) => { const o = optionFor(id, p, n); if (o) ctl.pickFace(id, o); },
        onHoverPick: (id, p, n) => {
          if (id === null) return null;
          const o = optionFor(id, p, n);
          return o ? { instanceId: id, origin: o.origin, axis: o.axis, role: "hover" } : null;
        },
        onDragStart: () => {
          const t = target.current;
          if (t?.kind === "free") ctl.beginDrag();
          else if (t?.kind === "joint") ctl.beginJointGesture();
        },
        onDrag: (v) => {
          const t = target.current;
          if (t?.kind === "free" && "transform" in v) ctl.dragInstance(t.id, v.transform);
          else if (t?.kind === "joint" && "value" in v) {
            const j = ctl.joint(t.jointId);
            if (j) ctl.setJointValue(j.id, withComponent(j.kind, ctl.values.peek()[j.id] ?? j.value, t.component, v.value));
          }
        },
        onDragEnd: (v) => {
          const t = target.current;
          if (t?.kind === "free" && "transform" in v) void ctl.commitInstance(t.id, v.transform);
          else if (t?.kind === "joint") ctl.endJointGesture();
        },
        onLabels: (next) => setLabels((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next)),
      });
    } catch {
      scene = null; // no WebGL: the panel still works
      setNoGl(true);
    }
    sceneRef.current = scene;
    if (scene && import.meta.env.MODE !== "production") {
      (window as unknown as { __misesAssembly?: ReturnType<AssemblyScene["probe"]> }).__misesAssembly = scene.probe();
    }
    const measure = () => {
      const r = host.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) scene?.resize(r.width, r.height);
    };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(host);
    const offPose = ctl.onPose((pose) => scene?.setPose(pose));
    void ctl.open();
    return () => {
      ro?.disconnect();
      offPose();
      ctl.pause();
      scene?.dispose();
      sceneRef.current = null;
      for (const t of templates.current.values()) t.scene?.dispose();
      templates.current.clear();
    };
  }, []);

  // part geometry: one GLB per part in the assembly, refetched when the part is rebuilt
  const used = [...new Set((data?.instances ?? []).map((i) => i.part_id))];
  const partsKey = used.map((id) => `${id}@${ctl.part(id)?.mtime ?? 0}`).join(",");
  useEffect(() => {
    // parts no longer in the assembly: free their GLBs
    for (const [id, t] of templates.current) {
      if (!used.includes(id)) { t.scene?.dispose(); templates.current.delete(id); }
    }
    try { actions.client(); } catch { return; } // no core bound yet
    for (const id of used) {
      const mtime = ctl.part(id)?.mtime ?? 0;
      let t = templates.current.get(id);
      if (!t) { t = { loader: new PartLoader(() => actions.client()), scene: null, mtime: -1 }; templates.current.set(id, t); }
      if (t.mtime === mtime) continue;
      t.mtime = mtime;
      const entry = t;
      void entry.loader.load(id, mtime).then((r) => {
        if (r.status !== "loaded") return;
        entry.scene?.dispose();
        entry.scene = r.scene;
        setTemplateTick((n) => n + 1);
      });
    }
  }, [partsKey]);

  // instances → scene
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    scene.setReach(reachOf());
    scene.setInstances((data?.instances ?? []).map((i) => ({
      id: i.id, template: templates.current.get(i.part_id)?.scene ?? null, color: ctl.part(i.part_id)?.color ?? null,
    })));
    scene.setPose(ctl.currentPose());
  }, [data, templateTick, ctl.parts.value]);

  // a rebuilt part (the global part list moved on): re-read the build's parts
  useEffect(() => effect(() => {
    void storeParts.value;
    void ctl.reloadParts();
  }), []);

  useEffect(() => { sceneRef.current?.setSelected(ctl.selected.value); }, [ctl.selected.value]);

  // Esc stops Animate and puts the joint back, wherever focus is (not under a modal)
  const animating = ctl.animating.value;
  useEffect(() => {
    if (animating === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || !isTrustedKey(e) || modalOpen()) return;
      e.preventDefault();
      ctl.stopAnimate();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [animating]);
  useEffect(() => { sceneRef.current?.setArriving(ctl.arriving.value); }, [ctl.arriving.value]);

  const it = ctl.interference.value;
  useEffect(() => {
    const byName = (n: string) => (data?.instances ?? []).filter((i) => (i.name || i.id) === n).map((i) => i.id);
    sceneRef.current?.setInterference(it.items.map((x) => ({ a: byName(x.a), b: byName(x.b), text: `${x.a} · ${x.b}  ${fmtVolume(x.volume_mm3)}` })));
  }, [it, data]);

  // picking: on while a joint is being made; markers on the picked faces
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    scene.setPicking(draft !== null);
    const m: PickMarker[] = [];
    if (draft?.a) m.push({ instanceId: draft.a.instanceId, origin: draft.a.option.origin, axis: draft.a.option.axis, role: "a" });
    if (draft?.b) m.push({ instanceId: draft.b.instanceId, origin: draft.b.option.origin, axis: draft.b.option.axis, role: "b" });
    scene.setMarkers(m);
  }, [draft]);

  // the gizmo: on the selected part, free or along its joint
  const sel = ctl.selected.value;
  const gmode = ctl.gizmo.value;
  const snap = ctl.snap.value;
  const values = ctl.values.value;
  const solved = ctl.solved.value;
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const { spec, t } = gizmoFor(sel, gmode, snap);
    target.current = t;
    scene.setGizmo(draft ? null : spec);
  }, [sel, gmode, snap, values, solved, draft, data]);

  /** Where each jointed part can swing: around its joint origin, as far as its farthest corner. */
  function reachOf(): { center: [number, number, number]; radius: number }[] {
    const s = ctl.solved.value;
    if (!data || !s) return [];
    const pose = ctl.currentPose();
    const out: { center: [number, number, number]; radius: number }[] = [];
    for (const inst of data.instances) {
      const p = s.placedBy[inst.id];
      const j = p ? ctl.joint(p.joint) : null;
      const bb = ctl.part(inst.part_id)?.metrics?.bbox;
      const t = pose[inst.id];
      if (!p || !j || !bb || !t) continue;
      const f = jointWorldFrame(data, pose, j, p.from, ctl.resolver());
      if (!f) continue;
      const c: [number, number, number] = [f[0][3], f[1][3], f[2][3]];
      let r = 0;
      for (const x of [bb.min[0], bb.max[0]]) for (const y of [bb.min[1], bb.max[1]]) for (const z of [bb.min[2], bb.max[2]]) {
        const w = transformPoint(t, [x, y, z]);
        r = Math.max(r, Math.hypot(w[0] - c[0], w[1] - c[1], w[2] - c[2]));
      }
      const slide = j.kind === "slider" || j.kind === "cylindrical" ? Math.max(...(j.limits ?? [0, 0]).map(Math.abs)) : 0;
      out.push({ center: c, radius: r + slide });
    }
    return out;
  }

  function gizmoFor(id: string | null, mode: "move" | "turn", snapOn: boolean): { spec: GizmoSpec; t: GizmoTarget } {
    const none = { spec: null, t: null };
    if (!id || !data) return none;
    const m = ctl.mobilityOf(id);
    const pose = ctl.currentPose();
    if (!m || m.kind === "grounded" || m.kind === "locked" || !pose[id]) return none;
    if (m.kind === "free") return { spec: { kind: "free", id, mode, transform: pose[id], snap: snapOn }, t: { kind: "free", id } };
    const j = ctl.joint(m.joint);
    if (!j || j.kind === "fixed" || j.kind === "ball") return none;
    const motion = j.kind === "revolute" ? "turn" : j.kind === "slider" ? "slide" : mode === "turn" ? "turn" : "slide";
    const component = j.kind === "cylindrical" && motion === "slide" ? 1 : 0;
    const frame = jointWorldFrame(data, pose, j, m.from, ctl.resolver());
    if (!frame) return none;
    const value = valueAt(ctl.values.peek()[j.id] ?? j.value, component);
    return {
      spec: { kind: "joint", frame, motion, value, sign: m.from === "a" ? 1 : -1, snap: snapOn },
      t: { kind: "joint", jointId: j.id, component },
    };
  }

  const selInst = ctl.instance(sel);
  const selMob = sel ? ctl.mobilityOf(sel) : null;
  const selJoint = selMob?.kind === "joint" ? ctl.joint(selMob.joint) : null;
  const hint = draft
    ? (draft.a === null ? "Click a face on the first part." : draft.b === null ? "Click a face on the second part." : "Check the preview, then create the joint.")
    : selInst
      ? selMob?.kind === "grounded" ? `${selInst.name} is grounded.`
        : selMob?.kind === "locked" ? `${selInst.name} is locked: its joint names a missing face.`
        : selMob?.kind === "joint" && selJoint ? `Drag ${selInst.name} along its ${KIND_LABEL[selJoint.kind].toLowerCase()}.`
          : `Drag ${selInst.name} to place it.`
      : (data?.instances.length ?? 0) > 0 ? "Click a part to move it." : null;

  return (
    <div class="asm" data-testid="assembly-view">
      <div class="vp asm-sheet" ref={hostRef}>
        <canvas ref={canvasRef} class="vp-canvas" data-testid="assembly-canvas"
          data-instances={data?.instances.length ?? 0} data-picking={draft ? "on" : "off"} data-arriving={ctl.arriving.value ? "on" : "off"}
          aria-label="Assembly view" />
        <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
        <span class="label vp-fig">FIG. 2 — ASSEMBLY</span>
        <ModeSwitch />
        <div class="viewer-tools" role="toolbar" aria-label="Assembly tools">
          <button type="button" class="vt-btn" aria-pressed={gmode === "move"} data-active={gmode === "move"} data-selected={(gmode === "move") ? "" : undefined} onClick={() => { ctl.gizmo.value = "move"; }}>move</button>
          <button type="button" class="vt-btn" aria-pressed={gmode === "turn"} data-active={gmode === "turn"} data-selected={(gmode === "turn") ? "" : undefined} onClick={() => { ctl.gizmo.value = "turn"; }}>turn</button>
          <button type="button" class="vt-btn" aria-pressed={snap} data-active={snap} data-selected={(snap) ? "" : undefined} title="Snap: moves to whole mm, turns to 15°"
            onClick={() => { ctl.snap.value = !snap; }}>snap</button>
          <button type="button" class="vt-btn" onClick={() => sceneRef.current?.fit()}>fit</button>
        </div>
        {labels.map((l) => (
          <span key={l.key} class="asm-tag" style={{ left: `${l.x}px`, top: `${l.y}px` }} data-testid="interference-tag">{l.text}</span>
        ))}
        {ctl.loading.value && !data && <div class="vp-empty label">Loading the assembly…</div>}
        {data && data.instances.length === 0 && <div class="vp-empty label">No parts in this assembly · add one from the panel</div>}
        {noGl && <div class="vp-empty label">3D view unavailable on this Mac · the panel still works</div>}
        {hint && <div class="asm-hint" data-testid="assembly-hint">{hint}</div>}
        {ctl.error.value && (
          <div class="asm-error" role="alert">
            {ctl.error.value}
            <button type="button" class="vt-btn" onClick={() => { ctl.error.value = null; }} aria-label="Dismiss">×</button>
          </div>
        )}
      </div>
      <AssemblyPanel ctl={ctl} />
    </div>
  );
}
