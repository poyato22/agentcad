// The generative design result card for the Simulations stream: the run's status and its
// variant grid (render, mass, deflection, peak stress, verified ✓ / ✗), opening the panel for
// the full view and Accept. `TopoptCardSlot` is the hook a tool-call row uses: a topopt_run /
// topopt_generate call renders this card under its line; any other call renders nothing.
import type { JSX } from "preact";
import { useCallback, useEffect } from "preact/hooks";
import { Button } from "../common/Button";
import type { LogItem } from "../console/logModel";
import { topopt } from "./instance";
import { statusLine } from "./model";
import { isTopoptCallName } from "./names";
import type { TopoptController } from "./state";
import { VariantGrid } from "./VariantGrid";
import "./topopt.css";

export { isTopoptCallName };

type Call = Extract<LogItem, { kind: "call" }>;

const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** The topopt_id a finished topopt_run / topopt_generate call returned, else null. */
export function topoptIdOf(call: Call): string | null {
  if (!isTopoptCallName(call.name) || !call.result) return null;
  const p = call.result.payload ?? {};
  const data = rec(rec(p.output).data);
  const id = data.topopt_id ?? rec(p.data).topopt_id;
  return typeof id === "string" ? id : null;
}

export function TopoptRunCard({ id, partId, controller = topopt }: { id: string; partId: string; controller?: TopoptController }): JSX.Element {
  const c = controller;
  useEffect(() => { if (!c.runs.peek().has(id)) void c.refresh(id); }, [c, id]);
  const r = c.view(id);
  const loadRender = useCallback((vid: string) => c.client().topoptRender(id, vid), [c, id]);
  return (
    <div class="gd-tcard" data-testid="topopt-card" data-topopt={id} data-status={r?.status ?? "loading"}>
      <div class="gd-tcard-head">
        <span class="gd-tcard-title">Generative design</span>
        <span class="dim gd-tcard-status">{r ? statusLine(r) : "Loading…"}</span>
        <Button variant="ghost" onClick={() => c.open(partId, id)} aria-label="Open the generative design run">Open</Button>
      </div>
      {r && <VariantGrid rec={r} loadRender={loadRender} compact />}
    </div>
  );
}

export function TopoptCardSlot({ call }: { call: Call }): JSX.Element | null {
  const id = topoptIdOf(call);
  if (!id) return null;
  const partId = typeof call.input.part_id === "string" ? call.input.part_id : "";
  return <div class="scard-slot" data-topopt-slot><TopoptRunCard id={id} partId={partId} /></div>;
}
