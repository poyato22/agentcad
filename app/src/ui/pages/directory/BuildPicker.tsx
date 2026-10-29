// "Into build" picker shared by the Directory ("Insert into build") and Onshape ("Import to build")
// pages. Lists the live builds (GET /projects) and starts at the active one (profile
// active_project_id, else the "Default" build, else the first). The choice is remembered across
// both pages for the session.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { signal, type Signal } from "@preact/signals";
import type { BuildRef } from "../../../api/directory";
import { conn, actions } from "../../../state/store";

/** The build the user picked on either page (null: not chosen yet, use the default). */
export const chosenBuild: Signal<string | null> = signal<string | null>(null);

export function defaultBuild(builds: readonly BuildRef[], active: string | null): string | null {
  if (active && builds.some((b) => b.id === active)) return active;
  return builds.find((b) => b.name === "Default")?.id ?? builds[0]?.id ?? null;
}

/** The live builds and the picked one (falls back to the default when the pick is gone). */
export function useBuilds(): { builds: BuildRef[] | null; buildId: string | null; error: string | null } {
  const [state, setState] = useState<{ builds: BuildRef[] | null; active: string | null; error: string | null }>({ builds: null, active: null, error: null });
  const stream = conn.value;
  useEffect(() => {
    let live = true;
    Promise.resolve().then(() => actions.client().buildChoices()).then(
      (r) => { if (live) setState({ builds: r.builds, active: r.active, error: null }); },
      (e: unknown) => { if (live) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) })); },
    );
    return () => { live = false; };
  }, [stream]);
  const builds = state.builds;
  const picked = chosenBuild.value;
  const buildId = builds ? (picked && builds.some((b) => b.id === picked) ? picked : defaultBuild(builds, state.active)) : null;
  return { builds, buildId, error: state.error };
}

export function BuildPicker({ builds, buildId, label = "Build" }: { builds: BuildRef[] | null; buildId: string | null; label?: string }): JSX.Element {
  return (
    <label class="dir-build">
      <span class="dir-build-label">{label}</span>
      <select aria-label={label} value={buildId ?? ""} disabled={!builds || builds.length === 0}
        onChange={(e) => { chosenBuild.value = e.currentTarget.value || null; }}>
        {!builds && <option value="">Loading…</option>}
        {builds && builds.length === 0 && <option value="">No builds</option>}
        {(builds ?? []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
    </label>
  );
}
