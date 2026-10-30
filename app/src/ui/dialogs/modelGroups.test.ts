import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { modelsInfoSchema } from "../../api/schemas";
import type { LocalStatus, ModelsInfo } from "../../api/types";
import { buildModelGroups, choiceFor, GROUP_TITLES, newRunModel, pickDefault, refLabel, stripVendor } from "./modelGroups";

const REAL = modelsInfoSchema.parse(JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/models.json"), "utf8")));

const FREE_NOTE = "Free on OpenRouter: 20 requests/min, one run at a time";
const entry = (model: string, recommended: "best" | "local" | "free" | null = null, note: string | null = null) => ({ model, recommended, note });

/** What a Plan 4 core answers (ui_api.models: per-vendor `entries`, top-level `recommended`) on a
 *  Mac with Ollama, an Anthropic key, Claude Code signed in. */
const PLAN4: ModelsInfo = modelsInfoSchema.parse({
  default: "anthropic/claude-sonnet-5-5",
  vendors: [
    { vendor: "anthropic", key: "keychain", entries: [entry("anthropic/claude-opus-5-5", "best", "strongest at CAD scripts")] },
    { vendor: "openai", key: "none" },
    { vendor: "openrouter", key: "none", free: true, models: ["openrouter/qwen/qwen3-coder:free", "openrouter/z-ai/glm-4.5-air:free"],
      entries: [entry("openrouter/qwen/qwen3-coder:free", "free", FREE_NOTE), entry("openrouter/z-ai/glm-4.5-air:free", "free", FREE_NOTE)] },
    { vendor: "ollama", key: "not_needed", models: ["ollama/gpt-oss:20b"], entries: [entry("ollama/gpt-oss:20b", "local", "fast, good tool use")] },
    { vendor: "lmstudio", key: "not_needed", models: [], hint: "LM Studio isn't running — start its local server (Developer tab)" },
    { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus", "haiku", "fable"],
      entries: [entry("claude-code/sonnet"), entry("claude-code/opus", "best", "uses your Claude plan"), entry("claude-code/haiku"), entry("claude-code/fable")] },
  ],
  recommended: [entry("claude-code/opus", "best", "uses your Claude plan"), entry("anthropic/claude-opus-5-5", "best", "strongest at CAD scripts"),
    entry("ollama/gpt-oss:20b", "local", "fast, good tool use")],
});

const LOCAL: LocalStatus = {
  installed: true, running: true, version: "0.12.3",
  models: [{ name: "gpt-oss:20b", size_gb: 13.8 }],
  recommended: [
    { name: "gpt-oss:20b", size_gb: 13.8, fits: true, note: "fast, good tool use" },
    { name: "qwen3-coder:30b", size_gb: 19, fits: true, note: "best local coder" },
    { name: "qwen3.6:27b", size_gb: 17, fits: false, note: "strong, but needs more memory" },
  ],
};

const titles = (g: ReturnType<typeof buildModelGroups>) => g.map((x) => x.title);
const refs = (g: ReturnType<typeof buildModelGroups>, id: string) => g.find((x) => x.id === id)?.choices.map((c) => c.ref) ?? [];

describe("buildModelGroups", () => {
  it("groups_in_the_fixed_order_with_their_titles", () => {
    const g = buildModelGroups(PLAN4, { local: LOCAL });
    expect(titles(g)).toEqual(["Recommended", "Your Claude Code", "On this Mac (Ollama)", "Free (OpenRouter)", "API keys"]);
    expect(GROUP_TITLES.local).toBe("On this Mac (Ollama)");
  });

  it("recommended_holds_usable_best_and_downloaded_local_picks_with_badges", () => {
    const g = buildModelGroups(PLAN4, { local: LOCAL });
    const rec = g.find((x) => x.id === "recommended");
    expect(rec?.choices.map((c) => c.ref)).toEqual(["claude-code/opus", "ollama/gpt-oss:20b", "anthropic/claude-opus-5-5"]);
    expect(rec?.choices.map((c) => c.badges.map((b) => b.text))).toEqual([["best"], ["runs on this Mac"], ["best"]]);
    expect(rec?.choices[0].note).toBe("uses your Claude plan");
    expect(rec?.choices[2].note).toBe("strongest at CAD scripts");
  });

  it("claude_code_aliases_are_bare_and_ollama_refs_are_not_doubled", () => {
    const g = buildModelGroups(PLAN4, { local: LOCAL });
    expect(refs(g, "claude")).toEqual(["claude-code/sonnet", "claude-code/opus", "claude-code/haiku", "claude-code/fable"]);
    expect(refs(g, "local")).toEqual(["ollama/gpt-oss:20b"]);
    expect(stripVendor("ollama", "ollama/gpt-oss:20b")).toBe("gpt-oss:20b");
    expect(stripVendor("openrouter", "openrouter/qwen/qwen3-coder:free")).toBe("qwen/qwen3-coder:free");
    expect(stripVendor("claude-code", "opus")).toBe("opus");
  });

  it("local_group_lists_downloaded_models_with_size_and_offers_recommended_downloads_that_fit", () => {
    const local = buildModelGroups(PLAN4, { local: LOCAL }).find((x) => x.id === "local");
    expect(local?.choices[0].label).toBe("gpt-oss:20b · 13.8 GB");
    expect(local?.local?.state).toBe("running");
    // qwen3.6:27b doesn't fit this Mac's memory: not offered; gpt-oss is already downloaded
    expect(local?.local?.downloads.map((d) => [d.name, d.size_gb])).toEqual([["qwen3-coder:30b", 19]]);
  });

  it("ollama_not_running_or_missing_is_a_state_not_a_list", () => {
    const stopped = buildModelGroups(PLAN4, { local: { ...LOCAL, running: false } }).find((x) => x.id === "local");
    expect(stopped?.local?.state).toBe("stopped");
    expect(stopped?.choices.every((c) => !c.usable)).toBe(true);
    const missing = buildModelGroups(PLAN4, { local: { ...LOCAL, installed: false, running: false, models: [] } }).find((x) => x.id === "local");
    expect(missing?.local?.state).toBe("not_installed");
    // older core: no /local/status → the vendor's own list and hint
    const old = buildModelGroups(PLAN4, { local: null }).find((x) => x.id === "local");
    expect(old?.local?.state).toBe("unknown");
    expect(old?.choices.map((c) => c.ref)).toEqual(["ollama/gpt-oss:20b"]);
  });

  it("free_openrouter_models_show_even_without_a_key_and_say_what_is_missing", () => {
    const free = buildModelGroups(PLAN4, { local: LOCAL }).find((x) => x.id === "free");
    expect(free?.choices.map((c) => c.ref)).toEqual(["openrouter/qwen/qwen3-coder:free", "openrouter/z-ai/glm-4.5-air:free"]);
    expect(free?.choices.every((c) => c.badges.some((b) => b.kind === "free"))).toBe(true);
    expect(free?.choices[0].usable).toBe(false);
    expect(free?.choices[0].why).toBe("needs a free OpenRouter key — add it in Profile");
    const keyed = { ...PLAN4, vendors: PLAN4.vendors.map((v) => (v.vendor === "openrouter" ? { ...v, key: "keychain" as const } : v)) };
    expect(buildModelGroups(keyed, {}).find((x) => x.id === "free")?.choices[0].usable).toBe(true);
  });

  it("api_key_vendors_use_their_list_else_the_known_defaults_and_say_when_a_key_is_missing", () => {
    const g = buildModelGroups(PLAN4, { profile: { default_model: "openai/gpt-5.5" } });
    const keys = g.find((x) => x.id === "keys");
    // anthropic: the core lists only its best model (from `entries`); the default model joins it
    expect(keys?.choices.map((c) => [c.ref, c.usable, c.why])).toEqual([
      ["anthropic/claude-opus-5-5", true, null],
      ["anthropic/claude-sonnet-5-5", true, null],
      ["openai/gpt-5.5", false, "no API key — add one in Profile"],
    ]);
  });

  it("with_an_openrouter_key_paid_models_show_under_api_keys", () => {
    const keyed: ModelsInfo = modelsInfoSchema.parse({
      default: "anthropic/claude-sonnet-5-5",
      vendors: [{ vendor: "openrouter", key: "keychain", models: ["openrouter/qwen/qwen3-coder:free", "openrouter/moonshotai/kimi-k3"],
        entries: [entry("openrouter/qwen/qwen3-coder:free", "free", FREE_NOTE), entry("openrouter/moonshotai/kimi-k3")] }],
    });
    const g = buildModelGroups(keyed, { profile: { default_model: "openrouter/anthropic/claude-sonnet-5" } });
    expect(refs(g, "free")).toEqual(["openrouter/qwen/qwen3-coder:free"]);
    expect(refs(g, "keys")).toEqual(["openrouter/moonshotai/kimi-k3", "openrouter/anthropic/claude-sonnet-5"]);
    // without a key, no paid entries (they couldn't run)
    const none = { ...keyed, vendors: keyed.vendors.map((v) => ({ ...v, key: "none" as const })) };
    expect(refs(buildModelGroups(none, { profile: { default_model: "openrouter/anthropic/claude-sonnet-5" } }), "keys")).toEqual([]);
  });

  it("signed_out_claude_code_is_listed_but_not_usable", () => {
    const out = { ...REAL, vendors: REAL.vendors.map((v) => (v.vendor === "claude-code" ? { ...v, key: "signed_out" as const } : v)) };
    const claude = buildModelGroups(out, {}).find((x) => x.id === "claude");
    expect(claude?.choices.every((c) => !c.usable && c.why === "signed out — sign in in Profile")).toBe(true);
    expect(refs(buildModelGroups(out, {}), "recommended").filter((r) => r.startsWith("claude-code/"))).toEqual([]);
  });

  it("the_real_core_answer_groups_with_its_badges", () => {
    const g = buildModelGroups(REAL, {});
    expect(titles(g)).toEqual(["Recommended", "Your Claude Code", "On this Mac (Ollama)", "Free (OpenRouter)", "API keys"]);
    // usable picks only: the fixture has Claude Code signed in, Ollama answering, no API keys
    expect(refs(g, "recommended")).toEqual(["claude-code/opus", "ollama/gpt-oss:20b"]);
    expect(refs(g, "keys")).toEqual([
      "anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "moonshot/kimi-k3.1-pro", "moonshot/kimi-k3.1-pro-highspeed", "moonshot/kimi-k3",
    ]);
    expect(choiceFor(g, "moonshot/kimi-k3")?.note).toBe("Kimi K3: 1M-token context, thinking always on");
    expect(choiceFor(g, "anthropic/claude-opus-5-5")?.badges.map((b) => b.kind)).toEqual(["best"]);
  });
});

describe("pickDefault / labels", () => {
  it("keeps_a_usable_preferred_ref_else_the_first_recommended_else_the_first_usable", () => {
    const g = buildModelGroups(PLAN4, { local: LOCAL });
    expect(pickDefault(g, "claude-code/haiku")).toBe("claude-code/haiku");
    expect(pickDefault(g, "openai/gpt-5.5")).toBe("claude-code/opus");
    expect(pickDefault(g, null)).toBe("claude-code/opus");
    expect(pickDefault(buildModelGroups(REAL, {}), null)).toBe("claude-code/opus");
    expect(pickDefault([], null)).toBeNull();
  });

  it("new_run_model_is_one_rule_for_the_dialog_and_the_composer (I6)", () => {
    // the user's own profile default is kept even when it can't run now (the form says why)
    expect(newRunModel({ models: PLAN4, local: LOCAL, profileDefault: "openai/gpt-5.5" })).toBe("openai/gpt-5.5");
    expect(newRunModel({ models: PLAN4, local: LOCAL, profileDefault: "claude-code/haiku" })).toBe("claude-code/haiku");
    expect(newRunModel({ models: PLAN4, local: LOCAL, profileDefault: null }, "openai/gpt-5.5")).toBe("openai/gpt-5.5");   // the agent's own
    expect(newRunModel({ models: PLAN4, local: LOCAL, profileDefault: null }, "inherit")).toBe(newRunModel({ models: PLAN4, local: LOCAL, profileDefault: null }));
    expect(newRunModel({ models: null, profileDefault: null })).toBeNull();
  });

  it("ref_labels_name_the_user's_way", () => {
    expect(refLabel("claude-code/opus")).toBe("Claude Code · opus");
    expect(refLabel("ollama/qwen3-coder:30b")).toBe("Ollama · qwen3-coder:30b");
    expect(refLabel("openrouter/qwen/qwen3-coder:free")).toBe("OpenRouter · qwen/qwen3-coder:free");
    expect(refLabel("anthropic/claude-sonnet-5-5")).toBe("anthropic · claude-sonnet-5-5");
  });

  it("choiceFor_finds_a_ref_in_its_home_group", () => {
    const g = buildModelGroups(PLAN4, { local: LOCAL });
    expect(choiceFor(g, "claude-code/opus")?.group).toBe("claude");
    expect(choiceFor(g, "nope/x")).toBeNull();
  });
});
