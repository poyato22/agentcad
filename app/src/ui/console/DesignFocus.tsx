// The build's design focus (Plan 9 G-B, D27): a small segmented chip in the Conversation header,
// "Design focus: Engineering · Mixed · Design". It shows the class the core gave the build (from
// the user's own words) and lets the user set it (PATCH /projects/{id} {design_class}); the
// user's choice always wins over the classifier. Clicking the active, user-set class hands it
// back to the classifier ("auto").
import { Fragment, type JSX } from "preact";
import { signal } from "@preact/signals";
import type { AgentRun } from "../../api/types";
import { actions } from "../../state/store";

export type DesignCls = "engineering" | "mixed" | "design";
const ORDER: DesignCls[] = ["engineering", "mixed", "design"];
const LABEL: Record<DesignCls, string> = { engineering: "Engineering", mixed: "Mixed", design: "Design" };

interface Shown { cls: DesignCls; byUser: boolean }

/** The class a run carries ({cls, source} from the core), leniently read. */
export function runDesignClass(run: AgentRun | null): Shown | null {
  const raw = (run as { design_class?: unknown } | null)?.design_class;
  if (!raw || typeof raw !== "object") return null;
  const cls = (raw as { cls?: unknown }).cls;
  if (cls !== "engineering" && cls !== "mixed" && cls !== "design") return null;
  return { cls, byUser: (raw as { source?: unknown }).source === "user" };
}

/** What the core answered to the user's last pick, per build, with the run payload it was
 *  answered against: shown until that payload changes (the runs refetch), so an "auto" never
 *  flashes the old user-set class back (re-review 2 m7). */
export const chosenFocus = signal<Record<string, { shown: Shown; basis: string } | null>>({});

const key = (s: Shown | null) => (s ? `${s.cls}:${s.byUser ? "user" : "auto"}` : "");

export function DesignFocus({ run, buildId }: { run: AgentRun | null; buildId: string | null }): JSX.Element | null {
  const fromRun = runDesignClass(run);
  const local = buildId ? chosenFocus.value[buildId] : null;
  const shown = local && local.basis === key(fromRun) ? local.shown : fromRun;
  if (!buildId || !shown) return null;
  const pick = async (cls: DesignCls) => {
    const next = shown.byUser && shown.cls === cls ? "auto" : cls;
    try {
      const out = await actions.client().setDesignClass(buildId, next);
      const dc = (out as { design_class?: { cls?: unknown; source?: unknown } | null }).design_class;
      const got: Shown | null = dc && (dc.cls === "engineering" || dc.cls === "mixed" || dc.cls === "design")
        ? { cls: dc.cls, byUser: dc.source === "user" } : null;
      chosenFocus.value = { ...chosenFocus.value, [buildId]: got ? { shown: got, basis: key(fromRun) } : null };
    } catch {
      /* the core refused (offline, unknown build): the chip keeps showing the current class */
    }
  };
  return (
    <span class="dock-focus" role="group" aria-label="Design focus" data-design-focus={shown.cls}
      title={shown.byUser ? "Design focus set by you (click the active one to let Mises decide)" : "Design focus chosen by Mises from your request (click to set it)"}>
      <span class="dock-focus-l dim">Design focus</span>
      {ORDER.map((c, i) => (
        <Fragment key={c}>
          {i > 0 && <span class="dock-focus-sep dim" aria-hidden="true">·</span>}
          <button type="button" class={`dock-focus-b${shown.cls === c ? " on" : ""}`} aria-pressed={shown.cls === c}
            data-focus={c} onClick={() => void pick(c)}>
            {LABEL[c]}
          </button>
        </Fragment>
      ))}
      {shown.byUser && <span class="dock-focus-you dim" data-focus-by-user>you</span>}
    </span>
  );
}
