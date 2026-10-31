// Fake core: GET /studies/context (Plan 9 U-D; core server/studies_context_api.py,
// sim/studies_agent.py) with the real core's shapes from
// core/scripts/dump_studies_context_fixtures.py: studies_context.json (the rule proposals) and
// studies_context_agent.json (after the studies agent's `studies_propose`). The fixture build's
// parts are hip_bracket / leg_front; here each proposal moves onto the scenario's built parts in
// order (its real settings kept), so its Run starts a study the fake's POST /sims can play.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { StudyRecord } from "../../src/api/studies";
import type { PartSummary } from "../../src/api/types";
import { mises, type Route } from "./server";

const DIR = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const json = (n: string): Record<string, unknown> => JSON.parse(readFileSync(resolve(DIR, n), "utf8")) as Record<string, unknown>;
export const REAL_STUDIES_CONTEXT = { rules: json("studies_context.json"), agent: json("studies_context_agent.json") };

export type StudiesAgentMode = "agent" | "rules" | "none";

export interface StudiesAgentDeps {
  parts(): readonly PartSummary[];
  liveBuild(id: string): boolean;
  buildName(id: string): string | null;
  mode(): StudiesAgentMode;
  /** The fake's stored studies (GET /sims): the context's past studies are the same ones. */
  records(): Iterable<StudyRecord>;
}

/** A study as the core's context lists it (studies_agent._study_row). */
function studyRow(r: StudyRecord): Record<string, unknown> {
  const card = r.card;
  return {
    sim_id: r.sim_id, part_id: r.part_id, project_id: null, type: r.type ?? "static", status: r.status, stale: !!r.stale,
    created_at: r.created_at, reason: r.reason ?? null, verdict: card?.verdict ?? null, fidelity_note: card?.fidelity_note ?? null,
    material_basis: (card as Record<string, unknown> | undefined)?.material_basis ?? null,
    metrics: (card?.metrics ?? []).filter((m) => m.value !== null && m.value !== undefined), checks: card?.checks ?? [],
    warnings: (r.warnings ?? []).slice(0, 3), summary: `GUIDE ONLY - ${r.type ?? "static"} study of ${r.part_id} (fake core)`,
  };
}

/** The fixture's own part names, as its why texts spell them. */
const FIXTURE_NAMES = /hip bracket|front leg|leg front|hip_bracket|leg_front/gi;

export function studiesContextFor(d: StudiesAgentDeps, projectId: string): Record<string, unknown> {
  const mode = d.mode();
  const src = structuredClone(REAL_STUDIES_CONTEXT[mode === "agent" ? "agent" : "rules"]);
  const built = d.parts().filter((p) => p.metrics !== null && (p.kind ?? "part") === "part" && (p.project_id ?? projectId) === projectId);
  const label = (p: PartSummary) => p.label ?? p.part_id.replace(/_/g, " ");
  const proposals = mode === "none" ? [] : (src.proposals as Record<string, unknown>[]).flatMap((p, i) => {
    const part = built[i % Math.max(1, built.length)];
    if (!part) return [];
    const name = label(part);
    return [{ ...p, part_id: part.part_id, title: String(p.title).replace(FIXTURE_NAMES, name), why: String(p.why).replace(FIXTURE_NAMES, name) }];
  });
  const parts = d.parts().filter((p) => (p.project_id ?? projectId) === projectId).map((p) => ({
    part_id: p.part_id, label: label(p), kind: p.kind ?? "part", parent_id: p.parent_id ?? null, built: p.metrics !== null,
    material: "PLA", material_source: "default", mass_g: p.metrics?.mass_g ?? null, mass_source: p.metrics ? "built" : null,
    mass_note: p.metrics ? null : "not built yet", volume_mm3: p.metrics?.volume_mm3 ?? null,
    bbox_mm: p.metrics ? [...p.metrics.bbox.size] : null, faces: p.metrics?.faces ?? null, holes: p.metrics?.holes.length ?? 0,
  }));
  const ids = new Set(parts.map((p) => p.part_id));
  const rows = [...d.records()].filter((r) => ids.has(r.part_id)).sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0)).map(studyRow);
  return {
    ...src, project_id: projectId, build_name: d.buildName(projectId), parts, studies: rows.slice(0, 40), studies_total: rows.length, proposals,
    proposals_source: mode === "agent" ? "agent" : "rules", proposals_run_id: mode === "agent" ? src.proposals_run_id ?? null : null,
  };
}

export function studiesAgentRoutes(d: StudiesAgentDeps): Route[] {
  return [{
    method: "GET", re: /^\/studies\/context$/, h: (_m, q) => {
      const pid = q.get("project_id");
      if (!pid || !/^[A-Za-z0-9_-]{1,64}$/.test(pid)) throw mises("invalid_input", `bad project id: ${JSON.stringify(pid)}`);
      if (!d.liveBuild(pid)) throw mises("not_found", `no build ${pid}`);
      return studiesContextFor(d, pid);
    },
  }];
}
