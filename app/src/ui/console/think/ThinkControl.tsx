// Plan 11 F8: the composer's Think control, a small menu beside the field. Auto lets the core
// pick the effort per turn (open-ended challenges and design builds think hard, routine edits
// light); Normal · Hard · Max override it for the next message. "Every message in this build"
// makes the pick the build's default. Self-contained: the composer only renders <ThinkControl />
// and chat.ts sends takeThink() with the message.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { THINK_CHOICES, THINK_LABEL, type ThinkChoice } from "../../../api/think";
import { activeBuildId } from "../../../state/builds";
import { OverflowMenu, type OverflowItem } from "../../common/OverflowMenu";
import { buildThink, loadBuildThink, saveBuildThink, thinkChoice, thinkSupported } from "./think";
import "./think.css";

const HINT: Record<ThinkChoice, string> = {
  auto: "Auto · Mises picks per message",
  normal: "Normal · everyday edits",
  hard: "Hard · think it through",
  max: "Max · the deepest thinking (slower)",
};

export function ThinkControl(): JSX.Element | null {
  const build = activeBuildId.value;
  useEffect(() => { if (build && !buildThink.peek().has(build)) void loadBuildThink(build); }, [build]);
  if (!thinkSupported.value) return null;
  const pick = thinkChoice.value;
  const saved = build ? buildThink.value.get(build) ?? "auto" : "auto";
  const shown: ThinkChoice = pick !== "auto" ? pick : saved;
  const sticky = pick === "auto" && saved !== "auto";
  const items: OverflowItem[] = [
    ...THINK_CHOICES.map((c): OverflowItem => ({ label: HINT[c], checked: shown === c, onSelect: () => { thinkChoice.value = c; } })),
    {
      label: "Every message in this build", checked: saved !== "auto", divider: true, disabled: !build,
      onSelect: () => {
        if (!build) return;
        // on: save the shown pick as the default (Hard when it is Auto); off: back to Auto
        const next: ThinkChoice = saved !== "auto" ? "auto" : shown !== "auto" ? shown : "hard";
        thinkChoice.value = "auto";
        void saveBuildThink(build, next).catch(() => { /* kept as it was */ });
      },
    },
  ];
  const label = `Think: ${THINK_LABEL[shown]}${sticky ? " (this build)" : ""}`;
  return (
    <span class={`think${shown !== "auto" ? " is-set" : ""}`} data-testid="think-control" data-think={shown} data-think-sticky={sticky ? "" : undefined}>
      <OverflowMenu label={label} title={`${label} · how hard the agent thinks`} class="ovm-float think-menu" btnClass="think-btn" items={items}
        trigger={<span class="think-t"><span class="think-k faint">Think</span>{` ${THINK_LABEL[shown]}`}</span>} />
    </span>
  );
}
