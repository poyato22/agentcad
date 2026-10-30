// "Agent is animating…" chip with a Stop button (R19), shown while an agent drives a motion in
// the viewer. Stop clears the animation and returns the assembly to rest.
import type { JSX } from "preact";
import { agentAnimation, stopAgentAnimation } from "../../state/assemblyAnimation";

export function AssemblyAnimationChip(): JSX.Element | null {
  const active = agentAnimation.value;
  if (!active) return null;
  const what = active.spec.sequence ? active.spec.sequence : active.spec.joint_id ? "a joint" : "the assembly";
  return (
    <div class="cv-animating" role="status" data-animating>
      <span class="cv-animating-dot" aria-hidden="true">▶</span>
      <span class="text">{`Agent is animating ${what}…`}</span>
      <button type="button" class="cv-animating-stop" onClick={() => stopAgentAnimation()}>Stop</button>
    </div>
  );
}
