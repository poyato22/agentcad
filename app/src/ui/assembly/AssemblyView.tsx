// The build view (Plan 9: the Design page IS the assembly): every part of the build's assembly
// posed by its joints, full-bleed, with interference tags and the floating viewer pill (Play,
// Section, X-ray, Explode, the check chip). Placement, joints and checks are agent-driven; a click
// on a part selects it (its row in the left tree too); a double-click on empty space or F fits.
// Play opens the assembly full-window: the agent's latest animation, else an exploded orbit tour.
import { materialOf } from "../../lib/appearance";
import { effect, useComputed } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AssemblyPart } from "../../api/assembly";
import { onCoreEvent } from "../../api/events";
import { isTrustedKey } from "../../lib/keys";
import { actions, parts as storeParts } from "../../state/store";
import type { PartScene } from "../viewer/loadPart";
import { PartLoader } from "../viewer/partLoader";
import type { ModeHost } from "../viewer/scene";
import { Orbiter } from "../viewer/modes/camera";
import { MODES_OFF } from "../viewer/modes/state";
import { useModesController } from "../viewer/modes/ViewModes";
import "../viewer/viewer.css";
import { AssemblyController } from "./controller";
import { jointWorldFrame, transformPoint } from "./kinematics";
import { activePage, modalOpen, setPage } from "../pages/router";
import { closePart, openPartId } from "./focus";
import { fmtVolume, KIND_LABEL } from "./format";
import { createAssemblyScene, type AssemblyScene, type OverlayLabel } from "./scene";
import { ImmersiveBar } from "./ImmersiveBar";
import { assemblyModes, immersive, latestAnimation, setPresenter } from "./state";
import { checkIssues, ViewerPill } from "./ViewerPill";
import { prefersReducedMotion } from "../viewer/modes/motion";
import type { ModesState } from "../viewer/modes/state";
import { activeBuildId, onBuildSwitch } from "../../state/builds";
import { assemblyOf } from "../left/treeActions";
import { parseAnimateSteps, setAnimationPlayer } from "../../state/assemblyAnimation";
import type { AnimateStep } from "../../api/types";
import { registerAnnounceJump } from "../console/announceJumps";
import "./assembly.css";

/** The full window frames its model at about 70% of the limiting side. */
const IMMERSIVE_MARGIN = 1.4;

/** The build's newest saved animation (core G-D route), parsed as untrusted data. */
async function savedAnimation(pid: string): Promise<{ name: string; steps: AnimateStep[]; loops: number } | null> {
  try {
    const all = await actions.client().assemblyAnimations(pid);
    for (const a of [...all].reverse()) {
      const steps = parseAnimateSteps(a.steps);
      if (steps.length) return { name: a.name.slice(0, 60) || "Animation", steps, loops: Math.min(10, Math.max(1, Math.floor(a.loops))) };
    }
  } catch { /* an older core: no saved animations */ }
  return null;
}

/** One per app: opening a part and coming back keeps the build loaded. */
export const assembly = new AssemblyController(() => actions.client());
onCoreEvent((ev) => assembly.onEvent(ev));
// Plan 7 R19: an agent's `assembly_animate` plays through the controller (its steps, loops) when
// that build's assembly is on screen; Watch brings it on screen first; Stop puts the joints back.
const onScreen = (pid: string | null) =>
  activePage.peek() === "design" && openPartId.peek() === null && assembly.data.peek() !== null
  // a motion the user is scrubbing full window is never hijacked: the chip offers Watch instead
  && immersive.peek()?.mode !== "animation"
  && (pid === null || assembly.projectId.peek() === pid);
setAnimationPlayer({
  play: (spec, onEnd) => onScreen(spec.project_id) && assembly.play(spec.steps, { loops: spec.loops, onEnd }),
  watch: async (spec, onEnd) => {
    setPage("design");
    closePart();
    immersive.value = null;           // the full window closes first (its own cleanup restores)
    if (assembly.data.peek() === null || (spec.project_id && assembly.projectId.peek() !== spec.project_id)) await assembly.open(spec.project_id);
    return assembly.play(spec.steps, { loops: spec.loops, onEnd });
  },
  stop: () => assembly.stopAnimate(),
});
// Plan 9 G-D: an agent's presented animation / section sweep card opens the build view full
// window (the assembly, never a part on its own) and plays there on the instances' poses
setPresenter((what) => {
  setPage("design");
  closePart();
  immersive.value = what;
});
// Plan 7 I5: an "Added to the assembly" card shows that instance in the build view
registerAnnounceJump("assembly_added", (a) => (a.ref.instance_id ? {
  label: "Show in assembly",
  go: () => {
    setPage("design");
    closePart();
    const id = a.ref.instance_id as string;
    const pick = () => assembly.select(id);
    if (assembly.data.peek() === null || (a.projectId && assembly.projectId.peek() !== a.projectId)) void assembly.open(a.projectId).then(pick);
    else pick();
  },
} : null));
// switching builds re-opens the assembly on the new build (once it was opened)
onBuildSwitch((id) => {
  if (assembly.projectId.peek() !== null && assembly.projectId.peek() !== id) {
    assemblyModes.value = MODES_OFF;
    void assembly.open(id);
  }
});

// the parts tree's delete confirm counts this build's instances (D29)
effect(() => {
  const d = assembly.data.value;
  assemblyOf.value = d && assembly.projectId.value === activeBuildId.value ? d : null;
});

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

export function AssemblyView(): JSX.Element {
  const ctl = assembly;
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<AssemblyScene | null>(null);
  const templates = useRef(new Map<string, { loader: PartLoader; scene: PartScene | null; mtime: number }>());
  const [templateTick, setTemplateTick] = useState(0);
  const [labels, setLabels] = useState<OverlayLabel[]>([]);
  const [noGl, setNoGl] = useState(false);
  const [modeHost, setModeHost] = useState<ModeHost | null>(null);
  const animate = useRef(false);
  const modesCtl = useModesController(modeHost, assemblyModes.value, animate);

  const data = ctl.data.value;

  // the scene: created once per mount
  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let scene: AssemblyScene | null = null;
    try {
      scene = createAssemblyScene(canvas, {
        onSelect: (id) => {
          ctl.select(id);
          // the part's row in the left tree follows (it does not open the part)
          const inst = ctl.instance(id);
          if (inst) actions.selectPart(inst.part_id);
        },
        onLabels: (next) => setLabels((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next)),
      });
    } catch {
      scene = null; // no WebGL
      setNoGl(true);
    }
    sceneRef.current = scene;
    setModeHost(scene?.modeHost() ?? null);
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
    return () => {
      ro?.disconnect();
      offPose();
      ctl.pause();
      setModeHost(null);
      scene?.dispose();
      sceneRef.current = null;
      for (const t of templates.current.values()) t.scene?.dispose();
      templates.current.clear();
    };
  }, []);

  // the active build's assembly (a build switch re-opens it, onBuildSwitch above)
  const build = activeBuildId.value;
  useEffect(() => {
    if (build && (ctl.projectId.peek() !== build || ctl.data.peek() === null)) void ctl.open(build);
  }, [build]);

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
      material: materialOf(ctl.part(i.part_id) ?? {}),
    })));
    scene.setPose(ctl.currentPose());
  }, [data, templateTick, ctl.parts.value]);

  // a rebuilt part (the global part list moved on): re-read the build's parts
  // (only on a real change after the assembly loaded: the first list is the one open() read)
  useEffect(() => {
    let seen: string | null = null;
    return effect(() => {
      const key = storeParts.value.map((p) => `${p.part_id}@${p.mtime}@${p.version ?? 0}@${p.color ?? ""}`).join(",");
      const loaded = ctl.data.value !== null;
      if (!loaded) return;
      if (seen && seen !== key) void ctl.reloadParts();     // an empty list was just not loaded yet
      seen = key;
    });
  }, []);

  useEffect(() => { sceneRef.current?.setSelected(ctl.selected.value); }, [ctl.selected.value]);
  useEffect(() => { sceneRef.current?.setArriving(ctl.arriving.value); }, [ctl.arriving.value]);

  const it = ctl.interference.value;
  // exploded, the clash blocks would hang where the parts were: they show assembled only
  const apart = assemblyModes.value.explode > 0;
  useEffect(() => {
    const byName = (n: string) => (data?.instances ?? []).filter((i) => (i.name || i.id) === n).map((i) => i.id);
    sceneRef.current?.setInterference(apart ? [] : it.items.map((x) => ({ a: byName(x.a), b: byName(x.b), text: `${x.a} · ${x.b}  ${fmtVolume(x.volume_mm3)}` })));
  }, [it, data, apart]);

  // ---- Play: the full-window view
  const imm = immersive.value;
  const orbiter = useRef<Orbiter | null>(null);
  /** The modes before the full window opened (they come back on close). */
  const beforeImm = useRef<ModesState | null>(null);
  function play(): void {
    const pid = ctl.projectId.peek();
    const spec = latestAnimation(pid);
    const usable = (steps: readonly { joint_id: string }[]) => steps.some((s) => ctl.joint(s.joint_id) !== null);
    if (spec && usable(spec.steps)) {
      immersive.value = { mode: "animation", steps: spec.steps, loop: spec.loops > 1, title: "The agent's animation", caption: "" };
      return;
    }
    // after a reload no live animation is known: the build's newest saved one, else the tour
    immersive.value = { mode: "tour" };
    if (!pid) return;
    void savedAnimation(pid).then((saved) => {
      if (!saved || !usable(saved.steps) || immersive.peek()?.mode !== "tour" || ctl.projectId.peek() !== pid) return;
      immersive.value = { mode: "animation", steps: saved.steps, loop: saved.loops > 1, title: saved.name, caption: "" };
    });
  }
  // the full window opens (Play, or an agent's presented card): what it shows starts here
  const loaded = data !== null;
  // kept as the view opens, before any child's effect (the sweep's first cut) changes the modes
  if (imm && beforeImm.current === null) beforeImm.current = assemblyModes.peek();
  useEffect(() => {
    if (!imm) {
      // closed (Close, Esc, Watch, leaving the page): the motion stops, the modes come back
      if (beforeImm.current === null) return;
      orbiter.current?.stop();
      ctl.closeMotion();
      animate.current = true;
      assemblyModes.value = beforeImm.current;
      beforeImm.current = null;
      sceneRef.current?.setFraming(null);
      return;
    }
    if (!beforeImm.current) return;
    if (imm.mode === "tour") {
      animate.current = true;
      assemblyModes.value = { ...beforeImm.current, explode: 1 };
    } else if (imm.mode === "animation") {
      assemblyModes.value = { ...beforeImm.current, explode: 0, section: MODES_OFF.section };
      if (loaded) ctl.openMotion(imm.steps, { loop: imm.loop, autoplay: !prefersReducedMotion() });
    } else {
      // the sweep bar drives the section (its first cut is already on); nothing exploded
      assemblyModes.value = { ...assemblyModes.peek(), explode: 0 };
    }
  }, [imm, loaded]);
  function closeImmersive(): void { immersive.value = null; }
  const tour = imm?.mode === "tour";
  useEffect(() => {
    if (!tour || !modeHost) return;
    const o = new Orbiter(modeHost);
    orbiter.current = o;
    o.start(1, 14);
    return () => { o.stop(); };
  }, [tour, modeHost]);
  // the canvas grew to the window (or back): frame what the view shows, the model ~70% of it —
  // a motion's envelope (not every joint's full reach), the posed parts for a sweep or the tour
  // (again once the tour's explode has glided out); resizes keep that framing
  const motionOpen = useComputed(() => ctl.motion.value !== null).value;   // not every frame
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    if (!imm) { requestAnimationFrame(() => sceneRef.current?.fit()); return; }
    const frame = () => {
      const poses = imm.mode === "animation" ? ctl.motionPoses() : [];
      sceneRef.current?.setFraming(poses.length ? { kind: "poses", poses } : { kind: "posed" }, IMMERSIVE_MARGIN);
    };
    const id = requestAnimationFrame(frame);
    const later = imm.mode === "tour" ? setTimeout(frame, 600) : null;
    return () => { cancelAnimationFrame(id); if (later) clearTimeout(later); };
  }, [imm, motionOpen, templateTick]);
  useEffect(() => () => {
    // leaving the page with it open: nothing is left moved, cut or exploded for the next visit
    if (immersive.peek()) immersive.value = null;
    ctl.closeMotion();
    if (beforeImm.current) assemblyModes.value = beforeImm.current;
    beforeImm.current = null;
  }, []);

  // keys: Esc closes Play's view, else stops an animation; F fits (never while typing or under a modal)
  const animating = ctl.animating.value;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!isTrustedKey(e) || modalOpen() || e.defaultPrevented) return;
      if (e.key === "Escape" && immersive.peek() !== null) { e.preventDefault(); closeImmersive(); return; }
      // Space plays / pauses a full-window motion (not on a focused button: that one's own click)
      const m = ctl.motion.peek();
      if (e.key === " " && m && immersive.peek()?.mode === "animation" && !typing(e.target) && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        ctl.setMotion({ playing: !m.playing });
        return;
      }
      if (e.key === "Escape" && ctl.animating.peek() !== null) { e.preventDefault(); ctl.stopAnimate(); return; }
      if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey && !typing(e.target)) {
        e.preventDefault();
        sceneRef.current?.fit();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [animating]);

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

  const sel = ctl.instance(ctl.selected.value);
  const selMob = sel ? ctl.mobilityOf(sel.id) : null;
  const selJoint = selMob?.kind === "joint" ? ctl.joint(selMob.joint) : null;
  const selPart: AssemblyPart | null = sel ? ctl.part(sel.part_id) : null;
  const selWhat = !sel ? null
    : selMob?.kind === "grounded" ? "grounded"
      : selMob?.kind === "locked" ? "locked: its joint names a missing face"
        : selJoint ? `on a ${KIND_LABEL[selJoint.kind].toLowerCase()} to ${ctl.instance(selMob?.kind === "joint" ? (selJoint.a.instance_id === sel.id ? selJoint.b.instance_id : selJoint.a.instance_id) : null)?.name ?? "a part"}`
          : "placed freely";
  const issues = checkIssues(it.status === "done" ? it.items : [], data?.overconstrained ?? []);
  const count = data?.instances.length ?? 0;

  return (
    <div class="asm" data-testid="assembly-view" data-immersive={imm?.mode ?? undefined}>
      <div class="vp asm-sheet" ref={hostRef}>
        <canvas ref={canvasRef} class="vp-canvas" data-testid="assembly-canvas"
          data-instances={count} data-arriving={ctl.arriving.value ? "on" : "off"}
          data-section={assemblyModes.value.section.on ? "on" : "off"} data-explode={assemblyModes.value.explode > 0 ? "on" : "off"}
          aria-label="Assembly view" />
        {labels.map((l) => (
          <span key={l.key} class="asm-tag" style={{ left: `${l.x}px`, top: `${l.y}px` }} data-testid="interference-tag">{l.text}</span>
        ))}
        {ctl.loading.value && !data && <div class="vp-empty asm-empty">Loading the assembly…</div>}
        {data && count === 0 && <div class="vp-empty asm-empty">Nothing in this assembly yet. Parts an agent builds land here by themselves.</div>}
        {noGl && <div class="vp-empty asm-empty">3D view unavailable on this Mac.</div>}
        {sel && !imm && (
          <div class="asm-caption" data-testid="assembly-selection" role="status">
            <b>{sel.name}</b>{selPart?.label && selPart.label !== sel.name ? <span class="dim"> · {selPart.label}</span> : null}
            <span class="dim"> · {selWhat}</span>
          </div>
        )}
        {ctl.error.value && (
          <div class="asm-error" role="alert">
            {ctl.error.value}
            <button type="button" class="pill-btn" onClick={() => { ctl.error.value = null; }} aria-label="Dismiss">✕</button>
          </div>
        )}
        {imm ? (
          <ImmersiveBar imm={imm} ctl={ctl} host={modeHost} onClose={closeImmersive} />
        ) : (
          <ViewerPill host={modeHost} modesCtl={modesCtl} modes={assemblyModes.value} onModes={(m) => { assemblyModes.value = m; }} animate={animate}
            issues={issues} onPlay={play} canPlay={count > 0} />
        )}
      </div>
    </div>
  );
}
