// Fake core: Plan 4 drawing / printer / slice / print routes, with the real core's shapes and
// error answers (core server/drawing_api.py, print_api.py; checked by test/contract.test.ts
// against fixtures dumped from the real core). The drawing is the real core's SVG fixture with
// the part's name and material swapped in. Slices finish after `slice_ms`. A print raises the
// print_send approval card on the shared "ui" run (approval_requested); the call ends when the
// card is decided (approved → done and the printer starts PRINTING, rejected → failed "denied").
// Printer API keys are only marked present: never stored, never returned.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type {
  AgentRun, Approval, DrawingInfo, PartSummary, PrintCall, PrintCapabilities, Printer, PrinterStatus, Slice,
} from "../../src/api/types";
import { HttpError, mises, RAW, type Body, type EventIn, type Route } from "./server";

const FIXTURES = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const SVG = (): string => readFileSync(resolve(FIXTURES, "drawing.svg"), "utf8");
const DRAWING = (): DrawingInfo => JSON.parse(readFileSync(resolve(FIXTURES, "drawing.json"), "utf8")) as DrawingInfo;
/** The 1×1 thumbnail PrusaSlicer embedded in core/tests/print/fixtures/sample.gcode. */
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "base64");

export const INSTALL_COMMAND = "brew install --cask prusaslicer";
const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;
const PRINTER_ID_RE = /^[a-z][a-z0-9_-]{1,30}$/;
const SLICE_ID_RE = /^slice_[0-9a-f]{10}$/;
const HOST_RE = /^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$/;
const MATERIAL_RE = /^[A-Za-z0-9_+-]{1,20}$/;
const SENDABLE = new Set(["IDLE", "READY", "FINISHED", "STOPPED"]);
const MODELS = ["MK4", "MINI"];
const MATERIALS = [
  { name: "PLA", density_g_cm3: 1.24, usd_per_kg: 22.0 }, { name: "PETG", density_g_cm3: 1.27, usd_per_kg: 25.0 },
  { name: "ABS", density_g_cm3: 1.04, usd_per_kg: 22.0 }, { name: "ASA", density_g_cm3: 1.07, usd_per_kg: 28.0 },
  { name: "TPU", density_g_cm3: 1.21, usd_per_kg: 35.0 },
];
const KINDS = [{ kind: "prusalink", available: true, note: null }, { kind: "bambu", available: false, note: "coming later" }];

/** What a printer reports: a PrusaLink state, or unreachable (null). */
export interface FakePrinterState { state: string | null; job?: PrinterStatus["job"] }

export interface PrintScenario {
  printers?: Omit<Printer, "has_key">[];
  /** Per printer id; default IDLE. */
  states?: Record<string, FakePrinterState>;
  slicer_installed?: boolean;
  /** How long a slice runs (default 300 ms). */
  slice_ms?: number;
  /** A slice fails with this message instead of finishing. */
  slice_error?: string | null;
  /** Part ids that have no model.step (drawing 404). */
  unbuilt?: string[];
  /** Delay before the approval card appears after POST /prints (default 50 ms). */
  card_ms?: number;
}

interface FakePrint { call: PrintCall; approvalId: string | null; error: Record<string, unknown> | null }

export interface FakePrinting {
  printers: Omit<Printer, "has_key">[];
  states: Record<string, FakePrinterState>;
  slicerInstalled: boolean;
  sliceMs: number;
  sliceError: string | null;
  unbuilt: Set<string>;
  cardMs: number;
  slices: Map<string, Slice & { gcode_sha256: string; file_name: string; call_id: string }>;
  prints: Map<string, FakePrint>;
  uiRunId: string | null;
  timers: ReturnType<typeof setTimeout>[];
}

export function printingFrom(sc: PrintScenario | undefined): FakePrinting {
  return {
    printers: structuredClone(sc?.printers ?? []), states: structuredClone(sc?.states ?? {}),
    slicerInstalled: sc?.slicer_installed ?? true, sliceMs: sc?.slice_ms ?? 300, sliceError: sc?.slice_error ?? null,
    unbuilt: new Set(sc?.unbuilt ?? []), cardMs: sc?.card_ms ?? 50, slices: new Map(), prints: new Map(), uiRunId: null, timers: [],
  };
}

export function stopPrinting(p: FakePrinting): void {
  p.timers.forEach(clearTimeout);
  p.timers = [];
}

const hex = (n: number) => randomBytes(n).toString("hex");
const now = () => Date.now() / 1000;
const bad = (message: string, hint: string | null = null): HttpError => mises("invalid_input", message, hint);

function body(raw: unknown): Body {
  if (raw === undefined) throw bad("body must be JSON");
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw bad("body must be a JSON object");
  return raw as Body;
}

function slug(name: string): string {
  const s = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30);
  return /^[a-z]/.test(s) ? s : `p_${s}`.slice(0, 30);
}

export interface PrintingDeps {
  get(): FakePrinting;
  parts(): readonly PartSummary[];
  defaultMaterial(): string;
  emit(ev: EventIn, extras?: { run?: AgentRun }): void;
  approval(id: string): Approval | undefined;
}

export function printingRoutes(d: PrintingDeps): Route[] {
  const printer = (id: string) => {
    const p = d.get().printers.find((x) => x.id === id);
    if (!p) throw mises("not_found", `no printer ${JSON.stringify(id)}`, "add it in Profile → Printers");
    return p;
  };
  const stateOf = (id: string): FakePrinterState => d.get().states[id] ?? { state: "IDLE" };
  const statusOf = (id: string): PrinterStatus => {
    const st = stateOf(id);
    const out: PrinterStatus = { printer_id: id, reachable: false, state: null, sendable: false, temp_nozzle: null, temp_bed: null, job: null, error: null };
    if (st.state === null) return { ...out, error: "printer unreachable (ConnectError)", hint: "check that the printer is on and on the same network" };
    return { ...out, reachable: true, state: st.state, sendable: SENDABLE.has(st.state), temp_nozzle: 215.0, temp_bed: 60.0, job: st.job ?? null };
  };
  const partId = (raw: string) => {
    const id = decodeURIComponent(raw);
    if (!PART_ID_RE.test(id)) throw bad(`bad part id: ${JSON.stringify(id)}`, `part ids match ${PART_ID_RE.source}`);
    if (!d.parts().some((p) => p.part_id === id) || d.get().unbuilt.has(id)) throw mises("not_found", `no built part ${JSON.stringify(id)}`, "build it with mirror_part_create");
    return id;
  };
  const drawingMaterial = (q: URLSearchParams) => {
    const m = q.get("material");
    if (m !== null && !MATERIAL_RE.test(m)) throw bad("bad material name");
    return m ?? d.defaultMaterial();
  };
  const sliceOf = (id: string) => {
    if (!SLICE_ID_RE.test(id)) throw bad(`bad slice id ${JSON.stringify(id)}`, `slice ids match ${SLICE_ID_RE.source}`);
    const s = d.get().slices.get(id);
    if (!s) throw mises("not_found", `no slice ${JSON.stringify(id)}`);
    return s;
  };
  const sliceView = (s: Slice): Slice => ({
    slice_id: s.slice_id, status: s.status, part_id: s.part_id, printer_id: s.printer_id, material: s.material, model: s.model,
    created_at: s.created_at, estimate: s.estimate, preview: s.preview, error: s.error, hint: s.hint, sent_to: s.sent_to,
  });
  /** The shared "ui" run (agent "you", provider "ui"); the core hides it from GET /runs. */
  const uiRun = (): string => {
    const st = d.get();
    if (st.uiRunId) return st.uiRunId;
    const t = now();
    st.uiRunId = hex(6);
    d.emit({ type: "run_started", run_id: st.uiRunId, data: { agent: "you", provider: "ui", model: "ui", parent_id: null, external: true } }, {
      run: { id: st.uiRunId, parent_id: null, agent: "you", provider: "ui", model: "ui", task: "actions started from the app", status: "running", branch: null,
        onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: t, updated_at: t, owner_pid: process.pid, external: true },
    });
    return st.uiRunId;
  };
  /** A print call's state follows its card (the real core waits on the approval). */
  const settle = (p: FakePrint): PrintCall => {
    if (p.call.status !== "running") return p.call;
    if (p.error) { p.call = { ...p.call, status: "failed", error: p.error }; return p.call; }
    const a = p.approvalId ? d.approval(p.approvalId) : undefined;
    if (!a || a.status === "pending") return p.call;
    if (a.status === "approved" || a.status === "executed") {
      const sid = String(a.payload.slice_id);
      const pid = String(a.payload.printer_id);
      const s = d.get().slices.get(sid);
      if (s) s.sent_to = pid;
      d.get().states[pid] = { state: "PRINTING", job: { id: 7, progress: 0, time_remaining: s?.estimate?.time_s ?? 0 } };
      p.call = {
        ...p.call, status: "done",
        output: { ok: true, summary: `sent ${String(a.payload.file_name)} to ${printer(pid).name}; the printer is starting the job`,
          data: { slice_id: sid, printer_id: pid, file: a.payload.file_name }, handles: [], warnings: [], step_kind: "tool_result" },
      };
    } else {
      p.call = { ...p.call, status: "failed", error: { error: "denied", message: `approval ${a.status} for print_send`, hint: null, retryable: false } };
    }
    return p.call;
  };

  return [
    // ---- drawings
    {
      method: "GET", re: /^\/parts\/([^/]+)\/drawing\.svg$/, h: (m, q, _b, _r, res) => {
        const id = partId(m[1]);
        const material = drawingMaterial(q);
        const svg = SVG().replaceAll("mount_plate", id).replace(/MATERIAL: [A-Z0-9_+-]+/, `MATERIAL: ${material}`);
        res.writeHead(200, { "Content-Type": "image/svg+xml", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "Cache-Control": "private, no-cache" });
        res.end(svg);
        return RAW;
      },
    },
    {
      method: "GET", re: /^\/parts\/([^/]+)\/drawing\.dxf$/, h: (m, q, _b, _r, res) => {
        const id = partId(m[1]);
        drawingMaterial(q);
        res.writeHead(200, { "Content-Type": "image/vnd.dxf", "Content-Disposition": `attachment; filename="${id}.dxf"`, "Cache-Control": "private, no-cache" });
        res.end(`0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n`);
        return RAW;
      },
    },
    {
      method: "GET", re: /^\/parts\/([^/]+)\/drawing\.json$/, h: (m, q) => {
        const id = partId(m[1]);
        const part = d.parts().find((p) => p.part_id === id) as PartSummary;
        const size = part.metrics?.bbox.size;
        return {
          ...DRAWING(), label: id, material: drawingMaterial(q), step_mtime_ns: Math.round(part.mtime * 1e9),
          ...(size ? { overall_mm: size.map((x) => Math.round(x * 100) / 100) } : {}),
        };
      },
    },
    // ---- printers
    {
      method: "GET", re: /^\/print\/capabilities$/, h: (): PrintCapabilities => ({
        slicer: { installed: d.get().slicerInstalled, path: d.get().slicerInstalled ? "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer" : null, install_command: INSTALL_COMMAND },
        kinds: KINDS, models: MODELS, materials: MATERIALS,
      }),
    },
    { method: "GET", re: /^\/printers$/, h: () => d.get().printers.map((p) => ({ ...p, has_key: true })) },
    {
      method: "POST", re: /^\/printers$/, h: (_m, _q, raw) => {
        const b = body(raw);
        const kind = b.kind ?? "prusalink";
        if (kind === "bambu") throw bad("Bambu printers are coming later", "only PrusaLink printers work today");
        const key = b.api_key;
        if (typeof key !== "string" || !key.trim()) throw bad("api_key is required", "send a non-empty string");
        if (typeof b.name !== "string" || !b.name.trim()) throw bad("name is required");
        const id = typeof b.id === "string" && b.id ? b.id : slug(b.name);
        const host = String(b.host ?? "").trim();
        const model = b.model ?? "MK4";
        const errs: string[] = [];
        if (!PRINTER_ID_RE.test(id)) errs.push(`id: Value error, printer id must match ${PRINTER_ID_RE.source}`);
        if (kind !== "prusalink") errs.push("kind: Input should be 'prusalink'");
        if (!HOST_RE.test(host) || (host.includes(":") && Number(host.split(":").pop()) > 65535)) {
          errs.push("host: Value error, printer host must be a hostname or IP address, optionally :port (no scheme or path)");
        }
        if (typeof model !== "string" || !MODELS.includes(model)) errs.push("model: Input should be 'MK4' or 'MINI'");
        if (b.name.trim().length > 60) errs.push("name: String should have at most 60 characters");
        if (errs.length) throw bad(`invalid printer: ${errs.join("; ")}`);
        if (d.get().printers.some((p) => p.id === id)) throw bad(`a printer with id '${id}' already exists`, "pick another name, or delete the old one first");
        const entry = { id, kind: "prusalink", host, name: b.name.trim(), model: model as string };
        d.get().printers.push(entry);
        return { ...entry, has_key: true };
      },
    },
    {
      method: "DELETE", re: /^\/printers\/([^/]+)$/, h: (m) => {
        const id = decodeURIComponent(m[1]);
        const st = d.get();
        if (!st.printers.some((p) => p.id === id)) throw mises("not_found", `no printer '${id}'`);
        st.printers = st.printers.filter((p) => p.id !== id);
        return { ok: true };
      },
    },
    {
      method: "GET", re: /^\/printers\/([^/]+)\/status$/, h: (m) => {
        const id = decodeURIComponent(m[1]);
        printer(id);
        return statusOf(id);
      },
    },
    // ---- slices
    {
      method: "POST", re: /^\/slices$/, h: (_m, _q, raw, _r, res) => {
        const b = body(raw);
        const pid = b.part_id;
        const prid = b.printer_id;
        if (typeof pid !== "string" || !PART_ID_RE.test(pid)) throw bad("part_id is required", `part ids match ${PART_ID_RE.source}`);
        if (typeof prid !== "string" || !PRINTER_ID_RE.test(prid)) throw bad("printer_id is required");
        if (!d.parts().some((p) => p.part_id === pid)) throw mises("not_found", `no built part '${pid}'`, "build it with mirror_part_create");
        const pr = printer(prid);
        const material = String(b.material || d.defaultMaterial()).toUpperCase();
        const mat = MATERIALS.find((x) => x.name === material);
        if (!mat) throw bad(`unknown material '${material}'`, `materials: ${MATERIALS.map((x) => x.name).join(", ")}`);
        const st = d.get();
        if (!st.slicerInstalled) {
          const text = JSON.stringify({ error: "tool_failed", message: "PrusaSlicer is not installed", hint: `install it with: ${INSTALL_COMMAND}`, retryable: false });
          res.writeHead(424, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
          res.end(text);
          return RAW;
        }
        const sid = `slice_${hex(5)}`;
        const run = uiRun();
        const callId = `ui_${hex(6)}`;
        st.slices.set(sid, {
          slice_id: sid, status: "running", part_id: pid, printer_id: pr.id, material, model: pr.model, created_at: now(),
          estimate: null, preview: false, error: null, hint: null, sent_to: null, gcode_sha256: hex(32), file_name: `mises_${pid}.gcode`, call_id: callId,
        });
        st.timers.push(setTimeout(() => {
          const s = st.slices.get(sid);
          if (!s || s.status !== "running") return;
          if (st.sliceError) { Object.assign(s, { status: "failed", error: st.sliceError, hint: "check the part in the slicer" }); return; }
          const g = 7.51;
          Object.assign(s, {
            status: "done", preview: true,
            estimate: { time_s: 93802, time_text: "1d 2h 3m 22s", filament_mm: 2519.58, filament_g: g, cost_usd: Math.round((g / 1000) * mat.usd_per_kg * 1e4) / 1e4 },
          });
        }, st.sliceMs));
        return { slice_id: sid, status: "running", run_id: run, call_id: callId };
      },
    },
    { method: "GET", re: /^\/slices\/([^/]+)$/, h: (m) => sliceView(sliceOf(decodeURIComponent(m[1]))) },
    {
      method: "GET", re: /^\/slices\/([^/]+)\/preview\.png$/, h: (m, _q, _b, _r, res) => {
        const s = sliceOf(decodeURIComponent(m[1]));
        if (!s.preview) throw mises("not_found", "this slice has no preview image", "PrusaSlicer embeds a thumbnail only when it can render one");
        res.writeHead(200, { "Content-Type": "image/png", "Content-Length": PNG.length, "Cache-Control": "private, no-cache" });
        res.end(PNG);
        return RAW;
      },
    },
    {
      method: "POST", re: /^\/slices\/([^/]+)\/cancel$/, h: (m) => {
        const s = sliceOf(decodeURIComponent(m[1]));
        if (s.status !== "running") throw mises("not_found", "that slice is not running");
        Object.assign(s, { status: "cancelled", error: "cancelled" });
        return { ok: true };
      },
    },
    // ---- prints (irreversible: approval card)
    {
      method: "POST", re: /^\/prints$/, h: (_m, _q, raw, _r, res) => {
        const b = body(raw);
        const sid = b.slice_id;
        const prid = b.printer_id;
        if (typeof sid !== "string" || !SLICE_ID_RE.test(sid)) throw bad("slice_id is required");
        if (typeof prid !== "string") throw bad("printer_id is required");
        const s = sliceOf(sid);
        if (s.status !== "done") throw bad(`slice ${sid} is ${s.status}`, "wait for the slice to finish");
        const pr = printer(prid);
        if (s.model !== pr.model) throw mises("denied", `this slice is for a ${s.model}, but '${pr.name}' is a ${pr.model}`, "slice again for this printer");
        const st = statusOf(pr.id);
        if (!st.reachable) throw mises("transient", "printer unreachable (ConnectError)", "check that the printer is on and on the same network");
        if (!st.sendable) {
          const text = JSON.stringify({ error: "denied", message: `printer '${pr.name}' reports ${st.state}; not sending a job`,
            hint: "clear the error or wait until the printer is idle, then try again", retryable: false, state: st.state });
          res.writeHead(409, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
          res.end(text);
          return RAW;
        }
        const run = uiRun();
        const callId = `ui_${hex(6)}`;
        const p: FakePrint = { call: { call_id: callId, run_id: run, tool: "print_send", status: "running", output: null, error: null }, approvalId: null, error: null };
        const fp = d.get();
        fp.prints.set(callId, p);
        fp.timers.push(setTimeout(() => {
          const t = now();
          const payload = { file_name: s.file_name, gcode_sha256: s.gcode_sha256, printer_id: pr.id, slice_id: sid };
          const est = s.estimate as NonNullable<Slice["estimate"]>;
          const approval: Approval = {
            id: hex(6), run_id: run, action: "print_send", payload, payload_hash: hex(32), microversion: null,
            deltas: {
              cost_usd: est.cost_usd, filament_g: est.filament_g, file_name: s.file_name, gcode_sha256_short: s.gcode_sha256.slice(0, 12),
              material: s.material, part_id: s.part_id, preview_available: s.preview,
              printer: { host: pr.host, id: pr.id, model: pr.model, name: pr.name, state: st.state }, slice_model: s.model,
              time: est.time_text, time_s: est.time_s,
            },
            status: "pending", expires_at: t + 1800, decided_by: null, decided_at: null, edited_payload: null,
          };
          p.approvalId = approval.id;
          d.emit({ type: "tool_call", run_id: run, data: { call_id: callId, name: "print_send", input: payload } });
          d.emit({ type: "approval_requested", run_id: run, data: approval as unknown as Record<string, unknown> });
        }, fp.cardMs));
        return { call_id: callId, run_id: run, status: "running" };
      },
    },
    {
      method: "GET", re: /^\/prints\/([^/]+)$/, h: (m) => {
        const p = d.get().prints.get(decodeURIComponent(m[1]));
        if (!p) throw mises("not_found", `no print request '${decodeURIComponent(m[1])}'`);
        return settle(p);
      },
    },
  ];
}
