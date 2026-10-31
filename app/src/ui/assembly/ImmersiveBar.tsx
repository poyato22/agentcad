// The assembly full window's chrome (Plan 9 D11 / D12 / D18): a quiet title at the top (what it
// is, the agent's caption) and one floating bar at the bottom for what this view does:
//   animation  ▶/❚❚ · time · scrub · total · Loop · 0.5× 1× 2× · Close
//   sweep      the plane sweeps once by itself, then: axis · slider · mm · Sweep again · Close
//   tour       Close (the exploded orbit runs by itself)
// Everything moves the build view's own scene: the controller's poses (animation) or the view
// modes' section (sweep), so a presented card plays on exactly the assembly the user builds.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ModeHost } from "../viewer/scene";
import { restBox } from "../viewer/modes/controller";
import { prefersReducedMotion, tween } from "../viewer/modes/motion";
import type { AssemblyController } from "./controller";
import { assemblyModes, type Immersive } from "./state";
import { AXIS_NORMAL, SWEEP_REST_AT, sweepEnds, sweepSection } from "./sweep";
import { towardCamera } from "./ViewerPill";

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
      {imm.mode === "animation" ? <MotionBar ctl={ctl} onClose={onClose} />
        : imm.mode === "sweep" ? <SweepBar key={JSON.stringify(imm.sweep)} imm={imm} host={host} onClose={onClose} />
          : <div class="asm-imm-bar"><CloseButton onClose={onClose} /></div>}
    </div>
  );
}

function CloseButton({ onClose }: { onClose(): void }): JSX.Element {
  return <button type="button" class="pill-btn" data-testid="assembly-immersive-close" onClick={onClose} title="Close (Esc)">Close</button>;
}

function MotionBar({ ctl, onClose }: { ctl: AssemblyController; onClose(): void }): JSX.Element {
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
      <CloseButton onClose={onClose} />
    </div>
  );
}

function SweepBar({ imm, host, onClose }: { imm: Extract<Immersive, { mode: "sweep" }>; host: ModeHost | null; onClose(): void }): JSX.Element {
  const sweep = imm.sweep;
  const box = host ? restBox(host.parts()) : null;
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
