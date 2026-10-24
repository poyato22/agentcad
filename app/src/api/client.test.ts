import { describe, expect, it, vi } from "vitest";
import { CoreClient, CoreError } from "./client";
import type { AgentRun, CoreInfo } from "./types";

const info: CoreInfo = { baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "tok-abc", owned: false };

const run: AgentRun = {
  id: "a1b2c3d4e5f6", parent_id: null, agent: "designer", provider: "anthropic", model: "claude-sonnet-5-5",
  task: "stiffen the upright", status: "running", branch: null, onshape_calls: 0, input_tokens: 10,
  output_tokens: 20, usd: 0.01, created_at: 1790000000, updated_at: 1790000010,
};

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function clientWith(impl: (url: string, init: RequestInit) => Promise<Response>) {
  const f = vi.fn(impl);
  return { client: new CoreClient(info, f as unknown as typeof fetch), f };
}

async function caught(p: Promise<unknown>): Promise<CoreError> {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(CoreError);
    return e as CoreError;
  }
  throw new Error("expected a CoreError");
}

describe("CoreClient", () => {
  it("sends_bearer_header", async () => {
    const { client, f } = clientWith(async () => json([run]));
    await client.listRuns(5);
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:7400/runs?limit=5");
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer tok-abc");
  });

  it("maps_error_json_to_CoreError", async () => {
    const { client } = clientWith(async () => json({
      error: "stale_version", message: "approval payload changed since the card was shown",
      hint: "refresh the approval card", retryable: false,
    }, 409));
    const e = await caught(client.decide("ap1", { payload_hash: "x", approve: true }));
    expect(e.status).toBe(409);
    expect(e.code).toBe("stale_version");
    expect(e.hint).toBe("refresh the approval card");
    expect(e.retryable).toBe(false);
    expect(e.message).toBe("approval payload changed since the card was shown");
  });

  it("maps_401_to_unauthorized", async () => {
    const { client } = clientWith(async () => json({ error: "unauthorized", message: "missing or bad token" }, 401));
    const e = await caught(client.health());
    expect(e.status).toBe(401);
    expect(e.code).toBe("unauthorized");
    expect(e.retryable).toBe(false);
  });

  it("maps_422_to_validation", async () => {
    const { client } = clientWith(async () => json({ detail: [{ loc: ["body", "prompt"], msg: "Field required" }] }, 422));
    const e = await caught(client.createRun({ agent: "designer", prompt: "" }));
    expect(e.status).toBe(422);
    expect(e.code).toBe("validation");
  });

  it("network_failure_is_retryable_network_error", async () => {
    const { client } = clientWith(async () => { throw new TypeError("Failed to fetch"); });
    const e = await caught(client.listRuns());
    expect(e.code).toBe("network");
    expect(e.status).toBe(0);
    expect(e.retryable).toBe(true);
  });

  it("rejects_schema_drift", async () => {
    const { status: _drop, ...noStatus } = run;
    const { client } = clientWith(async () => json([noStatus]));
    const e = await caught(client.listRuns());
    expect(e.code).toBe("validation");
    expect(e.message).toContain("status");
  });

  it("partGlb_sends_if_none_match_and_handles_304", async () => {
    const bytes = new Uint8Array([0x67, 0x6c, 0x54, 0x46]);
    const { client, f } = clientWith(async (_url, init) => {
      const inm = new Headers(init.headers).get("if-none-match");
      if (inm === '"1-4"') return new Response(null, { status: 304 });
      return new Response(bytes, { status: 200, headers: { etag: '"1-4"', "content-type": "model/gltf-binary" } });
    });
    const first = await client.partGlb("mount_plate");
    expect(first).not.toBe("not-modified");
    if (first === "not-modified") throw new Error("unreachable");
    expect(first.etag).toBe('"1-4"');
    expect(new Uint8Array(first.bytes)).toEqual(bytes);
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/parts/mount_plate/model.glb");
    expect(new Headers(f.mock.calls[0][1].headers).has("if-none-match")).toBe(false);

    const second = await client.partGlb("mount_plate", first.etag);
    expect(second).toBe("not-modified");
    expect(new Headers(f.mock.calls[1][1].headers).get("if-none-match")).toBe('"1-4"');
  });

  it("posts_exact_decide_body", async () => {
    const { client, f } = clientWith(async () => json({
      id: "ap1", run_id: run.id, action: "fixture_merge", payload: {}, payload_hash: "h", microversion: null,
      deltas: {}, status: "approved", expires_at: 1, decided_by: "local-user", decided_at: 2, edited_payload: null,
    }));
    const a = await client.decide("ap 1", { payload_hash: "h", approve: true });
    expect(a.status).toBe("approved");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:7400/approvals/ap%201/decide");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ payload_hash: "h", approve: true });
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
  });

  it("steps_and_approvals_query_strings", async () => {
    const { client, f } = clientWith(async () => json([]));
    await client.steps("r1", 7);
    await client.pendingApprovals("r1");
    await client.pendingApprovals();
    expect(f.mock.calls.map((c) => c[0])).toEqual([
      "http://127.0.0.1:7400/runs/r1/steps?after_id=7",
      "http://127.0.0.1:7400/approvals?status=pending&run_id=r1",
      "http://127.0.0.1:7400/approvals?status=pending",
    ]);
  });

  it("unknown_error_body_falls_back_by_status", async () => {
    const { client } = clientWith(async () => json({ detail: "Not Found" }, 404));
    const e = await caught(client.getRun("nope"));
    expect(e.code).toBe("not_found");
    expect(e.hint).toBeNull();
  });
});
