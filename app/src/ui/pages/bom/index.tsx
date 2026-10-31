// Parts list (BOM) page, per build (R5; core bom/__init__.py). One clear table of made and
// bought parts: name and key specs, vendor, an editable quantity, unit cost, currency and notes,
// a line total; then the totals, one line per currency (amounts in different currencies are
// never added together), with the made/bought split, the per-vendor subtotals and how many
// parts have no price yet. Made parts come in from the build automatically and their cost is a
// live estimate (mass × material $/kg), tagged "estimate" with the basis on hover. You can add a
// bought part by hand, edit any row, remove a row and export CSV (the core neutralizes
// spreadsheet formulas). The list refreshes when anything changes it (`bom_changed`).
//
// Every value a row carries (name, vendor, specs, notes, URL) may have come from an agent or a
// web page, so it is rendered as plain escaped text, and a link shows its domain.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Bom, BomPatch, BomRow, NewBomRow } from "../../../api/browser";
import { onCoreEvent } from "../../../api/events";
import { currencyLabel, money } from "../../../lib/money";
import { activeBuild } from "../../../state/builds";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { ExternalLink } from "../../common/ExternalLink";
import { linkHost } from "../../markup/parse";
import { describeError } from "../../profile/common";
import { saveBlob } from "../drawing/save";
import { PageShell } from "../PageShell";
import {
  currencies, grouped, keyCurrency, lineTotalText, parseCurrency, perCurrency, specsLine, unpricedText, validQty, vendorLabel,
} from "./model";
import "./bom.css";

export function BomPage(): JSX.Element {
  const build = activeBuild.value;
  const [bom, setBom] = useState<Bom | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const seq = useRef(0);

  const reload = (id: string) => {
    const mine = ++seq.current;
    actions.client().bom(id).then(
      (b) => { if (mine === seq.current) { setBom(b); setError(null); } },
      (e: unknown) => { if (mine === seq.current) setError(describeError(e)); },
    );
  };

  useEffect(() => {
    if (!build) { setBom(null); return; }
    setBom(null);
    reload(build.id);
    // an agent's bom_add, an edit in another window, a part built or removed: re-read
    return onCoreEvent((ev) => { if (ev.type === "bom_changed" && ev.data.project_id === build.id) reload(build.id); });
  }, [build?.id]);

  // BOM is a tab of the build (Plan 9 D9): the tabs lead back, the page has no "← Design" (FW-app M8)
  if (!build) {
    return (
      <PageShell title="Parts list">
        <p class="bom-empty dim">Open a build to see its parts list. Made parts appear here automatically; add the bought ones as the design settles.</p>
      </PageShell>
    );
  }

  const act = async (fn: () => Promise<unknown>): Promise<boolean> => {
    try { await fn(); setError(null); reload(build.id); return true; } catch (e) { setError(describeError(e)); return false; }
  };
  const patch = (id: string, p: BomPatch) => { void act(() => actions.client().bomPatch(build.id, id, p)); };
  const remove = (id: string) => { void act(() => actions.client().bomDelete(build.id, id)); };
  const add = async (row: NewBomRow) => { const ok = await act(() => actions.client().bomAdd(build.id, row)); if (ok) setAdding(false); return ok; };
  const exportCsv = async () => {
    try { saveBlob(await actions.client().bomCsv(build.id), `${build.name || "build"} parts list.csv`); } catch (e) { setError(describeError(e)); }
  };

  const g = bom ? grouped(bom.items) : null;

  return (
    <PageShell title="Parts list">
      <div class="bom" data-bom-page data-build={build.id}>
        <div class="bom-bar">
          <div class="bom-buildname text">{build.name || "Untitled build"}</div>
          <span class="bom-spacer" />
          <Button variant="default" onClick={() => setAdding(true)}>Add bought part</Button>
          <Button variant="ghost" disabled={!bom || bom.items.length === 0} onClick={() => void exportCsv()}>Export CSV</Button>
        </div>

        {error && <p class="bom-error cv-fail text" role="status">{error}</p>}
        {adding && <AddRow onAdd={add} onCancel={() => setAdding(false)} />}
        {!bom && !error && <p class="bom-empty dim">Loading…</p>}

        {bom && bom.items.length === 0 && !adding && (
          <p class="bom-empty dim">No parts yet. Parts you make in this build show up here by themselves; add the parts you buy with the button above.</p>
        )}

        {g && g.made.length > 0 && <Section title="Made in-house" rows={g.made} onPatch={patch} onRemove={remove} />}
        {g && g.bought.length > 0 && <Section title="Bought" rows={g.bought} onPatch={patch} onRemove={remove} />}

        {bom && bom.items.length > 0 && <Totals bom={bom} />}
      </div>
    </PageShell>
  );
}

function Section({ title, rows, onPatch, onRemove }: {
  title: string; rows: BomRow[]; onPatch: (id: string, p: BomPatch) => void; onRemove: (id: string) => void;
}): JSX.Element {
  return (
    <section class="bom-section" aria-label={title}>
      <h2 class="bom-section-title label">{title}</h2>
      <table class="bom-table">
        <thead>
          <tr>
            <th scope="col" class="bom-c-name">Part</th>
            <th scope="col" class="bom-c-vendor">Vendor</th>
            <th scope="col" class="bom-c-qty">Qty</th>
            <th scope="col" class="bom-c-cost">Unit cost</th>
            <th scope="col" class="bom-c-cur">Currency</th>
            <th scope="col" class="bom-c-total">Total</th>
            <th scope="col" class="bom-c-notes">Notes</th>
            <th scope="col" class="bom-c-rm"><span class="vh">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => <Row key={r.id} row={r} onPatch={onPatch} onRemove={onRemove} />)}
        </tbody>
      </table>
    </section>
  );
}

function Row({ row, onPatch, onRemove }: { row: BomRow; onPatch: (id: string, p: BomPatch) => void; onRemove: (id: string) => void }): JSX.Element {
  const specs = specsLine(row);
  const name = row.name || "Unnamed";
  const host = row.url ? linkHost(row.url) : "";
  return (
    <tr data-bom-line={row.id} data-kind={row.kind} data-source={row.source}>
      <td class="bom-c-name">
        <div class="bom-name text">
          {row.url && host
            ? <><ExternalLink class="bom-name-link" href={row.url}>{name}</ExternalLink><span class="bom-host faint text">{` · ${host}`}</span></>
            : name}
        </div>
        {specs && <div class="bom-specs faint text">{specs}</div>}
      </td>
      <td class="bom-c-vendor text">{row.vendor ?? (row.kind === "made" ? "In-house" : "—")}</td>
      <td class="bom-c-qty">
        <Field kind="qty" label={`Quantity of ${name}`} value={String(row.qty)}
          onCommit={(t) => { const q = validQty(t); if (q === null) return false; if (q !== row.qty) onPatch(row.id, { qty: q }); return true; }} />
      </td>
      <td class="bom-c-cost">
        <Field kind="money" label={`Unit cost of ${name}`} value={row.estimate || row.unit_cost === null ? "" : String(row.unit_cost)}
          placeholder={row.estimate && row.unit_cost !== null ? String(row.unit_cost) : "—"}
          onCommit={(t) => {
            const s = t.trim();
            if (s === "") { if (!row.estimate && row.unit_cost !== null) onPatch(row.id, { unit_cost: null }); return true; }
            const n = Number(s);
            if (!Number.isFinite(n) || n < 0) return false;
            if (row.estimate || n !== row.unit_cost) onPatch(row.id, { unit_cost: n });
            return true;
          }} />
        {row.estimate && <span class="bom-est" title={row.estimate_note ?? "Estimated"}>estimate</span>}
        {!row.estimate && row.unit_cost === null && row.estimate_note && <div class="bom-note faint text">{row.estimate_note}</div>}
      </td>
      <td class="bom-c-cur">
        <Field kind="currency" label={`Currency of ${name}`} value={row.currency ?? ""} placeholder="unknown"
          onCommit={(t) => {
            const c = parseCurrency(t);
            // the core keeps a set currency on PATCH {currency: null}, so clearing it is refused here
            if (c === undefined || (c === null && row.currency !== null)) return false;
            if (c !== row.currency) onPatch(row.id, { currency: c });
            return true;
          }} />
      </td>
      <td class="bom-c-total text">{lineTotalText(row)}</td>
      <td class="bom-c-notes">
        <Field kind="text" label={`Notes for ${name}`} value={row.notes} onCommit={(t) => { if (t !== row.notes) onPatch(row.id, { notes: t }); return true; }} />
      </td>
      <td class="bom-c-rm">
        <button type="button" class="bom-rm" aria-label={`Remove ${name}`} onClick={() => onRemove(row.id)}>×</button>
      </td>
    </tr>
  );
}

function Totals({ bom }: { bom: Bom }): JSX.Element {
  const t = bom.totals;
  const unpriced = unpricedText(bom);
  return (
    <div class="bom-totals" data-bom-totals>
      <table class="bom-grand">
        <caption class="label">Total</caption>
        <tbody>
          {currencies(t.by_currency).map((k) => (
            <tr key={k} data-currency={k}>
              <th scope="row" class="faint">{currencyLabel(keyCurrency(k))}</th>
              <td class="bom-grand-amt text">{money(t.by_currency[k], keyCurrency(k))}</td>
              {t.estimated[k] !== undefined && <td class="bom-grand-est faint">{`incl. ${money(t.estimated[k], keyCurrency(k))} estimated`}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {currencies(t.by_currency).length > 1 && <p class="bom-cur-note faint">Amounts in different currencies are listed separately, never added together.</p>}
      <dl class="bom-split">
        <div><dt class="faint">Made</dt><dd class="text">{perCurrency(t.made)}</dd></div>
        <div><dt class="faint">Bought</dt><dd class="text">{perCurrency(t.bought)}</dd></div>
      </dl>
      {t.by_vendor.length > 0 && (
        <table class="bom-vendors">
          <caption class="label">By vendor</caption>
          <tbody>
            {t.by_vendor.map((v) => (
              <tr key={`${v.vendor ?? v.kind}:${v.currency}`}>
                <td class="text">{vendorLabel(v)}</td>
                <td class="faint">{v.count === 1 ? "1 part" : `${v.count} parts`}</td>
                <td class="bom-vendors-amt text">{money(v.total, keyCurrency(v.currency))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {unpriced && <p class="bom-unpriced faint">{unpriced}</p>}
      {t.has_estimates && <p class="bom-est-note faint">Totals include estimated costs (tagged "estimate"); confirm them before ordering.</p>}
    </div>
  );
}

/** An inline field that commits on blur or Enter. Uncontrolled (the DOM holds the draft) so the
 *  async reload never drops keystrokes; `onCommit` returns false to reject the value (it snaps
 *  back). Enter + blur never commit twice. */
function Field({ kind, label, value, placeholder, onCommit }: {
  kind: "qty" | "money" | "currency" | "text"; label: string; value: string; placeholder?: string; onCommit: (t: string) => boolean;
}): JSX.Element {
  const ref = useRef<HTMLInputElement>(null);
  const last = useRef(value);
  useEffect(() => {
    last.current = value;
    if (ref.current && document.activeElement !== ref.current) ref.current.value = value;
  }, [value]);
  const commit = () => {
    const el = ref.current;
    if (!el || el.value === last.current) return;
    if (onCommit(el.value)) last.current = el.value;
    else el.value = last.current;
  };
  const props = kind === "qty" ? { type: "number", min: 1, step: 1, inputMode: "numeric" as const, class: "bom-num" }
    : kind === "money" ? { type: "number", min: 0, step: 0.01, inputMode: "decimal" as const, class: "bom-num" }
    : kind === "currency" ? { type: "text", maxLength: 3, class: "bom-cur", autocapitalize: "characters" as const }
    : { type: "text", class: "bom-notes-in" };
  return (
    <input ref={ref} {...props} aria-label={label} placeholder={placeholder} defaultValue={value} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />
  );
}

function AddRow({ onAdd, onCancel }: { onAdd: (r: NewBomRow) => Promise<boolean>; onCancel: () => void }): JSX.Element {
  const [name, setName] = useState("");
  const [vendor, setVendor] = useState("");
  const [qty, setQty] = useState("1");
  const [cost, setCost] = useState("");
  const [cur, setCur] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => { first.current?.focus(); }, []);

  const submit = async (e: Event) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    const q = validQty(qty);
    if (q === null) { setErr("Quantity must be a whole number, 1 or more."); return; }
    const c = parseCurrency(cur);
    if (c === undefined) { setErr("Currency is a 3-letter code like USD or EUR (or leave it empty)."); return; }
    const price = cost.trim() === "" ? null : Number(cost);
    if (price !== null && (!Number.isFinite(price) || price < 0)) { setErr("Unit cost must be a number, 0 or more."); return; }
    setErr(null);
    setBusy(true);
    const ok = await onAdd({ kind: "bought", name: name.trim(), vendor: vendor.trim() || null, qty: q, unit_cost: price, currency: c });
    if (!ok) setBusy(false);
  };

  return (
    <form class="bom-add" onSubmit={(e) => void submit(e)} aria-label="Add a bought part">
      <input ref={first} class="bom-add-name" type="text" aria-label="Part name" placeholder="Part name" value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} required />
      <input class="bom-add-vendor" type="text" aria-label="Vendor" placeholder="Vendor" value={vendor} onInput={(e) => setVendor((e.target as HTMLInputElement).value)} />
      <input class="bom-num" type="number" aria-label="Quantity" min={1} step={1} value={qty} onInput={(e) => setQty((e.target as HTMLInputElement).value)} />
      <input class="bom-num" type="number" aria-label="Unit cost" min={0} step={0.01} placeholder="Unit cost" value={cost} onInput={(e) => setCost((e.target as HTMLInputElement).value)} />
      <input class="bom-cur" type="text" aria-label="Currency" maxLength={3} placeholder="USD" value={cur} onInput={(e) => setCur((e.target as HTMLInputElement).value)} />
      <Button type="submit" variant="default" disabled={busy || !name.trim()}>{busy ? "Adding…" : "Add"}</Button>
      <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      {err && <p class="bom-add-err cv-fail text" role="status">{err}</p>}
    </form>
  );
}
