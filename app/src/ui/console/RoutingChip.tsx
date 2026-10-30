// The routing result as a small chip ("→ engineer · system build"), shown instead of an agent
// picker (R8). The agent was chosen from the prompt by the core's router (U3c); the confidence is
// in the title so the chip itself stays short. Agent and reason are core-provided and render as text.
import type { JSX } from "preact";
import type { Routed } from "../../api/types";

export function RoutingChip({ routed }: { routed: Routed }): JSX.Element {
  const pct = Math.round(Math.min(1, Math.max(0, routed.confidence)) * 100);
  return (
    <span class="cv-routed text" data-routed={routed.agent}
      title={`Chosen from your prompt · confidence ${pct}%`}>
      <span class="cv-routed-arrow" aria-hidden="true">→ </span>
      <span class="cv-routed-agent">{routed.agent}</span>
      {routed.reason ? <span class="cv-routed-why faint">{` · ${routed.reason}`}</span> : null}
    </span>
  );
}
