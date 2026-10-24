// Fetches and parses a part's GLB, but only when it actually changed (mtime) and with ETag revalidation.
import { CoreError, type CoreClient } from "../../api/client";
import { loadPart, type PartScene } from "./loadPart";

export type LoadResult =
  | { status: "loaded"; scene: PartScene }
  | { status: "unchanged" }
  | { status: "stale" }
  | { status: "error"; message: string };

export class PartLoader {
  private current: { partId: string; mtime: number; etag: string } | null = null;
  private seq = 0;

  private readonly client: Pick<CoreClient, "partGlb">;
  private readonly parse: (bytes: ArrayBuffer) => Promise<PartScene>;

  constructor(client: Pick<CoreClient, "partGlb">, parse: (bytes: ArrayBuffer) => Promise<PartScene> = loadPart) {
    this.client = client;
    this.parse = parse;
  }

  /** The previous model stays the caller's to keep on screen until `loaded` arrives. */
  async load(partId: string, mtime: number): Promise<LoadResult> {
    const cur = this.current;
    if (cur && cur.partId === partId && cur.mtime === mtime) return { status: "unchanged" };
    const mine = ++this.seq;
    try {
      const res = await this.fetch(partId, cur && cur.partId === partId ? cur.etag : undefined);
      if (mine !== this.seq) return { status: "stale" };
      if (res === "not-modified") {
        if (cur) this.current = { ...cur, mtime };
        return { status: "unchanged" };
      }
      const scene = await this.parse(res.bytes);
      if (mine !== this.seq) { scene.dispose(); return { status: "stale" }; }
      this.current = { partId, mtime, etag: res.etag };
      return { status: "loaded", scene };
    } catch (e) {
      if (mine !== this.seq) return { status: "stale" };
      return { status: "error", message: e instanceof Error ? e.message : String(e) };
    }
  }

  /**
   * Conditional GET first. A webview talking cross-origin to a core whose CORS preflight does not allow
   * `If-None-Match` fails with a network error; then fall back to a plain GET so the model still reloads.
   */
  private async fetch(partId: string, etag: string | undefined): ReturnType<Pick<CoreClient, "partGlb">["partGlb"]> {
    if (!etag) return this.client.partGlb(partId);
    try {
      return await this.client.partGlb(partId, etag);
    } catch (e) {
      if (e instanceof CoreError && e.code !== "network") throw e;
      return this.client.partGlb(partId);
    }
  }

  /** Forget the shown model (nothing selected): the next load fetches unconditionally. */
  reset(): void { this.seq++; this.current = null; }
}
