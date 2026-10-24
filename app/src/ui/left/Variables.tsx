import type { JSX } from "preact";
import { fmtMm } from "../../lib/format";
import { runLabel } from "../../lib/names";
import { activeChanges, activePart, activeRun, allRuns } from "./derived";

export function Variables(): JSX.Element {
  const part = activePart.value;
  const changes = activeChanges.value;
  const run = activeRun.value;
  if (!part) return <div class="pane-empty">no part selected</div>;
  const names = Object.keys(part.vars);
  for (const n of changes.keys()) if (!names.includes(n)) names.push(n);
  if (names.length === 0) return <div class="pane-empty">no variables</div>;
  const who = run ? runLabel(run, allRuns.value) : "an agent";
  return (
    <dl class="vars">
      {names.map((name) => {
        const c = changes.get(name);
        const value = c ? c.now : part.vars[name];
        const title = c ? `was ${c.prev === null ? "unknown" : c.prev.toFixed(2)} · set by ${who}` : undefined;
        return (
          <div class="var-row" key={name}>
            <dt class="var-name">{name}</dt>
            <dd class={c ? "var-value changed" : "var-value"} data-testid={`var-${name}`} data-changed={c ? "true" : undefined} title={title}>
              {fmtMm(value)}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
