// An agent's presented animation or section sweep (Plan 9 G-D, D11 / D18) in the View stream: the
// core's still of the assembly, big, with a play mark, the agent's caption and what it shows
// (the motion's name and length, or the sweep's axis and range). Click (or Enter) opens the build
// view full window, where the motion plays on the assembly's own poses (scrub, loop, speed) or
// the cut sweeps through it once and then follows the user's slider.
import type { JSX } from "preact";
import { presentOnAssembly } from "../../../assembly/state";
import { VIEW_KIND_TEXT } from "../../../viewer/modes/viewSpec";
import type { PresentedView } from "./announce";
import { useKeyedImageUrl } from "./images";
import "./critique.css";

const mm = (v: number) => `${Math.round(v * 10) / 10}`;

/** What the card says under its caption. */
export function motionMeta(view: PresentedView): string {
  const s = view.spec;
  if (s.animation) {
    const a = s.animation;
    const steps = a.steps.length === 1 ? "1 move" : `${a.steps.length} moves`;
    return [a.name, `${steps} · ${a.duration_s.toFixed(1)} s`, a.loops > 1 ? `plays ${a.loops}×` : null].filter(Boolean).join(" · ");
  }
  if (s.sweep) {
    const w = s.sweep;
    const range = w.from !== null && w.to !== null ? ` from ${mm(w.from)} to ${mm(w.to)} mm` : " through the assembly";
    return `Cuts across ${w.axis.toUpperCase()}${range}`;
  }
  return "";
}

/** Open it on the build view. */
export function openMotionView(view: PresentedView): void {
  const s = view.spec;
  const caption = s.caption;
  if (s.kind === "animation" && s.animation) {
    presentOnAssembly({ mode: "animation", steps: s.animation.steps, loop: s.animation.loops > 1, title: s.animation.name ?? "Animation", caption });
  } else if (s.kind === "section_sweep" && s.sweep) {
    presentOnAssembly({ mode: "sweep", sweep: s.sweep, title: "Section sweep", caption });
  }
}

export function MotionCard({ view }: { view: PresentedView }): JSX.Element {
  const spec = view.spec;
  const thumb = useKeyedImageUrl(view.thumbnail);
  const kind = VIEW_KIND_TEXT[spec.kind];
  const anim = spec.kind === "animation";
  const verb = anim ? "Play the animation" : "Sweep the section";
  return (
    <article class="vcard mcard" data-testid="motion-card" data-kind={spec.kind} aria-label={`${kind}`}>
      <div class="vcard-stage">
        <button type="button" class="vcard-open mcard-open" onClick={() => openMotionView(view)} aria-label={`${verb} full window`} data-testid="motion-card-open">
          {thumb
            ? <img class="vcard-still" src={thumb} alt="" data-testid="motion-card-still" />
            : <span class="vcard-blank faint">{kind}</span>}
          <span class="mcard-mark" aria-hidden="true">{anim ? "▶" : "◧"}</span>
        </button>
        <span class="vcard-kind label" aria-hidden="true">{kind}</span>
      </div>
      <div class="vcard-foot">
        {spec.caption && <p class="vcard-caption text" data-agent-text>{spec.caption}</p>}
        <p class="vcard-meta faint"><span class="text" data-testid="motion-card-meta">{motionMeta(view)}</span></p>
      </div>
    </article>
  );
}
