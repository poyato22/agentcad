import { describe, expect, it, vi } from "vitest";
import { CoreError } from "../../api/client";
import type { PartScene } from "./loadPart";
import { PartLoader } from "./partLoader";

const scene = (): PartScene => ({ object: {} as PartScene["object"], bbox: {} as PartScene["bbox"], dispose: vi.fn() });
const bytes = new ArrayBuffer(8);

describe("PartLoader", () => {
  it("reload_only_on_mtime_change", async () => {
    const partGlb = vi.fn().mockResolvedValue({ bytes, etag: '"a"' });
    const parse = vi.fn(async () => scene());
    const l = new PartLoader({ partGlb }, parse);
    expect((await l.load("mount_plate", 1)).status).toBe("loaded");
    expect((await l.load("mount_plate", 1)).status).toBe("unchanged");
    expect(partGlb).toHaveBeenCalledTimes(1);
    partGlb.mockResolvedValueOnce("not-modified"); // mtime changed, 304 keeps the model
    expect((await l.load("mount_plate", 2)).status).toBe("unchanged");
    expect(partGlb).toHaveBeenLastCalledWith("mount_plate", '"a"');
    expect(parse).toHaveBeenCalledTimes(1);
    partGlb.mockResolvedValueOnce({ bytes, etag: '"b"' });
    expect((await l.load("mount_plate", 3)).status).toBe("loaded");
    expect(parse).toHaveBeenCalledTimes(2);
  });

  it("resolves_the_client_per_fetch_so_a_rotated_token_is_used", async () => {
    // the session rebinds a new CoreClient after a token rotation: the loader must not keep the first one
    const oldGlb = vi.fn().mockResolvedValue({ bytes, etag: '"a"' });
    const newGlb = vi.fn().mockResolvedValue({ bytes, etag: '"b"' });
    let current: { partGlb: typeof oldGlb } = { partGlb: oldGlb };
    const l = new PartLoader(() => current, async () => scene());
    await l.load("p", 1);
    current = { partGlb: newGlb };
    expect((await l.load("p", 2)).status).toBe("loaded");
    expect(oldGlb).toHaveBeenCalledTimes(1);
    expect(newGlb).toHaveBeenCalledTimes(1);
  });

  it("does not send an etag for a different part", async () => {
    const partGlb = vi.fn().mockResolvedValue({ bytes, etag: '"a"' });
    const l = new PartLoader({ partGlb }, async () => scene());
    await l.load("p1", 1);
    await l.load("p2", 1);
    expect(partGlb).toHaveBeenLastCalledWith("p2");
  });

  it("falls back to a plain GET when the conditional request fails at the network level", async () => {
    const partGlb = vi.fn().mockResolvedValueOnce({ bytes, etag: '"a"' })
      .mockRejectedValueOnce(new CoreError("network", "Failed to fetch", { retryable: true }))
      .mockResolvedValueOnce({ bytes, etag: '"b"' });
    const l = new PartLoader({ partGlb }, async () => scene());
    await l.load("p", 1);
    expect((await l.load("p", 2)).status).toBe("loaded");
    expect(partGlb).toHaveBeenNthCalledWith(2, "p", '"a"');
    expect(partGlb).toHaveBeenNthCalledWith(3, "p");
  });

  it("an HTTP error on the conditional request is reported, not retried", async () => {
    const partGlb = vi.fn().mockResolvedValueOnce({ bytes, etag: '"a"' })
      .mockRejectedValueOnce(new CoreError("not_found", "no model.glb", { status: 404 }));
    const l = new PartLoader({ partGlb }, async () => scene());
    await l.load("p", 1);
    expect(await l.load("p", 2)).toEqual({ status: "error", message: "no model.glb" });
    expect(partGlb).toHaveBeenCalledTimes(2);
  });

  it("failure reports the message and a retry works", async () => {
    const partGlb = vi.fn().mockResolvedValueOnce({ bytes, etag: '"a"' }).mockRejectedValue(new Error("network down"));
    const l = new PartLoader({ partGlb }, async () => scene());
    await l.load("p", 1);
    expect(await l.load("p", 2)).toEqual({ status: "error", message: "network down" });
    partGlb.mockResolvedValueOnce({ bytes, etag: '"b"' });
    expect((await l.load("p", 2)).status).toBe("loaded");
  });

  it("a superseded load disposes its scene and reports stale", async () => {
    let release: (s: PartScene) => void = () => undefined;
    const slowParse = new Promise<PartScene>((r) => { release = r; });
    const partGlb = vi.fn().mockResolvedValue({ bytes, etag: '"n"' });
    const s1 = scene();
    const parse = vi.fn().mockReturnValueOnce(slowParse).mockResolvedValueOnce(scene());
    const l = new PartLoader({ partGlb }, parse);
    const first = l.load("p", 1);
    await new Promise((r) => setTimeout(r, 0)); // let load 1 reach its (slow) parse
    expect((await l.load("p", 2)).status).toBe("loaded");
    release(s1);
    expect((await first).status).toBe("stale");
    expect(s1.dispose).toHaveBeenCalled();
  });
});
