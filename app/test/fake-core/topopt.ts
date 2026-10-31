// Fake core: generative design routes (core server/topopt_api.py, Plan 9 D14) with the real
// core's shapes. Records, events, files and refusals come from the fixtures the real core wrote
// with the sandboxed optimizer and CalculiX (core/scripts/dump_topopt_fixtures.py →
// core/tests/fixtures/ui/topopt_*); the contract test checks this module against them.
//
// POST /topopt → 202 and a run whose variants progress one iteration batch every `stepMs`
// (topopt_progress with a preview each batch), then end as the real done record
// (topopt_finished). Cancel stops it; accept adds the new part to the parts list.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { TopoptRecord } from "../../src/api/topopt";
import type { PartSummary } from "../../src/api/types";
import { asBody, mises, HttpError, RAW, type EventIn, type Route } from "./server";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = resolve(HERE, "../../../core/tests/fixtures/ui");
const fixture = <T>(name: string): T => JSON.parse(readFileSync(resolve(FIX, name), "utf8")) as T;

export const REAL_TOPOPT = {
  start: fixture<TopoptRecord>("topopt_start.json"),
  running: fixture<TopoptRecord>("topopt_running.json"),
  done: fixture<TopoptRecord>("topopt_record.json"),
  invalid: fixture<Record<string, unknown>>("topopt_start_invalid.json"),
  cancelConflict: fixture<Record<string, unknown>>("topopt_cancel_conflict.json"),
  accept: fixture<Record<string, unknown>>("topopt_accept.json"),
};
const FILES: Record<string, [string, string]> = {
  "preview.glb": ["topopt_preview.glb", "model/gltf-binary"], "result.glb": ["topopt_result.glb", "model/gltf-binary"],
  "stress.glb": ["topopt_result.glb", "model/gltf-binary"], "render.png": ["topopt_render.png", "image/png"],
};
const ID_RE = /^to_[0-9a-f]{12}$/;
const BATCHES = [5, 10, 15, 20, 25, 30];

export interface TopoptDeps {
  parts: () => PartSummary[];
  setParts: (p: PartSummary[]) => void;
  emit: (ev: EventIn) => void;
  stepMs?: number;
}

export interface FakeTopopt { routes: Route[]; reset(): void; records: Map<string, TopoptRecord> }

export function topoptFake(d: TopoptDeps): FakeTopopt {
  const records = new Map<string, TopoptRecord>();
  const timers = new Map<string, ReturnType<typeof setTimeout>[]>();
  const stepMs = d.stepMs ?? 250;
  const get = (id: string): TopoptRecord => {
    if (!ID_RE.test(id)) throw mises("invalid_input", `bad topopt id '${id}'`);
    const r = records.get(id);
    if (!r) throw mises("not_found", `no generative design run ${id}`, "start one with topopt_run or topopt_generate");
    return r;
  };
  const progress = (rec: TopoptRecord, vid: string, it: number) => d.emit({
    type: "topopt_progress", run_id: rec.run_id ?? null,
    data: { topopt_id: rec.topopt_id, part_id: rec.part_id, project_id: rec.project_id ?? null, build_id: rec.project_id ?? null,
      stream: "sim", variant: vid, status: "running", phase: "optimize", pct: Math.round(5 + 75 * it / 60), it, max_iter: 60,
      compliance: 100 / it, vf: 0.3, preview: it, preview_url: `/topopt/${rec.topopt_id}/variants/${vid}/preview.glb?v=${it}` },
  });

  function start(body: Record<string, unknown>): TopoptRecord {
    const partId = body.part_id;
    const vf = body.volume_fraction;
    const sweep = (body.sweep as { volume_fraction?: number[] } | undefined)?.volume_fraction;
    const bad = (msg: string) => new HttpError(400, { ...REAL_TOPOPT.invalid, message: msg });
    if (typeof vf === "number" && !(vf > 0.03 && vf < 0.8)) throw new HttpError(400, structuredClone(REAL_TOPOPT.invalid));
    if (!Array.isArray(body.fixtures) || body.fixtures.length === 0) throw bad("fixtures: List should have at least 1 item");
    if (!Array.isArray(body.load_cases) || body.load_cases.length === 0) throw bad("load_cases: List should have at least 1 item");
    const part = d.parts().find((p) => p.part_id === partId);
    if (!part || !part.metrics) throw mises("not_found", `no built part '${String(partId)}'`, "create it with mirror_part_create");
    const n = Math.max(1, Math.min(6, sweep?.length ?? 1));
    const id = `to_${randomBytes(6).toString("hex")}`;
    const tmpl = REAL_TOPOPT.start.variants[0];
    const variants = Array.from({ length: n }, (_, i) => ({
      ...structuredClone(tmpl), id: `v${i}`, volume_fraction: sweep?.[i] ?? (typeof vf === "number" ? vf : 0.25),
      name: `vf ${(sweep?.[i] ?? (typeof vf === "number" ? vf : 0.25)).toFixed(2)} · r ${tmpl.filter_radius_mm.toFixed(1)} mm`,
    }));
    const rec: TopoptRecord = { ...structuredClone(REAL_TOPOPT.start), topopt_id: id, part_id: String(partId), status: "running",
      tool: n > 1 ? "topopt_generate" : "topopt_run", created_at: Date.now() / 1000, variants, accepted: [], recommended: null, pareto: [] };
    records.set(id, rec);
    const ts: ReturnType<typeof setTimeout>[] = [];
    BATCHES.forEach((it, k) => ts.push(setTimeout(() => {
      for (const v of rec.variants) {
        Object.assign(v, { status: "running", phase: "optimize", it, max_iter: 60, preview: it, pct: Math.round(5 + 75 * it / 60) });
        progress(rec, v.id, it);
      }
    }, stepMs * (k + 1))));
    ts.push(setTimeout(() => finish(rec), stepMs * (BATCHES.length + 1)));
    timers.set(id, ts);
    return rec;
  }

  function finish(rec: TopoptRecord): void {
    const done = REAL_TOPOPT.done;
    rec.variants = rec.variants.map((v, i) => {
      const real = structuredClone(done.variants[Math.min(i, done.variants.length - 1)]);
      return { ...real, id: v.id, name: v.name, volume_fraction: v.volume_fraction,
        urls: Object.fromEntries(Object.entries(real.urls ?? {}).map(([k, u]) => [k, String(u).replace(done.topopt_id, rec.topopt_id).replace(/variants\/v\d+/, `variants/${v.id}`)])) as typeof real.urls };
    });
    const best = rec.variants.find((v) => v.rank === 1) ?? rec.variants[0];
    rec.recommended = best?.id ?? null;
    rec.pareto = rec.variants.filter((v) => v.pareto).map((v) => v.id);
    rec.status = "done";
    rec.finished_at = Date.now() / 1000;
    rec.summary = `generative design ${rec.topopt_id} of ${rec.part_id}: done, ${rec.variants.length}/${rec.variants.length} variants; recommended ${rec.recommended}`;
    timers.delete(rec.topopt_id);
    d.emit({ type: "topopt_finished", run_id: rec.run_id ?? null, data: { topopt_id: rec.topopt_id, part_id: rec.part_id, stream: "sim",
      status: "done", summary: rec.summary, recommended: rec.recommended, pareto: rec.pareto, build_id: rec.project_id ?? null } });
  }

  const routes: Route[] = [
    { method: "POST", re: /^\/topopt$/, h: (_m, _q, raw, _r, res) => {
      const rec = start(asBody(raw));
      const text = JSON.stringify(rec);
      res.writeHead(202, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
      res.end(text);
      return RAW;
    } },
    { method: "GET", re: /^\/topopt$/, h: (_m, q) => {
      const part = q.get("part_id");
      return [...records.values()].filter((r) => part === null || r.part_id === part).sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
    } },
    { method: "GET", re: /^\/topopt\/([^/]+)$/, h: (m) => get(decodeURIComponent(m[1])) },
    { method: "POST", re: /^\/topopt\/([^/]+)\/cancel$/, h: (m) => {
      const rec = get(decodeURIComponent(m[1]));
      if (rec.status !== "running") throw new HttpError(409, { ...REAL_TOPOPT.cancelConflict, status: rec.status, message: `${rec.topopt_id} is not running` });
      for (const t of timers.get(rec.topopt_id) ?? []) clearTimeout(t);
      timers.delete(rec.topopt_id);
      rec.status = "cancelled";
      for (const v of rec.variants) Object.assign(v, { status: "cancelled", phase: "cancelled" });
      d.emit({ type: "topopt_finished", run_id: rec.run_id ?? null, data: { topopt_id: rec.topopt_id, part_id: rec.part_id, stream: "sim", status: "cancelled", summary: "cancelled" } });
      return { topopt_id: rec.topopt_id, status: "cancelling" };
    } },
    { method: "POST", re: /^\/topopt\/([^/]+)\/accept$/, h: (m, _q, raw) => {
      const rec = get(decodeURIComponent(m[1]));
      const body = asBody(raw);
      const vid = typeof body.variant === "string" ? body.variant : rec.recommended;
      const v = rec.variants.find((x) => x.id === vid);
      if (!v || v.status !== "done") throw mises("invalid_input", `variant ${String(vid)} is not done`);
      const src = d.parts().find((p) => p.part_id === rec.part_id);
      let pid = typeof body.new_part_id === "string" ? body.new_part_id : `${rec.part_id}_generative`;
      for (const c of "bcdefgh") if (d.parts().some((p) => p.part_id === pid)) pid = `${rec.part_id}_generative_${c}`;
      if (src) d.setParts([...d.parts(), { ...structuredClone(src), part_id: pid, mtime: Date.now() / 1000 }]);
      rec.accepted.push({ variant: v.id, part_id: pid, mode: "new", version: 1, at: Date.now() / 1000 });
      d.emit({ type: "part_changed", run_id: null, data: { part_id: pid, version: 1, by: "user", run_id: null, summary: `created ${pid} from generative design` } });
      return { ...structuredClone(REAL_TOPOPT.accept), part_id: pid, version: 1, variant: v.id, mode: "new", topopt_id: rec.topopt_id,
        summary: `accepted ${v.id} (${v.name}) of ${rec.topopt_id} as ${pid} v1` };
    } },
    { method: "GET", re: /^\/topopt\/([^/]+)\/variants\/(v\d{1,2})\/([a-z]+\.[a-z]+)$/, h: (m, _q, _b, _r, res) => {
      get(decodeURIComponent(m[1]));
      const f = FILES[m[3]];
      if (!f) throw mises("not_found", `no file '${m[3]}'`);
      const data = readFileSync(resolve(FIX, f[0]));
      res.writeHead(200, { "Content-Type": f[1], "Content-Length": data.length, ETag: `"${data.length}"` });
      res.end(data);
      return RAW;
    } },
  ];

  return {
    routes, records,
    reset() { for (const ts of timers.values()) ts.forEach(clearTimeout); timers.clear(); records.clear(); },
  };
}
