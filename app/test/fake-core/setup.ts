// Fake core: Plan 3 profile / keys / setup / Claude Code / Onshape-test / context routes, with the
// shapes of the plan's Task 1-3 interfaces (core server/profile_api.py). No real logic beyond the
// shapes: secrets are only marked present (never stored, never returned), Claude Code "signs in"
// after N status polls once login started, and the Onshape test answers from the scenario.
import type { ClaudeCodeStatus, KeyStatus, Profile, SetupPiece, SetupState } from "../../src/api/types";
import { asBody, HttpError, mises, wrongType, type Body, type Route, type Scenario } from "./server";

/** Same list as core VENDORS ∪ {"onshape"} (GET /keys order). */
export const KEY_VENDORS = ["anthropic", "openai", "gemini", "openrouter", "ollama", "lmstudio", "onshape"];
/** Vendors without an API key in core VENDORS (env_key None). */
const NO_KEY = new Set(["ollama", "lmstudio"]);

export interface SetupScenario {
  profile?: Partial<Profile>;
  /** Present secrets by vendor (incl. "onshape"). */
  keys?: Record<string, "env" | "keychain">;
  claude?: ClaudeCodeStatus;
  /** Status polls after POST /claude-code/login until signed in; null = never (sign-in times out). Default 1. */
  login_after_polls?: number | null;
  /** User name the Onshape test returns when keys are saved; null = keys are wrong ("denied"). */
  onshape_user?: string | null;
  /** Local vendors whose endpoint "answers" once set in profile.local_endpoints. */
  local_answering?: string[];
}

export interface FakeSetup {
  /** The scenario had a setup block: /models key status follows this state. */
  derived: boolean;
  profile: Profile;
  keys: Record<string, "env" | "keychain">;
  claude: ClaudeCodeStatus;
  loginAfterPolls: number | null;
  /** Polls left until signed in; null = no login running. */
  loginPending: number | null;
  onshapeUser: string | null;
  localAnswering: string[];
  context: unknown;
}

export const DEFAULT_PROFILE: Profile = {
  display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2, local_endpoints: {},
  setup_completed_at: null,
};

export function setupFrom(sc: Scenario): FakeSetup {
  const s = sc.setup;
  if (!s) {
    // Older scenarios: setup already done, keys as /models says.
    const keys: Record<string, "env" | "keychain"> = {};
    for (const v of sc.models.vendors) if (v.key === "env" || v.key === "keychain") keys[v.vendor] = v.key;
    return {
      derived: false, profile: { ...DEFAULT_PROFILE, display_name: "Hartigan", setup_completed_at: 1790000000 }, keys,
      claude: { installed: true, version: "2.1.4", signed_in: true, auth_method: "claude.ai" },
      loginAfterPolls: 1, loginPending: null, onshapeUser: "Hartigan Deely", localAnswering: [], context: null,
    };
  }
  return {
    derived: true,
    profile: { ...DEFAULT_PROFILE, ...s.profile },
    keys: { ...s.keys },
    claude: s.claude ?? { installed: true, version: "2.1.4", signed_in: false, auth_method: null },
    loginAfterPolls: s.login_after_polls === undefined ? 1 : s.login_after_polls,
    loginPending: null,
    onshapeUser: s.onshape_user === undefined ? "Hartigan Deely" : s.onshape_user,
    localAnswering: s.local_answering ?? [],
    context: null,
  };
}

export function setupStateOf(st: FakeSetup): SetupState {
  const apiKey = Object.keys(st.keys).some((v) => v !== "onshape");
  const local = Object.keys(st.profile.local_endpoints).some((v) => st.localAnswering.includes(v));
  const has_model = apiKey || st.claude.signed_in || local;
  const has_onshape = "onshape" in st.keys;
  const missing: SetupPiece[] = [];
  if (!has_model) missing.push("model");
  if (!has_onshape) missing.push("onshape");
  if (!st.profile.display_name.trim()) missing.push("profile");
  return { complete: has_model && st.profile.setup_completed_at !== null, has_model, has_onshape, missing };
}

function keyStatus(st: FakeSetup, vendor: string): KeyStatus {
  const src = st.keys[vendor];
  return { vendor, present: !!src, source: src ?? "none" };
}

function nonEmpty(b: Body, field: string): string {
  const v = b[field];
  if (v === undefined) throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", field], msg: "Field required" }] });
  if (typeof v !== "string") throw wrongType(field, "string", "<redacted>");
  if (!v.trim()) throw mises("invalid_input", `${field} is empty`, "paste the key again");
  return v;
}

/** PUT /profile field checks (pydantic-like 422 on a wrong type). */
const PROFILE_FIELDS: Record<string, (v: unknown) => boolean> = {
  display_name: (v) => typeof v === "string",
  default_material: (v) => typeof v === "string",
  default_agent: (v) => typeof v === "string",
  default_model: (v) => v === null || typeof v === "string",
  onshape_calls_year_cap: (v) => Number.isInteger(v),
  onshape_calls_run_cap: (v) => Number.isInteger(v),
  max_parallel_agents: (v) => Number.isInteger(v) && (v as number) >= 1,
  local_endpoints: (v) => typeof v === "object" && v !== null && !Array.isArray(v) && Object.values(v).every((x) => typeof x === "string"),
};

export function setupRoutes(get: () => FakeSetup): Route[] {
  return [
    { method: "GET", re: /^\/profile$/, h: () => get().profile },
    {
      method: "PUT", re: /^\/profile$/, h: (_m, _q, raw) => {
        const b = asBody(raw);
        const st = get();
        const next: Record<string, unknown> = { ...st.profile };
        for (const [k, v] of Object.entries(b)) {
          const ok = PROFILE_FIELDS[k];
          if (!ok) continue; // pydantic ignores unknown keys
          if (!ok(v)) throw new HttpError(422, { detail: [{ type: "value_error", loc: ["body", k], msg: "Input should be valid", input: v }] });
          next[k] = v;
        }
        st.profile = next as unknown as Profile;
        return st.profile;
      },
    },
    { method: "GET", re: /^\/keys$/, h: () => KEY_VENDORS.map((v) => keyStatus(get(), v)) },
    {
      method: "POST", re: /^\/keys\/([^/]+)$/, h: (m, _q, raw) => {
        const vendor = decodeURIComponent(m[1]);
        if (!KEY_VENDORS.includes(vendor) || NO_KEY.has(vendor)) throw mises("not_found", `unknown key vendor: ${vendor}`, `vendors: ${KEY_VENDORS.filter((v) => !NO_KEY.has(v)).join(", ")}`);
        const b = asBody(raw);
        if (vendor === "onshape") { nonEmpty(b, "access_key"); nonEmpty(b, "secret_key"); } else nonEmpty(b, "secret");
        const st = get();
        st.keys = { ...st.keys, [vendor]: "keychain" };
        return keyStatus(st, vendor);
      },
    },
    {
      method: "DELETE", re: /^\/keys\/([^/]+)$/, h: (m) => {
        const vendor = decodeURIComponent(m[1]);
        if (!KEY_VENDORS.includes(vendor)) throw mises("not_found", `unknown key vendor: ${vendor}`);
        const st = get();
        const { [vendor]: _gone, ...rest } = st.keys;
        st.keys = rest;
        return keyStatus(st, vendor);
      },
    },
    {
      method: "POST", re: /^\/onshape\/test$/, h: () => {
        const st = get();
        if (!("onshape" in st.keys)) return { ok: false, error: "no Onshape keys saved" };
        return st.onshapeUser ? { ok: true, user_name: st.onshapeUser } : { ok: false, error: "denied" };
      },
    },
    { method: "GET", re: /^\/setup$/, h: () => setupStateOf(get()) },
    {
      method: "POST", re: /^\/setup\/complete$/, h: () => {
        const st = get();
        st.profile = { ...st.profile, setup_completed_at: Date.now() / 1000 };
        return setupStateOf(st);
      },
    },
    {
      method: "GET", re: /^\/claude-code\/status$/, h: () => {
        const st = get();
        if (st.loginPending !== null) {
          st.loginPending -= 1;
          if (st.loginPending <= 0) {
            st.loginPending = null;
            st.claude = { ...st.claude, signed_in: true, auth_method: "claude.ai" };
          }
        }
        return st.claude;
      },
    },
    {
      method: "POST", re: /^\/claude-code\/login$/, h: () => {
        const st = get();
        if (!st.claude.installed) throw mises("invalid_input", "Claude Code is not installed", "install it, then try again");
        if (!st.claude.signed_in && st.loginAfterPolls !== null) st.loginPending = st.loginAfterPolls;
        return { ok: true };
      },
    },
    {
      method: "POST", re: /^\/claude-code\/logout$/, h: () => {
        const st = get();
        st.loginPending = null;
        st.claude = { ...st.claude, signed_in: false, auth_method: null };
        return { ok: true };
      },
    },
    {
      method: "PUT", re: /^\/context$/, h: (_m, _q, raw) => {
        get().context = asBody(raw);
        return { ok: true };
      },
    },
  ];
}
