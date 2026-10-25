// @vitest-environment node
// Plan 3 routes (profile, keys, setup, Claude Code status/sign-in, Onshape test, UI context):
// the typed client against the fake core. Shapes follow the plan's Task 1-3 interfaces.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CoreClient, CoreError } from "../../src/api/client";
import { claudeCodeStatusSchema, keyStatusSchema, modelsInfoSchema, profileSchema, setupStateSchema } from "../../src/api/schemas";
import { loadScenario, startFakeCore, type FakeCore } from "./server";

let core: FakeCore;
let client: CoreClient;
const SECRET = "sk-ant-api03-SECRET-never-echo-1234";

async function failure(p: Promise<unknown>): Promise<CoreError> {
  const e = await p.then(() => null, (err: unknown) => err);
  if (!(e instanceof CoreError)) throw new Error(`expected a CoreError, got ${String(e)}`);
  return e;
}

/** Every response body the fake sent for the real routes, as text. */
async function rawGet(path: string): Promise<string> {
  const r = await fetch(core.url + path, { headers: { Authorization: `Bearer ${core.token}` } });
  return r.text();
}

describe("setup routes", () => {
  beforeEach(async () => {
    core = await startFakeCore({ port: 0, token: "t0k", scenario: "setup" });
    client = new CoreClient({ baseUrl: core.url, wsUrl: core.url.replace("http", "ws") + "/events", token: core.token, owned: false });
  });
  afterEach(async () => { await core.stop(); });

  it("fresh_setup_state_is_incomplete_and_lists_missing_pieces", async () => {
    const s = await client.setupState();
    expect(s).toEqual({ complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape", "profile"] });
  });

  it("scenarios_without_a_setup_block_are_complete_so_older_specs_are_not_gated", async () => {
    core.reset("base");
    const s = await client.setupState();
    expect(s.complete).toBe(true);
    expect((await client.getProfile()).setup_completed_at).not.toBeNull();
  });

  it("profile_get_and_partial_put", async () => {
    const p = await client.getProfile();
    expect(p).toMatchObject({ display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null, max_parallel_agents: 2, local_endpoints: {} });
    const q = await client.updateProfile({ display_name: "Hartigan", max_parallel_agents: 3 });
    expect(q.display_name).toBe("Hartigan");
    expect(q.max_parallel_agents).toBe(3);
    expect(q.default_material).toBe("PLA");
    const put = core.state.calls.at(-1);
    expect(put?.method).toBe("PUT");
    expect(put?.body).toEqual({ display_name: "Hartigan", max_parallel_agents: 3 });
    expect((await client.setupState()).missing).not.toContain("profile");
  });

  it("profile_put_validation_is_a_422", async () => {
    const e = await failure(client.updateProfile({ max_parallel_agents: "four" as unknown as number }));
    expect(e.code).toBe("validation");
    expect(e.status).toBe(422);
  });

  it("keys_list_status_only_and_save_never_echoes_the_secret", async () => {
    const keys = await client.listKeys();
    expect(keys.map((k) => k.vendor)).toEqual(["anthropic", "openai", "gemini", "openrouter", "ollama", "lmstudio", "onshape"]);
    expect(keys.every((k) => !k.present && k.source === "none")).toBe(true);
    const st = await client.setKey("anthropic", SECRET);
    expect(st).toEqual({ vendor: "anthropic", present: true, source: "keychain" });
    expect(core.state.calls.at(-1)?.body).toEqual({ secret: SECRET });
    for (const path of ["/keys", "/profile", "/setup", "/models"]) expect(await rawGet(path)).not.toContain(SECRET);
    expect(await client.setupState()).toMatchObject({ has_model: true });
    const models = await client.models();
    expect(models.vendors.find((v) => v.vendor === "anthropic")?.key).toBe("keychain");
    expect(await client.deleteKey("anthropic")).toEqual({ vendor: "anthropic", present: false, source: "none" });
    expect(core.state.calls.at(-1)?.method).toBe("DELETE");
  });

  it("onshape_keys_save_and_one_test_call", async () => {
    const before = await client.testOnshape();
    expect(before.ok).toBe(false);
    const st = await client.setOnshapeKeys("AK-123", "SK-456");
    expect(st).toEqual({ vendor: "onshape", present: true, source: "keychain" });
    expect(core.state.calls.at(-1)?.body).toEqual({ access_key: "AK-123", secret_key: "SK-456" });
    const n = core.state.calls.length;
    expect(await client.testOnshape()).toEqual({ ok: true, user_name: "Hartigan Deely" });
    expect(core.state.calls.length - n).toBe(1);
    expect((await client.setupState()).has_onshape).toBe(true);
  });

  it("onshape_wrong_keys_are_denied", async () => {
    const sc = loadScenario("setup");
    sc.setup = { ...sc.setup, onshape_user: null };
    core.reset(sc);
    await client.setOnshapeKeys("AK", "SK");
    expect(await client.testOnshape()).toEqual({ ok: false, error: "denied" });
  });

  it("empty_secret_or_unknown_vendor_is_rejected", async () => {
    expect((await failure(client.setKey("anthropic", ""))).code).toBe("invalid_input");
    expect((await failure(client.setKey("nope", "x"))).code).toBe("not_found");
  });

  it("claude_code_sign_in_flips_after_polls", async () => {
    expect(await client.claudeCodeStatus()).toEqual({ installed: true, version: "2.1.4", signed_in: false, auth_method: null });
    await client.claudeCodeLogin();
    expect(core.state.calls.at(-1)).toMatchObject({ method: "POST", path: "/claude-code/login" });
    const s = await client.claudeCodeStatus();
    expect(s.signed_in).toBe(true);
    expect(s.auth_method).toBe("claude.ai");
    expect((await client.setupState()).has_model).toBe(true);
    expect((await client.models()).vendors.find((v) => v.vendor === "claude-code")).toEqual({ vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus", "haiku", "fable"] });
    await client.claudeCodeLogout();
    expect((await client.claudeCodeStatus()).signed_in).toBe(false);
  });

  it("claude_code_login_when_not_installed_is_an_error", async () => {
    const sc = loadScenario("setup");
    sc.setup = { ...sc.setup, claude: { installed: false, version: null, signed_in: false, auth_method: null } };
    core.reset(sc);
    expect((await failure(client.claudeCodeLogin())).code).toBe("invalid_input");
    expect((await client.models()).vendors.find((v) => v.vendor === "claude-code")?.key).toBe("not_installed");
  });

  it("local_endpoint_counts_as_a_model_only_when_it_answers", async () => {
    await client.updateProfile({ local_endpoints: { lmstudio: "http://localhost:1234/v1" } });
    expect((await client.setupState()).has_model).toBe(false);
    await client.updateProfile({ local_endpoints: { ollama: "http://localhost:11434/v1" } });
    expect((await client.setupState()).has_model).toBe(true);
  });

  it("complete_sets_completed_at", async () => {
    await client.setKey("openai", "sk-x");
    await client.completeSetup();
    expect((await client.setupState()).complete).toBe(true);
    expect((await client.getProfile()).setup_completed_at).toEqual(expect.any(Number));
  });

  it("put_context", async () => {
    await client.putContext({ selected_part_id: "mount_plate", selected_run_id: null, picks: [] });
    expect(core.state.calls.at(-1)).toMatchObject({ method: "PUT", path: "/context", body: { selected_part_id: "mount_plate", selected_run_id: null, picks: [] } });
  });

  it("cors_preflight_allows_put_and_delete", async () => {
    for (const m of ["PUT", "DELETE"]) {
      const r = await fetch(core.url + "/profile", { method: "OPTIONS", headers: { Origin: "http://localhost:1420", "Access-Control-Request-Method": m, "Access-Control-Request-Headers": "authorization,content-type" } });
      expect(r.status).toBe(200);
      expect(r.headers.get("access-control-allow-methods")).toContain(m);
    }
  });
});

describe("setup schemas", () => {
  it("parse_core_shapes", () => {
    expect(profileSchema.parse({ display_name: "x" }).default_material).toBe("PLA");
    expect(keyStatusSchema.safeParse({ vendor: "a", present: true, source: "keychain", secret: "s" }).data).toEqual({ vendor: "a", present: true, source: "keychain" });
    expect(setupStateSchema.safeParse({ complete: true, has_model: true, has_onshape: false, missing: ["onshape"] }).success).toBe(true);
    expect(claudeCodeStatusSchema.parse({ installed: false, version: null, signed_in: false, auth_method: null }).installed).toBe(false);
    // Task 3 extends /models with claude-code entries; older cores still parse
    expect(modelsInfoSchema.parse({ default: "a/b", vendors: [{ vendor: "claude-code", key: "signed_out", models: ["sonnet"] }, { vendor: "a", key: "env" }] }).vendors[1]).toEqual({ vendor: "a", key: "env" });
  });
});
