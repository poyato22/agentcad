// Plan 10 E14: the left surface's "Bought parts" group — the build's bought BOM rows (a servo, a
// bearing, screws: what an agent picked, and catalog parts inserted into the build), each with its
// quantity and price. A click opens its info (specs, price, where it comes from) and, when a part
// in the build draws it (core `geometry_part_id`), "Show the 3D model" opens that part alone.
// Names, vendors and specs may come from web pages: shown as plain text.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { BomRow } from "../../api/browser";
import { openPart } from "../assembly/focus";
import { ExternalLink } from "../common/ExternalLink";
import { buildBom } from "./bomData";

const money = (v: number, cur: string | null) => {
  const n = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
  return cur === "USD" ? `$${n}` : cur ? `${n} ${cur}` : `${n} (currency unknown)`;
};
const hostOf = (url: string) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };

export function BoughtParts(): JSX.Element | null {
  const bom = buildBom.value;
  const rows = (bom?.items ?? []).filter((r) => r.kind === "bought");
  const [open, setOpen] = useState<string | null>(null);
  const [folded, setFolded] = useState(false);
  if (!rows.length) return null;
  return (
    <section class="left-bought" aria-label="Bought parts" data-testid="bought-parts">
      <header class="left-sec-head">
        <button type="button" class="left-fold" aria-expanded={!folded} onClick={() => setFolded(!folded)}>
          <span class="left-sec-title">Bought parts</span>
          <span class="dim num">{rows.reduce((n, r) => n + r.qty, 0)}</span>
        </button>
      </header>
      {!folded && (
        <ul class="bought-list">
          {rows.map((r) => (
            <li key={r.id} class="bought-item" data-bought={r.id}>
              <button type="button" class="bought-row" aria-expanded={open === r.id} data-testid="bought-row"
                onClick={() => setOpen(open === r.id ? null : r.id)}>
                <span class="row-name">{r.name}</span>
                {r.qty > 1 && <span class="dim num">×{r.qty}</span>}
                {r.line_total !== null && <span class="num bought-cost">{money(r.line_total, r.currency)}</span>}
              </button>
              {open === r.id && <BoughtInfo row={r} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function BoughtInfo({ row }: { row: BomRow }): JSX.Element {
  const model = row.geometry_part_id ?? null;
  return (
    <div class="bought-info" data-testid="bought-info" role="group" aria-label={`${row.name}: details`}>
      <dl class="bought-facts">
        <div><dt>Price</dt><dd class="num">{row.unit_cost !== null ? `${money(row.unit_cost, row.currency)} each` : "unknown"}</dd></div>
        <div><dt>Quantity</dt><dd class="num">{row.qty}</dd></div>
        {row.vendor && <div><dt>Vendor</dt><dd>{row.vendor}</dd></div>}
        {row.specs.slice(0, 8).map((s) => (
          <div key={s.key}><dt>{s.key}</dt><dd>{typeof s.value === "number" ? <span class="num">{s.value}</span> : s.value}{s.unit ? ` ${s.unit}` : ""}</dd></div>
        ))}
      </dl>
      {row.notes && <p class="dim bought-notes">{row.notes}</p>}
      <div class="bought-actions">
        {model && (
          <button type="button" class="left-link" data-testid="bought-model" onClick={() => openPart(model)}
            title={row.geometry === "envelope" ? "An envelope model: its outer size, not its detail" : "Open its 3D model"}>
            Show the 3D model{row.geometry === "envelope" ? " (envelope)" : ""}
          </button>
        )}
        {!model && <span class="dim">No 3D model in this build</span>}
        {row.url && <ExternalLink href={row.url} class="left-link">{hostOf(row.url)}</ExternalLink>}
      </div>
    </div>
  );
}
