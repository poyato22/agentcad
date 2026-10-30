// One drawing import as a card: progress while the core reads the drawing and builds the part,
// then the result (the part, the dimension match table, the original drawing beside Mises's own
// drawing of the part), or what went wrong.
//
// Safety: file names, notes and dimension text are untrusted and rendered as text only. Both
// drawings are shown ONLY as <img> from blob: URLs (an SVG in an <img> runs nothing).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { DrawingImport, MatchRow } from "../../api/imports";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { dismissImport, type ImportEntry } from "./state";

const KIND: Record<string, string> = { pdf: "PDF", png: "PNG", jpeg: "JPG", dxf: "DXF" };

const DIM_LABEL: Record<string, string> = {
  length: "Length", width: "Width", thickness: "Thickness", diameter: "Hole Ø", radius: "Hole radius",
  position_x: "Hole position X", position_y: "Hole position Y", hole_spacing_x: "Hole spacing X",
  hole_spacing_y: "Hole spacing Y", angle: "Angle", other: "Other", callout: "Hole callout",
};

const fmt = (v: number | null, unit = "") => (v === null ? "—" : `${Math.round(v * 100) / 100}${unit}`);

function rowResult(r: MatchRow): { text: string; cls: string } {
  if (r.status === "match") return { text: "✓ matches", cls: "ok" };
  if (r.status === "unchecked") return { text: "not checked", cls: "faint" };
  if (r.status === "missing") return { text: "✗ not found", cls: "bad" };
  if (r.kind === "callout") return { text: "✗ not on Mises's drawing", cls: "bad" };
  const off = r.measured !== null && r.expected !== null ? Math.round((r.measured - r.expected) * 100) / 100 : null;
  return { text: off === null ? "✗ differs" : `✗ off by ${off > 0 ? "+" : ""}${off}`, cls: "bad" };
}

function MatchTableView({ rows }: { rows: readonly MatchRow[] }): JSX.Element {
  return (
    <table class="imp-table" data-match-table>
      <thead>
        <tr><th scope="col">Dimension</th><th scope="col">Drawing</th><th scope="col">Part</th><th scope="col">Result</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const res = rowResult(r);
          const unit = r.kind === "angle" ? "°" : "";
          const callout = r.kind === "callout";
          return (
            <tr key={r.id} data-row-status={r.status}>
              <th scope="row">
                {DIM_LABEL[r.kind] ?? r.kind}
                {r.text && !callout && r.text !== fmt(r.expected) && <span class="imp-dimtext"> {r.text}</span>}
              </th>
              <td>{callout ? r.text : fmt(r.expected, unit)}</td>
              <td>{callout ? (r.status === "match" ? r.text : "—") : fmt(r.measured, unit)}</td>
              <td class={`imp-res-${res.cls}`}>{res.text}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** A Blob from the core as an object URL, released when it changes or the card goes away. */
function useBlobUrl(load: (() => Promise<Blob>) | null, deps: unknown[]): string | null | "error" {
  const [url, setUrl] = useState<string | null | "error">(null);
  useEffect(() => {
    if (!load) return;
    let live = true;
    let made: string | null = null;
    load().then(
      (b) => { if (live) { made = URL.createObjectURL(b); setUrl(made); } },
      () => { if (live) setUrl("error"); },
    );
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, deps);
  return url;
}

function Picture({ url, alt, caption }: { url: string | null | "error"; alt: string; caption: string }): JSX.Element {
  return (
    <figure class="imp-fig">
      <div class="imp-fig-box">
        {url === "error" ? <span class="faint">Could not load</span>
          : url ? <img src={url} alt={alt} draggable={false} /> : <span class="faint">Loading…</span>}
      </div>
      <figcaption class="label">{caption}</figcaption>
    </figure>
  );
}

function SideBySide({ rec }: { rec: DrawingImport }): JSX.Element {
  const c = actions.client();
  const original = useBlobUrl(() => c.drawingImportOriginal(rec.import_id, rec.kind === "dxf" ? "image/svg+xml" : "image/png"), [rec.import_id]);
  const ours = useBlobUrl(rec.part_id ? () => c.drawingFile(rec.part_id as string, "svg") : null, [rec.part_id, rec.attempts]);
  return (
    <div class="imp-side" data-side-by-side>
      <Picture url={original} alt={`Your drawing: ${rec.file_name}`} caption="Your drawing" />
      <Picture url={ours} alt={`Mises's drawing of ${rec.part_id ?? "the part"}`} caption="Mises's drawing of the part" />
    </div>
  );
}

function summary(rec: DrawingImport): string {
  const t = rec.table;
  if (!t) return "";
  const checked = t.matches + t.mismatches + t.missing;
  if (t.ok) return checked === 1 ? "The dimension matches" : `All ${checked} dimensions match`;
  return `${t.matches} of ${checked} dimensions match`;
}

function stageText(e: ImportEntry): string {
  if (!e.rec) return "Uploading…";
  const s = e.rec.stage;
  return s ? s.charAt(0).toUpperCase() + s.slice(1) + "…" : "Reading the drawing…";
}

export function ImportCard({ entry }: { entry: ImportEntry }): JSX.Element {
  const rec = entry.rec;
  const failed = entry.error !== null || rec?.status === "failed";
  const done = !failed && rec?.status === "done";
  const state = failed ? "failed" : done ? "done" : "working";
  const kind = rec ? KIND[rec.kind] : entry.fileName.split(".").pop()?.toUpperCase();
  return (
    <section class="imp-card" data-import-state={state} aria-label={`Drawing import: ${entry.fileName}`}>
      <header class="imp-head">
        {kind && <span class="imp-kind">{kind}</span>}
        <span class="imp-name" title={entry.fileName}>{rec?.file_name ?? entry.fileName}</span>
        <button type="button" class="imp-x" aria-label={`Dismiss ${entry.fileName}`} title="Dismiss"
          onClick={() => dismissImport(entry.key)}>×</button>
      </header>

      {state === "working" && (
        <div class="imp-progress" aria-live="polite">
          <p class="imp-stage">{stageText(entry)}</p>
          <div class="imp-bar" role="progressbar" aria-label="Making the part" aria-valuetext={stageText(entry)}><span /></div>
          {rec && rec.max_attempts > 1 && rec.attempts > 0 && (
            <p class="imp-sub faint">Attempt {rec.attempts} of {rec.max_attempts}</p>
          )}
        </div>
      )}

      {state === "failed" && (
        <div class="imp-failed" role="alert">
          <p class="imp-error">{entry.error?.message ?? rec?.error ?? "The part could not be made."}</p>
          {entry.error?.hint && <p class="imp-hint">{entry.error.hint}</p>}
        </div>
      )}

      {state === "done" && rec && (
        <div class="imp-done">
          <p class="imp-result" data-result={rec.result ?? ""}>
            <span class={`led ${rec.result === "match" ? "led-green" : "led-amber"}`} aria-hidden="true" />
            <span>Made <b>{rec.part_id}</b> · {summary(rec)}</span>
          </p>
          <SideBySide rec={rec} />
          {rec.table && <MatchTableView rows={rec.table.rows} />}
          {rec.notes.length > 0 && (
            <ul class="imp-notes">{rec.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          )}
          <div class="imp-actions">
            <Button variant="primary" onClick={() => { if (rec.part_id) actions.selectPart(rec.part_id); }}>Open part</Button>
          </div>
        </div>
      )}
    </section>
  );
}
