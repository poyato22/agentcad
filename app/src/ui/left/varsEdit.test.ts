import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { CoreError } from "../../api/client";
import type { PartEditResult } from "../../api/builds";
import { VarsEditor, type SavedEdit } from "./varsEdit";

const FX = resolve(import.meta.dirname, "../../../test/fixtures/core-ui");
const fx = <T,>(n: string): T => JSON.parse(readFileSync(resolve(FX, n), "utf8")) as T;

function harness(answer: (vars: Record<string, number>, base: number) => Promise<PartEditResult>) {
  const timers: (() => void)[] = [];
  const calls: { vars: Record<string, number>; base: number }[] = [];
  const saved: SavedEdit[] = [];
  const ed = new VarsEditor("quad_arm", {
    client: () => ({ setPartVars: (_id, vars, base) => { calls.push({ vars, base }); return answer(vars, base); } }),
    onSaved: (e) => saved.push(e),
    setTimer: (cb) => { timers.push(cb); return timers.length; },
    clearTimer: (t) => { timers[(t as number) - 1] = () => {}; },
  });
  const fire = async () => { const all = timers.splice(0); for (const t of all) t(); await new Promise((r) => setTimeout(r, 0)); };
  return { ed, calls, saved, fire, timers };
}

const ok = (vars: Record<string, number>, version: number): PartEditResult => ({ part_id: "quad_arm", version, vars });

describe("VarsEditor", () => {
  it("debounces a drag into one POST /vars with the version the user started from", async () => {
    const h = harness(async (vars, base) => ok({ length: 60, ...vars }, base + 1));
    h.ed.set("thickness", 4.5, 1, { thickness: 4, length: 60 });
    h.ed.set("thickness", 5, 1, { thickness: 4, length: 60 });
    expect(h.calls).toEqual([]);
    expect(h.ed.status.kind).toBe("pending");
    await h.fire();
    expect(h.calls).toEqual([{ vars: { thickness: 5 }, base: 1 }]);
    expect(h.ed.status.kind).toBe("idle");
    expect(h.saved[0].prev).toEqual({ thickness: 4 });
    expect(h.ed.shown("thickness", 1, 4)).toBe(5);       // the list hasn't caught up yet
    expect(h.ed.shown("thickness", 2, 5)).toBe(5);
  });

  it("an agent edit during the drag doesn't move the base: the 409 shows instead of overwriting", async () => {
    const stale = fx<{ status: number; body: Record<string, unknown> }>("part_vars_stale.json");
    const h = harness(async () => { throw new CoreError("stale_version", String(stale.body.message), { status: 409, body: stale.body }); });
    h.ed.set("thickness", 6, 1, { thickness: 4, length: 60 });
    h.ed.set("thickness", 6.5, 2, { thickness: 5, length: 60 }); // agent's v2 arrived meanwhile
    await h.fire();
    expect(h.calls[0].base).toBe(1);
    expect(h.ed.status).toEqual({ kind: "conflict", current: 2, theirs: { thickness: 5, length: 60 } });
    expect(h.ed.drafts.get("thickness")).toBe(6.5);
  });

  it("Keep mine re-sends the user's values on the current version; Reload drops them", async () => {
    let n = 0;
    const h = harness(async (vars, base) => {
      if (n++ === 0) throw new CoreError("stale_version", "conflict", { status: 409, body: { current_version: 3, vars: { thickness: 5 } } });
      return ok(vars, base + 1);
    });
    h.ed.set("thickness", 7, 1, { thickness: 4 });
    await h.fire();
    await h.ed.keepMine();
    expect(h.calls.map((c) => c.base)).toEqual([1, 3]);
    expect(h.ed.status.kind).toBe("idle");

    const h2 = harness(async () => { throw new CoreError("stale_version", "conflict", { status: 409, body: { current_version: 2 } }); });
    h2.ed.set("thickness", 7, 1, { thickness: 4 });
    await h2.fire();
    h2.ed.discard();
    expect(h2.ed.drafts.size).toBe(0);
    expect(h2.ed.status.kind).toBe("idle");
  });

  it("a failed rebuild shows the op's own error line, and the values stay for a retry", async () => {
    const fail = fx<{ body: { message: string } }>("part_source_rematch_fail.json");
    const h = harness(async () => { throw new CoreError("tool_failed", fail.body.message, { status: 422 }); });
    h.ed.set("length", 20, 1, { length: 60 });
    await h.fire();
    expect(h.ed.status.kind).toBe("error");
    expect(h.ed.status.kind === "error" && h.ed.status.message).toMatch(/^The part didn't rebuild: mises-op 0 hole: target face not found/);
    expect(h.ed.drafts.get("length")).toBe(20);
  });

  it("changes made while a request is in flight go out next, on the version it returned", async () => {
    let release: (() => void) | null = null;
    const h = harness((vars, base) => new Promise((r) => { release = () => r(ok(vars, base + 1)); }));
    h.ed.set("thickness", 5, 1, { thickness: 4 });
    await h.fire();
    h.ed.set("thickness", 6, 1, { thickness: 4 });
    await h.fire();                         // in flight: no second request yet
    expect(h.calls.length).toBe(1);
    (release as unknown as () => void)();
    await new Promise((res) => setTimeout(res, 0));
    expect(h.calls.map((c) => [c.vars.thickness, c.base])).toEqual([[5, 1], [6, 2]]);
    (release as unknown as () => void)();   // the second answer
    await new Promise((res) => setTimeout(res, 0));
    expect(h.ed.status.kind).toBe("idle");
    expect(h.saved[1].prev).toEqual({ thickness: 5 });
  });
});
