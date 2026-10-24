// @vitest-environment node
// The CSP has font-src 'self': a font Vite inlines as a data: URI would be blocked in the app.
import { describe, expect, it } from "vitest";
import config from "../vite.config";

describe("vite.config", () => {
  it("never_inlines_assets_as_data_uris", () => {
    expect((config as { build?: { assetsInlineLimit?: unknown } }).build?.assetsInlineLimit).toBe(0);
  });
});
