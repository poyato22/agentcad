// Fake core: drawing import routes (core server/imports_api.py + imports/drawing_import.py), with
// the real core's shapes and error answers (checked by test/contract.imports.test.ts against
// fixtures dumped from the real core).
//
//   POST /imports/drawing   multipart `file` (+ project_id): sniffed by content (PDF, PNG, JPEG,
//                           DXF), at most 20 MB → the import record, status "reading".
//   GET  /imports/{id}      each poll moves it one stage on: building → done (deterministic,
//                           no timers). The result is the real core's DXF fixture table.
//   GET  /imports/{id}/original   the original drawing (an SVG for a DXF, else the PNG bytes).
//
// Scripting by file name: "novision" (a PDF / image) → the real core's no-vision 400; "mismatch"
// → done with one row that does not match; "unbuildable" → failed.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { PartSummary } from "../../src/api/types";
import { HttpError, mises, RAW, type EventIn, type Route } from "./server";

const FIXTURES = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const MAX_BYTES = 20 * 1024 * 1024;
const IMPORT_ID_RE = /^imp_[0-9a-f]{12}$/;
type Json = Record<string, unknown>;
const fx = (name: string): Json => JSON.parse(readFileSync(resolve(FIXTURES, name), "utf8")) as Json;

export interface Multipart { fields: Record<string, string>; files: Record<string, { name: string; data: Buffer }> }
export interface FakeImport { rec: Json; file: Buffer; polls: number }

/** A minimal multipart/form-data parser (enough for the app's FormData uploads). */
export function parseMultipart(body: Buffer, contentType: string): Multipart {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
  const out: Multipart = { fields: {}, files: {} };
  if (!m) return out;
  const sep = Buffer.from(`--${m[1] ?? m[2]}`);
  let at = body.indexOf(sep);
  while (at !== -1) {
    const start = at + sep.length;
    if (body.subarray(start, start + 2).toString() === "--") break;
    const next = body.indexOf(sep, start);
    if (next === -1) break;
    const part = body.subarray(start + 2, next - 2);           // skip CRLF after the boundary / before the next
    const split = part.indexOf("\r\n\r\n");
    if (split !== -1) {
      const head = part.subarray(0, split).toString("utf8");
      const data = part.subarray(split + 4);
      const name = /name="([^"]*)"/.exec(head)?.[1];
      const filename = /filename="([^"]*)"/.exec(head)?.[1];
      if (name !== undefined) {
        if (filename !== undefined) out.files[name] = { name: filename, data: Buffer.from(data) };
        else out.fields[name] = data.toString("utf8");
      }
    }
    at = next;
  }
  return out;
}

function sniff(b: Buffer): "pdf" | "png" | "jpeg" | "dxf" | "heic" | null {
  if (b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "png";
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "jpeg";
  if (b.length >= 12 && b.toString("latin1", 4, 8) === "ftyp") return "heic";
  if (b.toString("latin1", 0, 5) === "%PDF-") return "pdf";
  if (b.toString("latin1", 0, 22) === "AutoCAD Binary DXF\r\n\x1a\x00") return "dxf";
  const lines = b.toString("latin1", 0, 4096).replace(/^\xef\xbb\xbf/, "").split(/\r?\n/).map((l) => l.trim());
  let i = 0;
  while (i + 1 < lines.length && lines[i] === "999") i += 2;
  return lines[i] === "0" && lines[i + 1] === "SECTION" ? "dxf" : null;
}

function partBase(name: string): string {
  let stem = (name.split(/[\\/]/).pop() ?? "").replace(/\.[^.]*$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  stem = stem.replace(/[0-9_]+$/, "");
  if (!stem || !/^[a-z]/.test(stem)) stem = stem ? `drawing_${stem}` : "drawing_part";
  return stem.slice(0, 30).replace(/_+$/, "") || "drawing_part";
}

export interface ImportDeps {
  get(): Map<string, FakeImport>;
  parts(): PartSummary[];
  setParts(list: PartSummary[]): void;
  activeProject(): string;
  emit(ev: EventIn): void;
}

export function importRoutes(d: ImportDeps): Route[] {
  const find = (raw: string): FakeImport => {
    const id = decodeURIComponent(raw);
    if (!IMPORT_ID_RE.test(id)) throw mises("invalid_input", `bad import id: '${id}'`);
    const imp = d.get().get(id);
    if (!imp) throw mises("not_found", `unknown drawing import: ${id}`);
    return imp;
  };
  return [
    {
      method: "POST", re: /^\/imports\/drawing$/, h: (_m, _q, body) => {
        const mp = (body as { __multipart?: Multipart } | undefined)?.__multipart;
        const file = mp?.files.file;
        if (!file) throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", "file"], msg: "Field required", input: null }] });
        if (file.data.length > MAX_BYTES) throw mises("invalid_input", "the drawing is larger than 20 MB", "export a smaller file (20 MB at most)");
        if (!file.data.length) throw mises("invalid_input", "the file is empty");
        const kind = sniff(file.data);
        if (kind === "heic") throw mises("invalid_input", "HEIC images are not supported for drawings", "export it as PNG or JPEG");
        if (!kind) throw mises("invalid_input", "the drawing must be a PDF, PNG, JPEG or DXF file", "DWG and other CAD formats: export the drawing as DXF or PDF first");
        if (kind !== "dxf" && file.name.includes("novision")) {
          const r = fx("drawing_import_refused.json");
          throw new HttpError(400, r);
        }
        const id = `imp_${randomBytes(6).toString("hex")}`;
        const base = partBase(file.name);
        const taken = new Set(d.parts().map((p) => p.part_id));
        let part = base;
        for (let i = 0; taken.has(part); i++) part = `${base}_${String.fromCharCode(98 + i)}`;
        const rec: Json = {
          ...fx("drawing_import_upload.json"), import_id: id, kind, file_name: file.name.split(/[\\/]/).pop() ?? "drawing",
          project_id: mp?.fields.project_id || d.activeProject(), run_id: kind === "dxf" ? null : `run_${randomBytes(4).toString("hex")}`,
          max_attempts: kind === "dxf" ? 1 : 3, original_url: `/imports/${id}/original`, _part: part,
          stage: kind === "dxf" ? "building the part" : "the agent is reading the drawing",
        };
        d.get().set(id, { rec, file: file.data, polls: 0 });
        return publicRec(rec);
      },
    },
    {
      method: "GET", re: /^\/imports\/([^/]+)$/, h: (m) => {
        const imp = find(m[1]);
        advance(imp, d);
        return publicRec(imp.rec);
      },
    },
    {
      method: "GET", re: /^\/imports\/([^/]+)\/original$/, h: (m, _q, _b, _r, res) => {
        const imp = find(m[1]);
        const svg = imp.rec.kind === "dxf";
        const bytes = svg ? readFileSync(resolve(FIXTURES, "drawing_import_original.svg")) : imp.file;
        res.writeHead(200, svg
          ? { "Content-Type": "image/svg+xml", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "Content-Length": bytes.length }
          : { "Content-Type": imp.rec.kind === "jpeg" ? "image/jpeg" : "image/png", "Content-Length": bytes.length });
        res.end(bytes);
        return RAW;
      },
    },
  ];
}

function publicRec(rec: Json): Json {
  const { _part: _p, ...out } = rec;
  return out;
}

function advance(imp: FakeImport, d: ImportDeps): void {
  const rec = imp.rec;
  if (rec.status === "done" || rec.status === "failed") return;
  imp.polls += 1;
  const name = String(rec.file_name);
  if (imp.polls === 1) {
    Object.assign(rec, { status: "building", stage: "building the part (attempt 1)" });
    return;
  }
  if (name.includes("unbuildable")) {
    Object.assign(rec, { status: "failed", stage: "", attempts: 1, error: "the part did not build: the outline is not closed" });
    return;
  }
  const done = fx("drawing_import_done.json");
  const part = String(rec._part);
  const table = structuredClone(done.table) as { rows: Json[]; ok: boolean; matches: number; mismatches: number };
  if (name.includes("mismatch")) {
    const w = table.rows.find((r) => r.kind === "width");
    if (w) Object.assign(w, { measured: 59.5, status: "mismatch" });
    Object.assign(table, { ok: false, matches: table.matches - 1, mismatches: table.mismatches + 1 });
  }
  Object.assign(rec, {
    status: "done", stage: "", attempts: rec.kind === "dxf" ? 1 : 2, table, result: table.ok ? "match" : "mismatch",
    part_id: part, drawing_url: `/parts/${part}/drawing.svg`, notes: done.notes, updated_at: Date.now() / 1000,
  });
  d.setParts([...d.parts(), { part_id: part, vars: { thickness: 6, hole_a_d: 6, hole_b_d: 10 }, metrics: null, mtime: Date.now() / 1000 }]);
  d.emit({ type: "part_changed", run_id: null, data: { part_id: part, version: 1, by: "user", run_id: null, summary: `created ${part} from script` } });
  d.emit({ type: "agent_announce", run_id: null, data: { run_id: null, kind: "drawing_imported", title: `Made ${part} from ${name}`,
    detail: `${table.matches} of ${table.rows.filter((r) => r.status !== "unchecked").length} dimensions match`, ref: { import_id: rec.import_id, part_id: part } } });
}
