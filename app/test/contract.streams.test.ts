// @vitest-environment node
// Plan 9 D4 (B3): the real core tags every event and step with its stream (core
// scripts/dump_streams_fixtures.py → core/tests/fixtures/ui/p9): they parse with the app's
// schemas, the fake core's streamOf gives the same stream for every real fixture, and an agent's
// study comes back at once (status running) with sim_finished and the lead's note after it.
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { eventSchema, stepSchema } from "../src/api/schemas";
import type { MisesEvent, Step } from "../src/api/types";
import { streamOf } from "./fake-core/streams";

const UI = resolve(import.meta.dirname, "../../core/tests/fixtures/ui");
const DIR = join(UI, "p9");
const lines = (p: string): MisesEvent[] => readFileSync(p, "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
const steps = (p: string): Step[] => JSON.parse(readFileSync(p, "utf8")) as Step[];

describe("p9 streams fixtures", () => {
  it("every event and step parses and carries a stream", () => {
    for (const e of lines(join(DIR, "streams_events.jsonl"))) expect(eventSchema.parse(e).stream).toMatch(/^(text|sim|view)$/);
    for (const s of steps(join(DIR, "streams_steps.json"))) expect(stepSchema.parse(s).stream).toMatch(/^(text|sim|view)$/);
  });

  it("the fake core's streamOf matches the real core on every fixture event and step", () => {
    const files = [...readdirSync(UI).map((f) => join(UI, f)), ...readdirSync(DIR).map((f) => join(DIR, f)),
      ...readdirSync(join(UI, "plan7")).map((f) => join(UI, "plan7", f))];
    let n = 0;
    for (const f of files) {
      if (f.endsWith(".jsonl")) {
        for (const e of lines(f)) if (e.stream) { expect(streamOf(e.type, e.data), `${f}: ${e.type}`).toBe(e.stream); n++; }
      } else if (f.endsWith("steps.json")) {
        for (const s of steps(f)) if (s.stream) { expect(streamOf(s.kind, s.payload), `${f}: ${s.kind}`).toBe(s.stream); n++; }
      }
    }
    expect(n).toBeGreaterThan(200);
  });

  it("an agent's study returns running at once; the render and the reply come before sim_finished", () => {
    const evs = lines(join(DIR, "streams_events.jsonl"));
    const started = evs.find((e) => e.type === "tool_result" && e.data.name === "sim_run");
    expect(started?.stream).toBe("sim");
    const render = evs.findIndex((e) => e.type === "tool_result" && e.data.name === "render_views");
    const finished = evs.findIndex((e) => e.type === "sim_finished");
    const firstTurnEnd = evs.findIndex((e) => e.type === "run_finished");
    expect(evs[render].stream).toBe("view");
    expect(render).toBeLessThan(finished);
    expect(firstTurnEnd).toBeLessThan(finished);
    const fin = evs[finished];
    expect(fin.stream).toBe("sim");
    expect(Object.keys(fin.data).sort()).toEqual(["build_id", "part_id", "run_id", "sim_id", "status", "summary", "type"]);
    const st = steps(join(DIR, "streams_steps.json"));
    const out = st.find((s) => s.kind === "tool_result" && s.payload.name === "sim_run")?.payload.output as { data: Record<string, unknown> };
    expect(out.data).toMatchObject({ status: "running", stream: "sim", sim_id: fin.data.sim_id });
    const note = st.find((s) => s.kind === "message" && s.payload.system_note);
    expect(note?.payload.system_note).toEqual({ kind: "sim_finished", sim_id: fin.data.sim_id, status: "done" });
    expect(note?.stream).toBe("text");
  });
});
