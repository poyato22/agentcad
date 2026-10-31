// The build view's floating pill (Plan 9 D12 / D17), bottom-centre over the assembly:
//   ▶ Play     full-window view of the assembly (the agent's latest animation, else an exploded tour)
//   ◧ Section  one click cuts the assembly; the slider sweeps the plane through its box, X / Y / Z
//              pick the plane, flip looks from the other side (the drag handle on the model too)
//   X-ray · Explode
// and, only when the agents' check found something, a small status chip (interference, a joint that
// can't apply). The modes are the part viewer's (ui/viewer/modes) on the instances' poses.
import type { JSX } from "preact";
import { useRef } from "preact/hooks";
import * as THREE from "three";
import type { ModeHost } from "../viewer/scene";
import { restBox, type ModesController } from "../viewer/modes/controller";
import { sectionAt, sectionRange, unit } from "../viewer/modes/section";
import { SectionHandle } from "../viewer/modes/SectionHandle";
import { MODES_OFF, XRAY_OPACITY, type ModesState, type Vec3 } from "../viewer/modes/state";
import { fmtVolume } from "./format";

const AXES: { key: string; normal: Vec3 }[] = [{ key: "X", normal: [1, 0, 0] }, { key: "Y", normal: [0, 1, 0] }, { key: "Z", normal: [0, 0, 1] }];

function axisOf(n: Vec3): string | null {
  const u = unit(n);
  for (const a of AXES) if (Math.abs(Math.abs(u.dot(new THREE.Vector3(...a.normal))) - 1) < 1e-6) return a.key;
  return null;
}

/** The plane's normal, signed so the kept side faces the camera: you look straight at the cut
 *  (review I1). The clip keeps n·p <= at, and the cap is seen from +n. */
export function towardCamera(host: ModeHost | null, normal: Vec3): Vec3 {
  if (!host) return normal;
  const look = host.camera.position.clone().sub(host.controls.target);
  return unit(normal).dot(look) >= 0 ? normal : [-normal[0], -normal[1], -normal[2]];
}

export interface CheckIssue { tone: "fail" | "warn"; text: string }

/** What the check chip says: clashes first (fail), then joints that can't apply (warn). */
export function checkIssues(items: readonly { a: string; b: string; volume_mm3: number }[], overconstrained: readonly (readonly string[])[]): CheckIssue[] {
  return [
    ...items.map((x) => ({ tone: "fail" as const, text: `${x.a} and ${x.b} overlap by ${fmtVolume(x.volume_mm3)}` })),
    ...overconstrained.map((g) => ({ tone: "warn" as const, text: `${g.join(" and ")} are grounded and joined: one joint can't apply` })),
  ];
}

export interface ViewerPillProps {
  host: ModeHost | null;
  /** The build view's modes controller (it outlives the pill: Play's view keeps the explode). */
  modesCtl: ModesController | null;
  modes: ModesState;
  onModes(next: ModesState): void;
  /** Set true before a change that should glide (the explode). */
  animate: { current: boolean };
  issues: readonly CheckIssue[];
  onPlay(): void;
  canPlay: boolean;
}

export function ViewerPill({ host, modesCtl: ctl, modes, onModes, animate, issues, onPlay, canPlay }: ViewerPillProps): JSX.Element {
  const set = (patch: Partial<ModesState>, glide = false) => { animate.current = glide; onModes({ ...modes, ...patch }); };
  const sec = modes.section;
  const xrayOn = modes.xray.opacity < 0.999;
  const exploding = modes.explode > 0;
  const canExplode = (ctl?.exploder.count ?? 0) >= 2;
  const box = host ? restBox(host.parts()) : null;
  const n = unit(sec.normal);
  const range = box ? sectionRange(box, n) : [0, 0];
  const at = sectionAt(sec, box);
  const step = Math.max((range[1] - range[0]) / 200, 0.01);
  const fails = issues.filter((i) => i.tone === "fail").length;
  const chipRef = useRef<HTMLSpanElement>(null);

  return (
    <>
      {sec.on && host && <SectionHandle host={host} section={sec} onAt={(v) => set({ section: { ...sec, at: v } })} />}
      <div class="asm-pill" role="toolbar" aria-label="View" data-testid="viewer-pill">
        <button type="button" class="pill-btn pill-play" data-testid="pill-play" disabled={!canPlay}
          title="Watch the assembly full-window: the agent's latest animation, else an exploded tour" onClick={onPlay}>
          <span aria-hidden="true">▶</span> Play
        </button>
        <span class="pill-sep" aria-hidden="true" />
        <button type="button" class="pill-btn" aria-pressed={sec.on} data-testid="pill-section" disabled={!box}
          title="Cut the assembly with a plane" onClick={() => set({ section: sec.on ? { ...sec, on: false } : { on: true, normal: towardCamera(host, sec.normal), at: null } })}>
          <span aria-hidden="true">◧</span> Section
        </button>
        {sec.on && (
          <span class="pill-section" data-testid="pill-section-controls">
            <span class="pill-axes" role="group" aria-label="Section plane normal">
              {AXES.map((a) => (
                <button key={a.key} type="button" class="pill-axis" aria-pressed={axisOf(sec.normal) === a.key}
                  onClick={() => set({ section: { on: true, normal: towardCamera(host, a.normal), at: null } })}>{a.key}</button>
              ))}
              <button type="button" class="pill-axis" title="Look at the cut from the other side" aria-label="Flip the section"
                onClick={() => set({ section: { on: true, normal: [-sec.normal[0], -sec.normal[1], -sec.normal[2]], at: -at } })}>⇄</button>
            </span>
            <input type="range" class="pill-range" min={range[0]} max={range[1]} step={step} value={at}
              aria-label="Section plane position" data-testid="section-slider"
              onInput={(e) => set({ section: { ...sec, at: Number(e.currentTarget.value) } })} />
            <span class="pill-val num" data-testid="section-value">{`${at.toFixed(1)} mm`}</span>
          </span>
        )}
        <button type="button" class="pill-btn" aria-pressed={xrayOn} data-testid="pill-xray" title="See through the parts to what is inside"
          onClick={() => set({ xray: { ...modes.xray, opacity: xrayOn ? 1 : XRAY_OPACITY } })}>X-ray</button>
        <button type="button" class="pill-btn" aria-pressed={exploding} data-testid="pill-explode" disabled={!canExplode && !exploding}
          title={canExplode || exploding ? "Move the parts apart" : "Explode needs two or more parts"}
          onClick={() => set({ explode: exploding ? 0 : 1 }, true)}>Explode</button>
        {(xrayOn || sec.on || exploding) && (
          <button type="button" class="pill-btn pill-clear" title="Turn every view mode off" aria-label="Clear the view modes"
            onClick={() => { animate.current = modes.explode > 0; onModes(MODES_OFF); }}>✕</button>
        )}
        {issues.length > 0 && (
          <span ref={chipRef} class="pill-chip" data-testid="assembly-check-chip" data-status={fails ? "fail" : "warn"} tabIndex={0}
            title={issues.map((i) => i.text).join("\n")} aria-label={`Check: ${issues.map((i) => i.text).join("; ")}`}>
            <span class={`led ${fails ? "led-red" : "led-amber"}`} aria-hidden="true" />
            {fails ? `${fails} clash${fails === 1 ? "" : "es"}` : `${issues.length} warning${issues.length === 1 ? "" : "s"}`}
          </span>
        )}
      </div>
    </>
  );
}
