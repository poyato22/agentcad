// The variant grid of a generative design run: one card per variant in the core's rank order,
// with its render, mass, CalculiX deflection / peak stress / safety factor, verified ✓ / ✗ and
// the Pareto / recommended marks, and Accept. Numbers render in the mono face (tabular).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { TopoptRecord, TopoptVariant } from "../../api/topopt";
import { Button } from "../common/Button";
import { phaseLine, ranked, variantRows, verdict } from "./model";

export type RenderLoader = (variant: string) => Promise<Blob>;

function Render({ variant, load }: { variant: TopoptVariant; load: RenderLoader }): JSX.Element {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (variant.status !== "done") return;
    let live = true;
    let made: string | null = null;
    void load(variant.id).then((b) => {
      if (!live || typeof URL.createObjectURL !== "function") return;
      made = URL.createObjectURL(b);
      setUrl(made);
    }).catch(() => undefined);
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [variant.id, variant.status, load]);
  return url
    ? <img class="gd-render" src={url} alt={`Render of ${variant.name}`} />
    : <div class="gd-render gd-render-empty faint" aria-hidden="true">{variant.status === "done" ? "" : `${Math.round(variant.pct)}%`}</div>;
}

/** Accept; a variant that is not a passing, verified one asks once more, saying why. */
function AcceptButton({ v, best, busy, onAccept }: { v: TopoptVariant; best: boolean; busy: boolean; onAccept: (id: string) => void }): JSX.Element {
  const [confirm, setConfirm] = useState(false);
  const risky = !(v.verified && v.passes);
  const why = !v.verified ? "It was not verified in CalculiX." : "It fails the safety factor or deflection target.";
  if (confirm) {
    return (
      <>
        <p class="gd-confirm" role="alert">{why} Accept it anyway?</p>
        <Button variant="danger" disabled={busy} onClick={() => { setConfirm(false); onAccept(v.id); }}
          aria-label={`Accept ${v.name} anyway`}>Accept anyway</Button>
      </>
    );
  }
  return (
    <Button variant={best ? "primary" : "default"} disabled={busy} onClick={() => (risky ? setConfirm(true) : onAccept(v.id))}
      aria-label={`Accept ${v.name} as a new part`}>Accept</Button>
  );
}

export function VariantGrid({ rec, loadRender, onAccept, busy, compact = false }: {
  rec: TopoptRecord; loadRender: RenderLoader; onAccept?: (variant: string) => void; busy?: boolean; compact?: boolean;
}): JSX.Element {
  const accepted = new Set(rec.accepted.map((a) => a.variant));
  return (
    <ul class={`gd-grid${compact ? " gd-grid-compact" : ""}`} data-testid="gd-variants">
      {ranked(rec).map((v) => {
        const badge = verdict(v);
        const best = v.id === rec.recommended;
        return (
          <li key={v.id} class="gd-card" data-variant={v.id} data-recommended={best ? "" : undefined} data-verdict={badge.kind}>
            <Render variant={v} load={loadRender} />
            <div class="gd-card-head">
              <span class="gd-card-name">{v.name}</span>
              {best && <span class="gd-tag gd-tag-best">Recommended</span>}
              {v.pareto && !best && <span class="gd-tag">Pareto</span>}
            </div>
            <span class={`gd-verdict gd-${badge.kind}`} data-testid="gd-verdict">{badge.text}</span>
            {v.status === "done"
              ? (
                <dl class="gd-rows">
                  {variantRows(v).map((r) => (
                    <div key={r.label} class="gd-row"><dt class="dim">{r.label}</dt><dd class="num">{r.value}</dd></div>
                  ))}
                </dl>
              )
              : <span class="faint gd-phase">{v.status === "failed" ? (v.error ?? "Failed") : phaseLine(v)}</span>}
            {!compact && v.warnings[0] && <p class="gd-warn faint">{v.warnings[0]}</p>}
            {!compact && onAccept && v.status === "done" && (
              accepted.has(v.id)
                ? <span class="gd-accepted dim">Added as {rec.accepted.find((a) => a.variant === v.id)?.part_id}</span>
                : <AcceptButton v={v} best={best} busy={busy ?? false} onAccept={onAccept} />
            )}
          </li>
        );
      })}
    </ul>
  );
}
