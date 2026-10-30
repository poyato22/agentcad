// The layout skeleton's panel on the Design sheet (I4b): what the build's parts are built against —
// its parameters, datums, envelope and curves — instead of a model (a skeleton has none).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { PartSummary } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { actions, steps } from "../../state/store";
import { newerSkeleton, num, parseSkeleton, skeletonFromSteps, vec, type SkeletonView } from "./skeleton";

export function SkeletonPanel({ part }: { part: PartSummary }): JSX.Element {
  const projectId = (part as { project_id?: string | null }).project_id ?? activeBuildId.value;
  const [fetched, setFetched] = useState<SkeletonView | null>(null);
  // the build's skeleton from the core at once (GET /projects/{id}/skeleton), not only once a run's
  // steps are loaded; re-read when the row's version changes (a skeleton_set bumped it)
  useEffect(() => {
    let live = true;
    setFetched(null);
    if (!projectId) return;
    actions.client().projectSkeleton(projectId)
      .then((d) => {
        // skeleton: null is "none saved yet": keep the empty state rather than empty lists
        const saved = typeof d === "object" && d !== null && (d as { skeleton?: unknown }).skeleton != null;
        if (live) setFetched(saved ? parseSkeleton(d, part.part_id) : null);
      })
      .catch(() => { /* an older core: the steps still fill it */ });
    return () => { live = false; };
  }, [projectId, part.part_id, part.version]);
  const sk = newerSkeleton(fetched, skeletonFromSteps(steps.value.values(), part.part_id));
  return (
    <section class="skel" data-testid="skeleton-panel" aria-label="Layout skeleton">
      <header class="skel-head">
        <h2 class="skel-title">Layout skeleton</h2>
        <span class="faint">{`v${sk?.version ?? part.version ?? 1}`}</span>
      </header>
      <p class="skel-note dim">Shared dimensions and datums this build's parts are built against. It has no model of its own.</p>
      {!sk && <p class="skel-note faint" data-skeleton-empty>No skeleton saved yet. An agent sets one when this build's parts share dimensions.</p>}
      {sk && (
        <>
          <h3 class="skel-h label">Parameters</h3>
          {sk.params.length === 0
            ? <p class="skel-note faint">None.</p>
            : <dl class="skel-list" aria-label="Parameters">{sk.params.map(([k, v]) => <div key={k} class="skel-row"><dt>{k}</dt><dd>{num(v)}</dd></div>)}</dl>}
          <h3 class="skel-h label">Datums</h3>
          {sk.datums.length === 0
            ? <p class="skel-note faint">None.</p>
            : (
              <dl class="skel-list" aria-label="Datums">
                {sk.datums.map(([k, d]) => (
                  <div key={k} class="skel-row">
                    <dt>{k}</dt>
                    <dd>{`${d.kind} at ${vec(d.origin)}${d.kind === "plane" ? ` · normal ${vec(d.normal)}` : d.kind === "axis" ? ` · along ${vec(d.direction)}` : ""}`}</dd>
                  </div>
                ))}
              </dl>
            )}
          <h3 class="skel-h label">Envelope</h3>
          <p class="skel-note" data-skeleton-envelope>
            {sk.envelope
              ? `${vec(sk.envelope.min)} to ${vec(sk.envelope.max)} · ${sk.envelope.max.map((x, i) => num(x - sk.envelope!.min[i])).join(" × ")} mm`
              : "None."}
          </p>
          {sk.curves.length > 0 && (
            <>
              <h3 class="skel-h label">Curves</h3>
              <p class="skel-note">{sk.curves.join(", ")}</p>
            </>
          )}
        </>
      )}
    </section>
  );
}
