// The small amber chip next to a run's model when shape-heavy work runs on a model that can't see
// its renders or is a small local one (G-R6). Hover or focus reads the why.
import type { JSX } from "preact";
import type { AgentRun } from "../../../../api/types";
import { Led } from "../../../common/Led";
import { shapeWarning } from "./shapeWarning";
import "./critique.css";

export function ModelWarning({ run }: { run: Pick<AgentRun, "provider" | "model" | "shape_complexity"> }): JSX.Element | null {
  const w = shapeWarning(run);
  if (!w) return null;
  return (
    <span class="mwarn" role="note" tabIndex={0} title={w.detail} aria-label={`Warning: ${w.detail}`} data-testid="model-warning" data-concern={w.concern}>
      <Led tone="amber" />
      <span class="mwarn-text">{w.text}</span>
    </span>
  );
}
