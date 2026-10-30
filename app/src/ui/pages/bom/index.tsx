// Parts list (BOM) page, per build (R5). One clear table of made and bought parts: name and key
// specs, vendor, an editable quantity, unit cost and notes, a line total, and totals by vendor
// with the grand total. Made parts come in from the build automatically and their cost is an
// estimate (mass × material $/kg), marked "estimate". You can add a bought part by hand, edit any
// cost or quantity, remove a line, and export the list as CSV.
//
// Every value a part carries (name, vendor, specs, notes) may have come from an agent or a web
// page, so it is rendered as plain escaped text.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Bom, BomLine, BomPatch, NewBomLine } from "../../../api/browser";
import { activeBuild } from "../../../state/builds";
import { actions } from "../../../state/store";
import { setPage } from "../router";
import { Button } from "../../common/Button";
import { ExternalLink } from "../../common/ExternalLink";
import { describeError } from "../../profile/common";
import { PageShell } from "../PageShell";
import { saveBlob } from "../drawing/save";
import { grouped, hasEstimate, lineTotal, money, specsLine, vendorLabel } from "./model";
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
  }, [build?.id]);

  if (!build) {
    return (
      <PageShell title="Parts list">
        <div class="bom-bar"><Button variant="ghost" aria-label="Back to design" onClick={() => setPage("design")}>← Design</Button></div>
        <p class="bom-empty dim">Open a build to see its parts list. Made parts appear here automatically; add the bought ones as the design settles.</p>
      </PageShell>
    );
  }

  const patch = async (lineId: string, p: BomPatch) => {
    try { await actions.client().bomPatch(build.id, lineId, p); reload(build.id); }
    catch (e) { setError(describeError(e)); }
  };
  const remove = async (lineId: string) => {
    try { await actions.client().bomDelete(build.id, lineId); reload(build.id); }
    catch (e) { setError(describeError(e)); }
  };
  const add = async (body: NewBomLine) => {
    try { await actions.client().bomAdd(build.id, body); setAdding(false); reload(build.id); }
    catch (e) { setError(describeError(e)); throw e; }
  };
  const exportCsv = async () => {
    try {
      const blob = await actions.client().bomCsv(build.id);
      saveBlob(blob, `${build.name || "build"}-parts.csv`);
    } catch (e) { setError(describeError(e)); }
  };

  const g = bom ? grouped(bom.lines) : null;

  return (
    <PageShell title="Parts list">
      <div class="bom" data-bom-page data-build={build.id}>
        <div class="bom-bar">
          <Button variant="ghost" aria-label="Back to design" onClick={() => setPage("design")}>← Design</Button>
          <div class="bom-buildname text">{build.name || "Untitled build"}</div>
          <span class="bom-spacer" />
          <Button variant="default" onClick={() => setAdding(true)}>Add bought part</Button>
          <Button variant="ghost" disabled={!bom || bom.lines.length === 0} onClick={() => void exportCsv()}>Export CSV</Button>
        </div>

        {error && <p class="bom-error cv-fail text" role="status">{error}</p>}
        {adding && <AddRow onAdd={add} onCancel={() => setAdding(false)} />}

        {bom && bom.lines.length === 0 && !adding && (
          <p class="bom-empty dim">No parts yet. Parts the agent makes or buys land here, and you can add bought parts with the button above.</p>
        )}

        {g && g.made.length > 0 && <Section title="Made in-house" lines={g.made} onPatch={patch} onRemove={remove} />}
        {g && g.bought.length > 0 && <Section title="Bought" lines={g.bought} onPatch={patch} onRemove={remove} />}

        {bom && bom.lines.length > 0 && <Totals bom={bom} />}
      </div>
    </PageShell>
  );
}

function Section({ title, lines, onPatch, onRemove }: {
  title: string; lines: BomLine[]; onPatch: (id: string, p: BomPatch) => void; onRemove: (id: string) => void;
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
            <th scope="col" class="bom-c-total">Total</th>
            <th scope="col" class="bom-c-notes">Notes</th>
            <th scope="col" class="bom-c-rm"><span class="vh">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => <Row key={l.id} line={l} onPatch={onPatch} onRemove={onRemove} />)}
        </tbody>
      </table>
    </section>
  );
}

function Row({ line, onPatch, onRemove }: { line: BomLine; onPatch: (id: string, p: BomPatch) => void; onRemove: (id: string) => void }): JSX.Element {
  const total = lineTotal(line);
  const specs = specsLine(line);
  return (
    <tr data-bom-line={line.id} data-kind={line.kind}>
      <td class="bom-c-name">
        <div class="bom-name text">
          {line.url ? <ExternalLink class="bom-name-link" href={line.url}>{line.name || "Unnamed"}</ExternalLink> : (line.name || "Unnamed")}
        </div>
        {specs && <div class="bom-specs faint text">{specs}</div>}
      </td>
      <td class="bom-c-vendor text">{line.vendor ?? (line.kind === "made" ? "In-house" : "—")}</td>
      <td class="bom-c-qty">
        <NumField label={`Quantity of ${line.name || "part"}`} value={line.qty} min={0} step={1}
          onCommit={(v) => { if (v !== null && v !== line.qty) onPatch(line.id, { qty: v }); }} />
      </td>
      <td class="bom-c-cost">
        <NumField label={`Unit cost of ${line.name || "part"}`} value={line.unit_cost} min={0} step={0.01} placeholder="—"
          onCommit={(v) => { if (v !== line.unit_cost) onPatch(line.id, { unit_cost: v }); }} />
        {line.estimated && <span class="bom-est" title="Estimated from mass × material cost">estimate</span>}
      </td>
      <td class="bom-c-total text">{total === null ? "—" : money(total, line.currency)}</td>
      <td class="bom-c-notes">
        <TextField label={`Notes for ${line.name || "part"}`} value={line.notes}
          onCommit={(v) => { if (v !== line.notes) onPatch(line.id, { notes: v }); }} />
      </td>
      <td class="bom-c-rm">
        <button type="button" class="bom-rm" aria-label={`Remove ${line.name || "part"}`} onClick={() => onRemove(line.id)}>×</button>
      </td>
    </tr>
  );
}

function Totals({ bom }: { bom: Bom }): JSX.Element {
  const t = bom.totals;
  return (
    <div class="bom-totals" data-bom-totals>
      <div class="bom-grand">
        <span class="label">Total</span>
        <span class="bom-grand-amt text">{money(t.amount, t.currency)}</span>
      </div>
      <dl class="bom-split">
        <div><dt class="faint">Made</dt><dd class="text">{money(t.made, t.currency)}</dd></div>
        <div><dt class="faint">Bought</dt><dd class="text">{money(t.bought, t.currency)}</dd></div>
      </dl>
      {t.by_vendor.length > 0 && (
        <table class="bom-vendors">
          <caption class="label">By vendor</caption>
          <tbody>
            {t.by_vendor.map((v) => (
              <tr key={v.vendor ?? "none"}><td class="text">{vendorLabel(v)}</td><td class="bom-vendors-amt text">{money(v.amount, v.currency)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {hasEstimate(bom) && <p class="bom-est-note faint">Totals include estimated costs (shown "estimate"); confirm them before ordering.</p>}
    </div>
  );
}

/** A compact number input that commits on blur or Enter; empty commits null (unknown cost). */
function NumField({ label, value, min, step, placeholder, onCommit }: {
  label: string; value: number | null; min?: number; step?: number; placeholder?: string; onCommit: (v: number | null) => void;
}): JSX.Element {
  // Uncontrolled: the DOM holds the draft; the effect only writes the loaded value back when the
  // field isn't being edited. (A controlled value fights the async reload and drops keystrokes.)
  const asStr = value === null ? "" : String(value);
  const ref = useRef<HTMLInputElement>(null);
  const last = useRef(asStr);        // what was last committed / loaded, so Enter + blur don't double-fire
  useEffect(() => {
    last.current = asStr;
    if (ref.current && document.activeElement !== ref.current) ref.current.value = asStr;
  }, [asStr]);
  const commit = () => {
    const t = (ref.current?.value ?? "").trim();
    if (t === last.current) return;
    if (t === "") { last.current = ""; onCommit(null); return; }
    const n = Number(t);
    if (Number.isFinite(n) && (min === undefined || n >= min)) { last.current = t; onCommit(n); }
    else if (ref.current) ref.current.value = last.current;
  };
  return (
    <input ref={ref} class="bom-num" type="number" inputMode="decimal" aria-label={label} min={min} step={step} placeholder={placeholder}
      defaultValue={asStr} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />
  );
}

function TextField({ label, value, onCommit }: { label: string; value: string; onCommit: (v: string) => void }): JSX.Element {
  const ref = useRef<HTMLInputElement>(null);
  const last = useRef(value);
  useEffect(() => {
    last.current = value;
    if (ref.current && document.activeElement !== ref.current) ref.current.value = value;
  }, [value]);
  const commit = () => { const v = ref.current?.value ?? ""; if (v !== last.current) { last.current = v; onCommit(v); } };
  return (
    <input ref={ref} class="bom-notes-in" type="text" aria-label={label} defaultValue={value} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />
  );
}

function AddRow({ onAdd, onCancel }: { onAdd: (b: NewBomLine) => Promise<unknown>; onCancel: () => void }): JSX.Element {
  const [name, setName] = useState("");
  const [vendor, setVendor] = useState("");
  const [qty, setQty] = useState("1");
  const [cost, setCost] = useState("");
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => { first.current?.focus(); }, []);

  const submit = async (e: Event) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await onAdd({
        kind: "bought", name: name.trim(), vendor: vendor.trim() || null,
        qty: Number(qty) || 1, unit_cost: cost.trim() === "" ? null : Number(cost), source: "user",
      });
    } catch { setBusy(false); }
  };

  return (
    <form class="bom-add" onSubmit={submit} aria-label="Add a bought part">
      <input ref={first} class="bom-add-name" type="text" aria-label="Part name" placeholder="Part name" value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} required />
      <input class="bom-add-vendor" type="text" aria-label="Vendor" placeholder="Vendor" value={vendor} onInput={(e) => setVendor((e.target as HTMLInputElement).value)} />
      <input class="bom-num" type="number" aria-label="Quantity" min={1} step={1} value={qty} onInput={(e) => setQty((e.target as HTMLInputElement).value)} />
      <input class="bom-num" type="number" aria-label="Unit cost" min={0} step={0.01} placeholder="Unit cost" value={cost} onInput={(e) => setCost((e.target as HTMLInputElement).value)} />
      <Button type="submit" variant="default" disabled={busy || !name.trim()}>{busy ? "Adding…" : "Add"}</Button>
      <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
    </form>
  );
}
