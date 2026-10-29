import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import type { PartEditResult, PartSource } from "../../api/builds";
import { partSourceSchema } from "../../api/builds";
import { CoreError } from "../../api/client";
import { SourceSession } from "./sourceEdit";

const FX = resolve(import.meta.dirname, "../../../test/fixtures/core-ui");
const fx = <T,>(n: string): T => JSON.parse(readFileSync(resolve(FX, n), "utf8")) as T;
const SRC = partSourceSchema.parse(fx("part_source.json"));

function session(opts: { reads?: PartSource[]; put?: (src: string, base: number) => Promise<PartEditResult> }) {
  const reads = [...(opts.reads ?? [SRC])];
  const calls: string[] = [];
  const partSource = vi.fn(async () => { calls.push("GET"); return reads.length > 1 ? reads.shift() as PartSource : reads[0]; });
  const putPartSource = vi.fn(async (_id: string, src: string, base: number) => { calls.push(`PUT@${base}`); return (opts.put ?? (async (_s, b) => ({ part_id: "quad_arm", version: b + 1, vars: {} })))(src, base); });
  const saved: PartEditResult[] = [];
  const s = new SourceSession("quad_arm", { client: () => ({ partSource, putPartSource }), onSaved: (r) => saved.push(r) });
  return { s, calls, saved, putPartSource };
}

describe("SourceSession", () => {
  it("opens with a fresh read and saves against the version it read", async () => {
    const h = session({ reads: [SRC, { ...SRC, version: SRC.version + 1, source: SRC.source + "\n# refreshed" }] });
    await h.s.open();
    expect(h.s.base).toBe(SRC.version);
    h.s.edit(SRC.source.replace("16, ", "18, "));
    expect(h.s.dirty).toBe(true);
    await h.s.save();
    expect(h.calls).toEqual(["GET", `PUT@${SRC.version}`, "GET"]);
    expect(h.saved[0].version).toBe(SRC.version + 1);
    expect(h.s.status.kind).toBe("saved");
    expect(h.s.dirty).toBe(false);
    expect(h.s.text).toContain("# refreshed");       // the core's rewrite after the build
  });

  it("a stale save is a conflict; keep mine re-reads first, then writes on the new version", async () => {
    const stale = fx<{ body: Record<string, unknown> }>("part_source_stale.json");
    let n = 0;
    const h = session({
      reads: [SRC, { ...SRC, version: 5, source: "agent's script" }],
      put: async (_s, base) => {
        if (n++ === 0) throw new CoreError("stale_version", String(stale.body.message), { status: 409, body: stale.body });
        return { part_id: "quad_arm", version: base + 1, vars: {} };
      },
    });
    await h.s.open();
    h.s.edit("mine");
    await h.s.save();
    expect(h.s.status).toEqual({ kind: "conflict", current: 5 });
    expect(h.s.text).toBe("mine");
    await h.s.keepMine();
    expect(h.calls).toEqual(["GET", `PUT@${SRC.version}`, "GET", "PUT@5", "GET"]);
    expect(h.putPartSource).toHaveBeenLastCalledWith("quad_arm", "mine", 5);
  });

  it("load theirs drops my text", async () => {
    const h = session({ reads: [SRC, { ...SRC, version: 9, source: "theirs" }] });
    await h.s.open();
    h.s.edit("mine");
    await h.s.loadTheirs();
    expect(h.s.text).toBe("theirs");
    expect(h.s.dirty).toBe(false);
  });

  it("a build failure keeps my text and shows the op's error line", async () => {
    const fail = fx<{ body: { message: string } }>("part_source_rematch_fail.json");
    const h = session({ put: async () => { throw new CoreError("tool_failed", fail.body.message, { status: 422 }); } });
    await h.s.open();
    h.s.edit("broken");
    await h.s.save();
    expect(h.s.status.kind).toBe("error");
    expect(h.s.status.kind === "error" && h.s.status.message).toMatch(/nothing changed: mises-op 0 hole: target face not found/);
    expect(h.s.text).toBe("broken");
  });

  it("an outside change reloads a clean editor, and warns a dirty one", async () => {
    const h = session({ reads: [SRC, { ...SRC, version: 7, source: "new" }] });
    await h.s.open();
    h.s.external(7);
    await new Promise((r) => setTimeout(r, 0));
    expect(h.s.text).toBe("new");
    h.s.edit("mine");
    h.s.external(8);
    expect(h.s.status).toEqual({ kind: "behind", current: 8 });
  });
});
