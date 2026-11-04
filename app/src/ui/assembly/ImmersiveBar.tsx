// The assembly full window's chrome (Plan 9 D11 / D12 / D18): a quiet title at the top (what it
// is, the agent's caption) and one floating bar at the bottom for what this view does:
//   animation  ▶/❚❚ · time · scrub · total · Loop · 0.5× 1× 2× · Close
//   sweep      the plane sweeps once by itself, then: axis · slider · mm · Sweep again · Close
//   tour       Close (the exploded orbit runs by itself)
// Everything moves the build view's own scene: the controller's poses (animation) or the view
// modes' section (sweep), so a presented card plays on exactly the assembly the user builds.
import type { JSX } from "preact";
import type * as THREE from "three";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ModeHost } from "../viewer/scene";
import { restBox } from "../viewer/modes/controller";
import { prefersReducedMotion, tween } from "../viewer/modes/motion";
import type { AssemblyController } from "./controller";
import { sectionAt, sectionRange, unit } from "../viewer/modes/section";
import { XRAY_OPACITY, type ModesState } from "../viewer/modes/state";
import { assemblyModes, type Immersive } from "./state";
import { AXIS_NORMAL, SWEEP_REST_AT, sweepEnds, sweepSection } from "./sweep";
import { AXES, axisOf, towardCamera } from "./ViewerPill";
import { modalOpen } from "../pages/router";

const SPEEDS = [0.5, 1, 2] as const;
const fmtS = (s: number) => `${s.toFixed(1)}`;
const fmtMm = (v: number) => `${v.toFixed(1)} mm`;

export function ImmersiveBar({ imm, ctl, host, onClose }: { imm: Immersive; ctl: AssemblyController; host: ModeHost | null; onClose(): void }): JSX.Element {
  const title = imm.mode === "tour" ? "Exploded tour" : imm.title;
  const caption = imm.mode === "tour" ? "" : imm.caption;
  return (
    <div class="asm-imm" data-testid="assembly-immersive" data-mode={imm.mode} role="dialog" aria-label="Assembly, full window">
      <div class="asm-imm-head">
        <span class="asm-imm-title">{title}</span>
        {caption && <span class="asm-imm-caption" data-agent-text>{caption}</span>}
      </div>
      {imm.mode === "animation" ? <MotionBar ctl={ctl} host={host} onClose={onClose} />
        : imm.mode === "sweep" ? <SweepBar key={JSON.stringify(imm.sweep)} imm={imm} host={host} onClose={onClose} />
          : <div class="asm-imm-bar"><CloseButton onClose={onClose} /></div>}
    </div>
  );
}

function CloseButton({ onClose }: { onClose(): void }): JSX.Element {
  return <button type="button" class="pill-btn" data-testid="assembly-immersive-close" onClick={onClose} title="Close (Esc)">Close</button>;
}

/** ←/→ anywhere in the full window (not while typing, not on a focused slider: its own keys),
 *  ⇧ for bigger steps: `nudge(±1, big)`. */
function useArrows(nudge: (dir: 1 | -1, big: boolean) => void): void {
  const ref = useRef(nudge);
  ref.current = nudge;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key !== "ArrowLeft" && e.key !== "ArrowRight") || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || modalOpen()) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      e.preventDefault();
      ref.current(e.key === "ArrowRight" ? 1 : -1, e.shiftKey);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

/** The assembly's rest box, re-checked until the parts have loaded: the bar can mount (a study's
 *  Watch) before the scene has any parts, and nothing else re-renders it then, so Section stayed
 *  disabled for the whole playback. */
export function useRestBox(host: ModeHost | null): THREE.Box3 | null {
  const [, setTick] = useState(0);
  const box = host ? restBox(host.parts()) : null;
  const missing = host !== null && box === null;
  useEffect(() => {
    if (!missing) return;
    const t = setInterval(() => setTick((n) => n + 1), 250);
    return () => clearInterval(t);
  }, [missing, host]);
  return box;
}

/** G5: Section (axis, flip, the plane's slider) and X-ray while a motion plays: the cut is a
 *  world plane, so the moving parts pass through it and their caps follow them every frame. */
function MotionModes({ host }: { host: ModeHost | null }): JSX.Element {
  const modes = assemblyModes.value;
  const set = (patch: Partial<ModesState>) => { assemblyModes.value = { ...assemblyModes.peek(), ...patch }; };
  const sec = modes.section;
  const xrayOn = modes.xray.opacity < 0.999;
  const box = useRestBox(host);
  const range = box ? sectionRange(box, unit(sec.normal)) : [0, 0];
  const at = sectionAt(sec, box);
  return (
    <span class="imm-modes" data-testid="motion-modes">
      <button type="button" class="pill-btn" aria-pressed={sec.on} data-testid="motion-section" disabled={!box} title="Cut the assembly with a plane"
        onClick={() => set({ section: sec.on ? { ...sec, on: false } : { on: true, normal: towardCamera(host, sec.normal), at: null } })}>
        <span aria-hidden="true">◧</span> Section
      </button>
      {sec.on && (
        <span class="pill-section" data-testid="motion-section-controls">
          <span class="pill-axes" role="group" aria-label="Section plane normal">
            {AXES.map((a) => (
              <button key={a.key} type="button" class="pill-axis" aria-pressed={axisOf(sec.normal) === a.key}
                onClick={() => set({ section: { on: true, normal: towardCamera(host, a.normal), at: null } })}>{a.key}</button>
            ))}
            <button type="button" class="pill-axis" title="Look at the cut from the other side" aria-label="Flip the section"
              onClick={() => set({ section: { on: true, normal: [-sec.normal[0], -sec.normal[1], -sec.normal[2]], at: -at } })}>⇄</button>
          </span>
          <input type="range" class="pill-range" min={range[0]} max={range[1]} step={Math.max((range[1] - range[0]) / 200, 0.01)} value={at}
            aria-label="Section plane position" data-testid="motion-section-slider"
            onInput={(e) => set({ section: { ...sec, at: Number(e.currentTarget.value) } })} />
          <span class="pill-val num" data-testid="motion-section-value">{`${at.toFixed(1)} mm`}</span>
        </span>
      )}
      <button type="button" class="pill-btn" aria-pressed={xrayOn} data-testid="motion-xray" title="See through the parts to what is inside"
        onClick={() => set({ xray: { ...modes.xray, opacity: xrayOn ? 1 : XRAY_OPACITY } })}>X-ray</button>
    </span>
  );
}

function MotionBar({ ctl, host, onClose }: { ctl: AssemblyController; host: ModeHost | null; onClose(): void }): JSX.Element {
  // a twentieth of the motion a press (a quarter with ⇧), paused there
  useArrows((dir, big) => {
    const cur = ctl.motion.peek();
    if (cur) ctl.seekMotion(cur.t + dir * cur.duration * (big ? 0.25 : 0.05));
  });
  const m = ctl.motion.value;
  if (!m) {
    return (
      <div class="asm-imm-bar" data-testid="motion-bar" data-playing="false">
        <span class="asm-imm-note">This motion's joints aren't in the assembly any more.</span>
        <CloseButton onClose={onClose} />
      </div>
    );
  }
  const step = Math.max(m.duration / 400, 0.01);
  return (
    <div class="asm-imm-bar" data-testid="motion-bar" data-playing={m.playing ? "true" : "false"}>
      <button type="button" class="pill-btn imm-play" data-testid="motion-play" aria-label={m.playing ? "Pause" : "Play"} title={m.playing ? "Pause (Space)" : "Play (Space)"}
        onClick={() => ctl.setMotion({ playing: !m.playing })}>
        <span aria-hidden="true">{m.playing ? "❚❚" : "▶"}</span>
      </button>
      <span class="imm-time num" data-testid="motion-time">{fmtS(m.t)}</span>
      <input type="range" class="imm-scrub" min={0} max={m.duration} step={step} value={m.t} aria-label="Scrub the motion" aria-valuetext={`${fmtS(m.t)} of ${fmtS(m.duration)} s`}
        data-testid="motion-scrub" onInput={(e) => ctl.seekMotion(Number(e.currentTarget.value))} />
      <span class="imm-time num dim">{`${fmtS(m.duration)} s`}</span>
      <span class="pill-sep" aria-hidden="true" />
      <button type="button" class="pill-btn" aria-pressed={m.loop} data-testid="motion-loop" title="Play it over and over" onClick={() => ctl.setMotion({ loop: !m.loop })}>Loop</button>
      <span class="pill-axes" role="group" aria-label="Speed">
        {SPEEDS.map((s) => (
          <button key={s} type="button" class="pill-axis imm-speed" aria-pressed={m.speed === s} data-testid={`motion-speed-${s}`}
            onClick={() => ctl.setMotion({ speed: s })}>{`${s}×`}</button>
        ))}
      </span>
      <span class="pill-sep" aria-hidden="true" />
      <MotionModes host={host} />
      <span class="pill-sep" aria-hidden="true" />
      <CloseButton onClose={onClose} />
    </div>
  );
}

function SweepBar({ imm, host, onClose }: { imm: Extract<Immersive, { mode: "sweep" }>; host: ModeHost | null; onClose(): void }): JSX.Element {
  const sweep = imm.sweep;
  const box = useRestBox(host);
  const ends = sweepEnds(sweep, box);
  const [phase, setPhase] = useState<"auto" | "manual">("auto");
  const [value, setValue] = useState<number | null>(null);
  const [round, setRound] = useState(0);
  const toward = useRef<[number, number, number] | null>(null);
  const cancel = useRef<(() => void) | null>(null);
  const ready = ends !== null;

  const show = (v: number) => {
    if (!toward.current) toward.current = towardCamera(host, AXIS_NORMAL[sweep.axis]);
    setValue(v);
    assemblyModes.value = { ...assemblyModes.peek(), section: sweepSection(sweep, toward.current, v) };
  };

  // the one sweep (again on "Sweep again"): from → to by itself, then the slider is the user's;
  // under reduced motion the cut just lands a little before the middle
  useEffect(() => {
    if (!ready || !ends) return;
    const [from, to] = ends;
    if (prefersReducedMotion()) { show(from + SWEEP_REST_AT * (to - from)); setPhase("manual"); return; }
    setPhase("auto");
    show(from);
    cancel.current = tween(from, to, sweep.duration_s * 1000, (v) => {
      show(v);
      if (v === to) { cancel.current = null; setPhase("manual"); }
    });
    return () => { cancel.current?.(); cancel.current = null; };
  }, [ready, round]);

  const take = (v: number) => { cancel.current?.(); cancel.current = null; setPhase("manual"); show(v); };
  const lo = ends ? Math.min(ends[0], ends[1]) : 0;
  const hi = ends ? Math.max(ends[0], ends[1]) : 0;
  // ←/→ nudge the cut a fiftieth of the range (a tenth with ⇧): the user's from then on
  useArrows((dir, big) => {
    if (!ready) return;
    const cur = value ?? lo;
    take(Math.min(hi, Math.max(lo, cur + dir * (hi - lo) * (big ? 0.1 : 0.02))));
  });
  return (
    <div class="asm-imm-bar" data-testid="sweep-bar" data-sweep={ready ? phase : "waiting"}>
      <span class="pill-axis imm-axis" aria-label={`Cut across ${sweep.axis.toUpperCase()}`}>{sweep.axis.toUpperCase()}</span>
      <input type="range" class="imm-scrub" min={lo} max={hi} step={Math.max((hi - lo) / 400, 0.01)} value={value ?? lo} disabled={!ready}
        aria-label="Section plane position" data-testid="sweep-slider" onInput={(e) => take(Number(e.currentTarget.value))} />
      <span class="imm-time imm-mm num" data-testid="sweep-value">{value === null ? "—" : fmtMm(value)}</span>
      <span class="pill-sep" aria-hidden="true" />
      <button type="button" class="pill-btn" data-testid="sweep-again" disabled={!ready || phase === "auto"} onClick={() => setRound((r) => r + 1)}>Sweep again</button>
      <span class="pill-sep" aria-hidden="true" />
      <CloseButton onClose={onClose} />
    </div>
  );
}
