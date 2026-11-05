// One drawing import as a card: progress while the core reads the drawing and builds the part,
// then the result (the part, the dimension match table, the original drawing beside Mises's own
// drawing of the part), or what went wrong.
//
// Safety: file names, notes and dimension text are untrusted and rendered as text only. Both
// drawings are shown ONLY as <img> from blob: URLs (an SVG in an <img> runs nothing).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { DrawingImport, MatchRow } from "../../api/imports";
import { builds } from "../../state/builds";
import { openBuild } from "../../state/home";
import { actions } from "../../state/store";
import { openPart as openPartAlone } from "../assembly/focus";
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
  if (r.kind === "callout") return { text: "✗ not on AgentCAD's drawing", cls: "bad" };
  const off = r.measured !== null && r.expected !== null ? Math.round((r.measured - r.expected) * 100) / 100 : null;
  return { text: off === null ? "✗ differs" : `✗ off by ${off > 0 ? "+" : ""}${off}`, cls: "bad" };
}

function MatchTableView({ rows, basis }: { rows: readonly MatchRow[]; basis: DrawingImport["basis"] }): JSX.Element {
  const caption = basis === "drawing"
    ? "Checked against the drawing"
    : "Built part vs the dimensions the model read from the drawing";
  return (
    <table class="imp-table" data-match-table data-basis={basis}>
      <caption class="imp-caption">
        {caption}
        {basis === "reading" && <span class="imp-caption-note"> · the reading itself is the model's, not checked against the drawing</span>}
      </caption>
      <thead>
        <tr><th scope="col">Dimension</th><th scope="col">{basis === "drawing" ? "Drawing" : "Model's reading"}</th><th scope="col">Part</th><th scope="col">Result</th></tr>
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
              <td class={`imp-res-${res.cls}`}>
                {res.text}
                {r.evidence === "printed" && <span class="imp-ev imp-res-ok" title="This number is printed on the drawing (its PDF text)"> · on drawing</span>}
                {r.evidence === "not printed" && <span class="imp-ev imp-res-warn" title="No such number is printed on the drawing (its PDF text)"> · not on drawing</span>}
              </td>
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
      <Picture url={ours} alt={`AgentCAD's drawing of ${rec.part_id ?? "the part"}`} caption="AgentCAD's drawing of the part" />
    </div>
  );
}

function summary(rec: DrawingImport): string {
  const t = rec.table;
  if (!t) return "";
  const checked = t.matches + t.mismatches + t.missing;
  const what = rec.basis === "drawing" ? "the drawing" : "the model's reading";
  if (t.ok) return checked === 1 ? `The dimension matches ${what}` : `All ${checked} dimensions match ${what}`;
  return `${t.matches} of ${checked} dimensions match ${what}`;
}

/** The independent checks a PDF / image import has (none for a DXF: it is checked directly). */
function Checks({ rec }: { rec: DrawingImport }): JSX.Element | null {
  if (rec.basis === "drawing") return null;
  const tl = rec.checks.text_layer;
  const look = rec.checks.second_look;
  const notOn = [...(tl?.unsupported ?? []), ...(tl?.built_unsupported ?? [])];
  return (
    <div class="imp-checks" data-checks>
      {tl && (
        <p class={`imp-check ${!tl.available ? "faint" : tl.ok ? "imp-res-ok" : "imp-res-warn"}`} data-check="text">
          {!tl.available ? (tl.note ?? "No text on the drawing to check against")
            : tl.ok ? "Every value is a number printed on the drawing (its PDF text)"
              : `Not printed on the drawing: ${notOn.join(", ")}`}
        </p>
      )}
      {look && (
        <div class="imp-look" data-check="second-look" data-look={look.status}>
          <p class="imp-look-head"><span class="label">Second opinion</span>{" "}
            {look.status === "ok" ? "The model compared both drawings and found no difference."
              : look.status === "issues" ? "The model compared both drawings and noticed:"
                : (look.note ?? "Not available")}
          </p>
          {look.status === "issues" && (
            <ul class="imp-notes">
              {(look.mismatches ?? []).map((m, i) => (
                <li key={i}>{m.feature}: drawing {m.drawing}; part {m.part}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function stageText(e: ImportEntry): string {
  if (!e.rec) return "Uploading…";
  const s = e.rec.stage;
  return s ? s.charAt(0).toUpperCase() + s.slice(1) + "…" : "Reading the drawing…";
}

/** The build a part landed in, by name (m2: an import from Home no longer lands silently). */
function buildName(id: string | null): string | null {
  return id ? builds.value.find((b) => b.id === id)?.name ?? null : null;
}

async function openPart(rec: DrawingImport, key: string): Promise<void> {
  if (rec.project_id && builds.peek().some((b) => b.id === rec.project_id && b.trashed_at === null)) await openBuild(rec.project_id);
  if (rec.part_id) openPartAlone(rec.part_id);              // Plan 9 D7: opened alone, ← Assembly returns
  dismissImport(key);
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
            <span>Made <b>{rec.part_id}</b>{buildName(rec.project_id) && <> in <b data-import-build>{buildName(rec.project_id)}</b></>} · {summary(rec)}</span>
          </p>
          <SideBySide rec={rec} />
          {rec.table && <MatchTableView rows={rec.table.rows} basis={rec.basis} />}
          <Checks rec={rec} />
          {rec.notes.length > 0 && (
            <ul class="imp-notes">{rec.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          )}
          <div class="imp-actions">
            {/* m2: opens the part's own build (from Home too) and puts the card away, so it no
                longer floats over the viewer */}
            <Button variant="primary" onClick={() => { void openPart(rec, entry.key); }}>Open part</Button>
          </div>
        </div>
      )}
    </section>
  );
}
