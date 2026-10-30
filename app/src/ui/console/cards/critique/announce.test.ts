import { describe, expect, it } from "vitest";
import type { Step } from "../../../../api/types";
import { buildThread } from "../../threadModel";
import { buildLog } from "../../logModel";
import { input, run } from "../../testSteps";
import { g4Announcement, imageRef } from "./announce";
import { modelConcern, shapeComplexity, shapeWarning } from "./shapeWarning";
import { modesForSpec, parseViewSpec, partsForSpec } from "../../../viewer/modes/viewSpec";

let id = 0;
const step = (payload: Record<string, unknown>, kind: Step["kind"] = "decision"): Step =>
  ({ id: ++id, run_id: "r1", ts: 1000 + id, kind, payload, evidence_hash: "", prev_hash: null, collapsed: false });
const announce = (kind: string, ref: Record<string, unknown>, title = "t") => step({ agent_announce: { run_id: "r1", kind, title, detail: "", ref } });

describe("g4 announcements", () => {
  it("reads a critique round leniently and keeps model text as plain data", () => {
    const a = g4Announcement(announce("critique_round", {
      round: 2, max_rounds: 3, score: 0.86, threshold: 0.8, stop_reason: "passed",
      before_image_id: "r_abc", after: { image_id: "r_def" },
      issues_fixed: [{ view: "top", issue: "straight trailing edge", severity: "high", suggested_fix: "sawtooth" }, "bare string issue", { issue: "" }],
      issues_open: [{ issue: "<script>x</script>", severity: "bogus" }],
      iou: { front: 0.91, top: 1.4, side: "x" },
    }));
    expect(a?.kind).toBe("critique_round");
    if (a?.kind !== "critique_round") return;
    const r = a.round;
    expect(r).toMatchObject({ round: 2, maxRounds: 3, score: 0.86, threshold: 0.8, stopReason: "passed", before: { id: "r_abc" }, after: { id: "r_def" } });
    expect(r.fixed.map((i) => i.issue)).toEqual(["straight trailing edge", "bare string issue"]);
    expect(r.open[0]).toEqual({ view: null, issue: "<script>x</script>", severity: null, fix: null });
    expect(r.iou).toEqual([{ view: "front", value: 0.91 }, { view: "top", value: 1 }]);
  });

  it("only accepts render ids and inline raster data URLs as images", () => {
    expect(imageRef("r_1")).toEqual({ id: "r_1" });
    expect(imageRef("data:image/png;base64,iVBORw0KGgo=")).toEqual({ dataUrl: "data:image/png;base64,iVBORw0KGgo=" });
    expect(imageRef("data:image/svg+xml;base64,PHN2Zz4=")).toBeNull();
    expect(imageRef("../../etc/passwd")).toBeNull();
    expect(imageRef("javascript:alert(1)")).toBeNull();
  });

  it("reads a presented view; anything malformed is dropped", () => {
    const a = g4Announcement(announce("view_presented", { view_spec: { kind: "section", target: { part_id: "pi_case" }, section: { origin: [0, 5, 0], normal: [0, 2, 0] }, caption: "  the  wall  " } }));
    expect(a?.kind).toBe("view_presented");
    if (a?.kind !== "view_presented") return;
    expect(a.view.spec.caption).toBe("the wall");
    expect(modesForSpec(a.view.spec).section).toEqual({ on: true, normal: [0, 2, 0], at: 5 });
    expect(g4Announcement(announce("view_presented", { view_spec: { kind: "teleport" } }))).toBeNull();
    expect(g4Announcement(announce("part_created", { part_id: "x" }))).toBeNull();
    expect(parseViewSpec({ kind: "orbit", target: "x", parts: ["ok_1", "../bad", 3] })?.parts).toEqual(["ok_1"]);
  });

  it("maps each kind to modes", () => {
    const spec = (s: Record<string, unknown>) => parseViewSpec({ target: { project_id: "b1" }, ...s })!;
    expect(modesForSpec(spec({ kind: "internal" })).xray).toEqual({ opacity: 0.25, parts: null });
    expect(modesForSpec(spec({ kind: "internal", xray_parts: ["pi_case"] })).xray.parts).toEqual(["pi_case"]);
    expect(modesForSpec(spec({ kind: "exploded" })).explode).toBe(1);
    expect(modesForSpec(spec({ kind: "exploded", explode: 0.4 })).explode).toBe(0.4);
    expect(modesForSpec(spec({ kind: "subset", parts: ["pi_fan"] })).isolate).toEqual(["pi_fan"]);
    expect(spec({ kind: "orbit" }).orbit).toEqual({ duration_s: 12, turns: 1 });
    const all = [{ part_id: "a", project_id: "b1", vars: {}, metrics: {} as never, mtime: 1 }, { part_id: "b", project_id: "b1", vars: {}, metrics: {} as never, mtime: 1 }, { part_id: "c", project_id: "b2", vars: {}, metrics: {} as never, mtime: 1 }];
    expect(partsForSpec(spec({ kind: "subset", parts: ["b"] }), all)).toEqual(["a", "b"]);
    expect(partsForSpec(spec({ kind: "orbit" }), all)).toEqual(["a", "b"]);
    expect(partsForSpec(parseViewSpec({ kind: "orbit", target: { part_id: "c" } })!, all)).toEqual(["c"]);
  });

  it("threads: cards in place of notes; the views of one reply form one set", () => {
    const view = (k: string) => announce("view_presented", { view_spec: { kind: k, target: { part_id: "pi_case" } } });
    const steps = [
      step({ role: "user", text: "make a case" }, "message"),
      view("orbit"), view("section"),
      step({ role: "assistant", text: "Here it is." }, "message"),
      view("exploded"),
      announce("critique_round", { round: 1 }),
      step({ role: "user", text: "again" }, "message"),
      view("subset"),
    ];
    const t = buildThread(buildLog(input({ stepList: steps, selectedRunId: "r1", runs: [run("r1")], filter: { scope: "run", kind: "everything" } })));
    const views = t.filter((i) => i.kind === "view");
    expect(views).toHaveLength(4);
    expect(t.some((i) => i.kind === "critique")).toBe(true);
    expect(t.some((i) => i.kind === "note")).toBe(false);
    if (views[0].kind !== "view" || views[3].kind !== "view") return;
    expect(views[0].group.map((v) => v.spec.kind)).toEqual(["orbit", "section", "exploded"]);
    expect(views[3].group.map((v) => v.spec.kind)).toEqual(["subset"]);
  });
});

describe("model warning", () => {
  it("reads shape complexity in any of its forms", () => {
    expect(shapeComplexity(0.8)?.level).toBe("high");
    expect(shapeComplexity(4)?.level).toBe("medium");
    expect(shapeComplexity("low")?.level).toBe("low");
    expect(shapeComplexity({ score: 0.7, keywords: ["aircraft", 3] })).toEqual({ level: "high", score: 0.7, keywords: ["aircraft"] });
    expect(shapeComplexity({ nope: 1 })).toBeNull();
    expect(shapeComplexity(undefined)).toBeNull();
  });

  it("knows small local and non-vision models", () => {
    expect(modelConcern({ provider: "claude-code", model: "opus" })).toBeNull();
    expect(modelConcern({ provider: "anthropic", model: "claude-sonnet-5-5" })).toBeNull();
    expect(modelConcern({ provider: "ollama", model: "qwen3:8b" })).toBe("small-local");
    expect(modelConcern({ provider: "ollama", model: "llama3.3:70b" })).toBe("no-vision");
    expect(modelConcern({ provider: "openrouter", model: "deepseek/deepseek-chat" })).toBe("no-vision");
    expect(modelConcern({ provider: "openai", model: "o3-mini" })).toBe("no-vision");
  });

  it("warns only for shape-heavy work on such a model", () => {
    expect(shapeWarning({ provider: "ollama", model: "qwen3:8b", shape_complexity: { level: "high", keywords: ["aircraft"] } })?.detail).toContain("aircraft");
    expect(shapeWarning({ provider: "ollama", model: "qwen3:8b", shape_complexity: "low" })).toBeNull();
    expect(shapeWarning({ provider: "ollama", model: "qwen3:8b" })).toBeNull();
    expect(shapeWarning({ provider: "claude-code", model: "opus", shape_complexity: 0.9 })).toBeNull();
  });
});
