import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  agentInfoSchema, agentRunSchema, approvalSchema, coreStatusSchema, eventSchema, localPullSchema, localStatusSchema, modelsInfoSchema,
  partSummarySchema, profileSchema, stepSchema,
} from "./schemas";
import type { ZodType } from "zod";

// The core's real fixtures (Task 1) live in ../core/tests/fixtures/ui/. A copy is kept in
// test/fixtures/core-ui/ so the contract is checked even on a branch without Task 1's files.
const CORE = resolve(__dirname, "../../../core/tests/fixtures/ui");
const LOCAL = resolve(__dirname, "../../test/fixtures/core-ui");

const arrays: [string, ZodType][] = [
  ["runs.json", agentRunSchema], ["steps.json", stepSchema], ["approvals.json", approvalSchema],
  ["parts.json", partSummarySchema], ["agents.json", agentInfoSchema],
];
const singles: [string, ZodType][] = [["models.json", modelsInfoSchema], ["status.json", coreStatusSchema]];

function check(dir: string) {
  for (const [file, schema] of arrays) {
    it(`${file} parses`, () => {
      const data = JSON.parse(readFileSync(resolve(dir, file), "utf8")) as unknown[];
      expect(data.length).toBeGreaterThan(0);
      for (const item of data) expect(schema.parse(item)).toEqual(item);
    });
  }
  for (const [file, schema] of singles) {
    it(`${file} parses`, () => {
      const data: unknown = JSON.parse(readFileSync(resolve(dir, file), "utf8"));
      expect(schema.parse(data)).toEqual(data);
    });
  }
  it("events.jsonl parses line by line", () => {
    const lines = readFileSync(resolve(dir, "events.jsonl"), "utf8").split("\n").filter(Boolean);
    expect(lines.length).toBeGreaterThan(0);
    for (const l of lines) {
      const ev: unknown = JSON.parse(l);
      expect(eventSchema.parse(ev)).toEqual(ev);
    }
  });
  it("approval_requested event data is a full Approval", () => {
    const lines = readFileSync(resolve(dir, "events.jsonl"), "utf8").split("\n").filter(Boolean);
    const ev = lines.map((l) => eventSchema.parse(JSON.parse(l))).find((e) => e.type === "approval_requested");
    expect(ev).toBeDefined();
    expect(() => approvalSchema.parse(ev?.data)).not.toThrow();
  });
}

describe("schemas vs local fixture copy (test/fixtures/core-ui)", () => check(LOCAL));

const haveCore = existsSync(resolve(CORE, "runs.json"));
describe.skipIf(!haveCore)(
  haveCore ? "schemas vs core fixtures" : "schemas vs core fixtures — SKIPPED: ../core/tests/fixtures/ui not present (Task 1 not merged)",
  () => check(CORE),
);

describe("schema strictness", () => {
  it("rejects a run with an unknown status", () => {
    const run = JSON.parse(readFileSync(resolve(LOCAL, "runs.json"), "utf8"))[0];
    expect(() => agentRunSchema.parse({ ...run, status: "paused" })).toThrow();
  });
  it("rejects a step with an unknown kind", () => {
    const step = JSON.parse(readFileSync(resolve(LOCAL, "steps.json"), "utf8"))[0];
    expect(() => stepSchema.parse({ ...step, kind: "banana" })).toThrow();
  });
});

// ---- Plan 4 Task 5: optional fields from the core's model, local-model and parallelism work
describe("plan 4 additions (all optional: older cores still parse)", () => {
  it("models_vendor_gains_recommended_note_free_hint", () => {
    const m = modelsInfoSchema.parse({
      default: "anthropic/claude-sonnet-5-5",
      vendors: [
        { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"], recommended: "best", note: "your plan, best at CAD", free: false, hint: null },
        { vendor: "openrouter", key: "none", models: ["openrouter/qwen/qwen3-coder:free"], free: true, recommended: "free" },
      ],
    });
    expect(m.vendors[0]).toMatchObject({ recommended: "best", note: "your plan, best at CAD", free: false });
    expect(m.vendors[1]).toMatchObject({ free: true, recommended: "free" });
  });

  it("models_entries_may_be_objects_with_their_own_badges", () => {
    const m = modelsInfoSchema.parse({
      default: "a/b",
      vendors: [{ vendor: "anthropic", key: "keychain", models: ["claude-sonnet-5-5", { id: "claude-opus-5-5", recommended: "best", note: "strongest" }] }],
    });
    expect(m.vendors[0].models).toEqual(["claude-sonnet-5-5", "claude-opus-5-5"]);
    expect(m.vendors[0].model_meta).toEqual({ "claude-opus-5-5": { recommended: "best", note: "strongest", free: false } });
  });

  it("an_unknown_recommended_value_is_dropped_not_fatal", () => {
    const m = modelsInfoSchema.parse({ default: "a/b", vendors: [{ vendor: "x", key: "none", recommended: "shiny" }] });
    expect(m.vendors[0].recommended).toBeNull();
  });

  it("string_only_vendors_parse_unchanged", () => {
    const data = { default: "a/b", vendors: [{ vendor: "claude-code", key: "signed_in", models: ["sonnet"] }] };
    expect(modelsInfoSchema.parse(data)).toEqual(data);
  });

  it("run_status_queued_parses", () => {
    const run = JSON.parse(readFileSync(resolve(LOCAL, "runs.json"), "utf8"))[0];
    expect(agentRunSchema.parse({ ...run, status: "queued" }).status).toBe("queued");
  });

  it("status_parallel_parses_and_is_optional", () => {
    const st = JSON.parse(readFileSync(resolve(LOCAL, "status.json"), "utf8"));
    expect(coreStatusSchema.parse(st).parallel).toBeUndefined();
    const p = coreStatusSchema.parse({ ...st, parallel: { limit: 3, auto: true, reason: "M5 Pro · 24 GB · cloud model → 3" } });
    expect(p.parallel).toEqual({ limit: 3, auto: true, reason: "M5 Pro · 24 GB · cloud model → 3" });
  });

  it("profile_parallel_auto_and_theme", () => {
    const p = profileSchema.parse({ max_parallel_agents: "auto", theme: "dark" });
    expect(p.max_parallel_agents).toBe("auto");
    expect(p.theme).toBe("dark");
    expect(profileSchema.parse({ max_parallel_agents: 3 }).max_parallel_agents).toBe(3);
  });

  it("local_status_parses", () => {
    const s = localStatusSchema.parse({
      installed: true, running: false, version: "0.12.3", models: [{ name: "gpt-oss:20b", size_gb: 13.8 }],
      recommended: [{ name: "qwen3-coder:30b", size_gb: 19, fits: true, note: "best local coder for CAD scripts" }],
    });
    expect(s.recommended[0].fits).toBe(true);
    expect(localStatusSchema.parse({ installed: false, running: false }).models).toEqual([]);
  });

  it("local_pull_event_data_parses", () => {
    expect(localPullSchema.parse({ model: "qwen3-coder:30b", completed: 5, total: 10, status: "downloading" }).completed).toBe(5);
  });
});
