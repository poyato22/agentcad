// "Agent is animating…" chip with a Stop button (R19), shown while an agent's motion plays in
// the viewer. When the build's assembly isn't on screen it says so and offers Watch (switch to
// Assembly and play there) instead of claiming a motion nobody sees.
import type { JSX } from "preact";
import { agentAnimation, stopAgentAnimation, watchAgentAnimation } from "../../state/assemblyAnimation";

export function AssemblyAnimationChip(): JSX.Element | null {
  const active = agentAnimation.value;
  if (!active) return null;
  const n = active.spec.steps.length;
  const what = n > 1 ? `${n} joints in sequence` : "a joint";
  const loops = active.spec.loops > 1 ? ` · ${active.spec.loops}×` : "";
  return (
    <div class="cv-animating" role="status" data-animating data-playing={active.playing ? "" : undefined}>
      <span class="cv-animating-dot" aria-hidden="true">▶</span>
      {active.playing
        ? <span class="text">{`Agent is animating ${what}${loops}…`}</span>
        : <span class="text">{`The agent wants to show you ${what} moving${loops}.`}</span>}
      {!active.playing && <button type="button" class="cv-animating-stop" onClick={() => { void watchAgentAnimation(); }}>Watch</button>}
      <button type="button" class="cv-animating-stop" onClick={() => stopAgentAnimation()}>{active.playing ? "Stop" : "Dismiss"}</button>
    </div>
  );
}
