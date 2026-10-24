import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  agentInfoSchema, agentRunSchema, approvalSchema, coreStatusSchema, eventSchema, modelsInfoSchema,
  partSummarySchema, stepSchema,
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
