// G8 (Plan 11 F8): the composer's Think control, always visible as a segmented choice —
// Auto · Low · Medium · High · Max — with the current pick obvious. The pick is the build's
// (core PUT /projects/{id}/effort, so it persists per build); Auto lets the core pick per turn
// (from the profile's default, else the request). Max is the runtime's top effort plus a
// deliberate plan → build → review → revise loop: a note says it costs much more usage. The
// level each turn really ran at shows on that turn (EffortChip).
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { THINK_CHOICES, THINK_LABEL, type ThinkChoice } from "../../../api/think";
import { activeBuildId } from "../../../state/builds";
import { buildThink, loadBuildThink, saveBuildThink, thinkChoice, thinkSupported } from "./think";
import "./think.css";

const HINT: Record<ThinkChoice, string> = {
  auto: "Auto: AgentCAD picks the level for each message",
  low: "Low: quick, for routine edits",
  medium: "Medium: everyday work",
  high: "High: think it through",
  max: "Max: the deepest thinking, plus plan, independent review and revision",
};

export function ThinkControl(): JSX.Element | null {
  const build = activeBuildId.value;
  useEffect(() => { if (build && !buildThink.peek().has(build)) void loadBuildThink(build); }, [build]);
  if (!thinkSupported.value) return null;
  const shown: ThinkChoice = thinkChoice.value;
  const choose = (c: ThinkChoice) => {
    thinkChoice.value = c;
    if (build) void saveBuildThink(build, c).catch(() => { /* kept for this session */ });
  };
  return (
    <span class="think" data-testid="think-control" data-think={shown}>
      <span class="think-seg" role="radiogroup" aria-label="How hard the agent thinks">
        <span class="think-k faint" aria-hidden="true">Think</span>
        {THINK_CHOICES.map((c) => (
          <button key={c} type="button" role="radio" aria-checked={shown === c} class={`think-opt${shown === c ? " on" : ""}${c === "max" ? " is-max" : ""}`}
            data-testid={`think-${c}`} title={HINT[c]} onClick={() => choose(c)}>{THINK_LABEL[c]}</button>
        ))}
      </span>
      {shown === "max" && <span class="think-note faint" data-testid="think-max-note">Max uses much more of your plan's usage.</span>}
    </span>
  );
}
