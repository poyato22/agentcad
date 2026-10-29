// Fake core: Plan 3 profile / keys / setup / Claude Code / Onshape-test / local-test / context
// routes, with the real core's shapes and error answers (core server/profile_api.py,
// context_api.py; checked by test/contract.test.ts against fixtures dumped from the real core).
// No real logic beyond the shapes: secrets are only marked present (never stored, never
// returned), Claude Code "signs in" after N status polls once login started, and the Onshape and
// local-model tests answer from the scenario.
import type { ClaudeCodeStatus, KeyStatus, PartSummary, Profile, SetupPiece, SetupState } from "../../src/api/types";
import { asBody, HttpError, mises, type Body, type Route, type Scenario } from "./server";

/** Same list as core VENDORS ∪ {"onshape"} (GET /keys order). */
export const KEY_VENDORS = ["anthropic", "openai", "gemini", "openrouter", "ollama", "lmstudio", "onshape"];
/** Core profile.DEFAULT_LOCAL_ENDPOINTS: probed for has_model even when the profile lists none. */
export const LOCAL_DEFAULTS: Record<string, string> = { ollama: "http://localhost:11434/v1", lmstudio: "http://localhost:1234/v1" };
/** Vendors without an API key in core VENDORS (env_key None). */
const NO_KEY = new Set(["ollama", "lmstudio"]);
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export interface SetupScenario {
  profile?: Partial<Profile>;
  /** Present secrets by vendor (incl. "onshape"). */
  keys?: Record<string, "env" | "keychain">;
  claude?: ClaudeCodeStatus;
  /** Status polls after POST /claude-code/login until signed in; null = never (sign-in times out). Default 1. */
  login_after_polls?: number | null;
  /** User name the Onshape test returns when keys are saved; null = keys are wrong ("denied"). */
  onshape_user?: string | null;
  /** Local vendors whose server answers (at their default URL, or the URL saved for them).
   *  Like the real core, an answering default endpoint counts for has_model with nothing saved. */
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
  context: { selected_part_id: string | null; selected_run_id: string | null; picks: unknown[] };
}

export const DEFAULT_PROFILE: Profile = {
  display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: "auto", local_endpoints: {},
  theme: "system", setup_completed_at: null,
};

/** Core profile.PROFILE_SCHEMA_VERSION: GET/PUT /profile carry it, and the core's `printers`
 *  list (the print page's; none here). The app's schema strips both and never sends them. */
export const PROFILE_SCHEMA_VERSION = 2;
const withVersion = (p: Profile) => ({ schema_version: PROFILE_SCHEMA_VERSION, ...p, printers: [] });

const EMPTY_CONTEXT = { selected_part_id: null, selected_run_id: null, picks: [] };

export function setupFrom(sc: Scenario): FakeSetup {
  const s = sc.setup;
  if (!s) {
    // Older scenarios: setup already done, keys as /models says.
    const keys: Record<string, "env" | "keychain"> = {};
    for (const v of sc.models.vendors) if (v.key === "env" || v.key === "keychain") keys[v.vendor] = v.key;
    return {
      derived: false, profile: { ...DEFAULT_PROFILE, display_name: "Hartigan", setup_completed_at: 1790000000 }, keys,
      claude: { installed: true, version: "2.1.4", signed_in: true, auth_method: "claude.ai" },
      loginAfterPolls: 1, loginPending: null, onshapeUser: "Hartigan Deely", localAnswering: [], context: { ...EMPTY_CONTEXT },
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
    context: { ...EMPTY_CONTEXT },
  };
}

/** Does a local server answer at `url`? (the vendor's default URL, or the one saved for it) */
function answers(st: FakeSetup, url: string): boolean {
  return st.localAnswering.some((v) => LOCAL_DEFAULTS[v] === url || st.profile.local_endpoints[v] === url);
}

/** Core profile.setup_state: complete = setup_completed_at set; "profile" is missing until then. */
export function setupStateOf(st: FakeSetup): SetupState {
  const apiKey = Object.keys(st.keys).some((v) => v !== "onshape" && !NO_KEY.has(v));
  const urls = [...Object.values(st.profile.local_endpoints), ...Object.values(LOCAL_DEFAULTS)];
  const local = urls.some((u) => answers(st, u));
  const has_model = apiKey || st.claude.signed_in || local;
  const has_onshape = "onshape" in st.keys;
  const done = st.profile.setup_completed_at !== null;
  const missing: SetupPiece[] = [];
  if (!has_model) missing.push("model");
  if (!has_onshape) missing.push("onshape");
  if (!done) missing.push("profile");
  return { complete: done, has_model, has_onshape, missing };
}

function keyStatus(st: FakeSetup, vendor: string): KeyStatus {
  const src = st.keys[vendor];
  return { vendor, present: !!src, source: src ?? "none" };
}

/** Core profile_api._bad: 400 invalid_input (the /keys bodies are parsed by hand, never a 422). */
const bad = (message: string, hint: string | null = null): HttpError => mises("invalid_input", message, hint);

function keysBody(raw: unknown): Body {
  if (raw === undefined) throw bad("body must be JSON");
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw bad("body must be a JSON object");
  return raw as Body;
}

function secretField(b: Body, field: string): string {
  const v = b[field];
  if (typeof v !== "string" || !v.trim()) throw bad(`${field} is required`, "send a non-empty string");
  return v;
}

function keyVendor(vendor: string): string {
  if (vendor === "onshape") return vendor;
  if (!KEY_VENDORS.includes(vendor) || NO_KEY.has(vendor)) {
    throw bad(`no API key can be stored for '${vendor}'`, `keys: ${KEY_VENDORS.filter((v) => !NO_KEY.has(v) && v !== "onshape").join(", ")}, onshape`);
  }
  return vendor;
}

function loopback(url: string): boolean {
  try {
    const u = new URL(url);
    return (u.protocol === "http:" || u.protocol === "https:") && LOOPBACK.has(u.hostname);
  } catch {
    return false;
  }
}

/** Core profile_api._invalid: 422 with the core's error JSON (not pydantic's detail list). */
const invalidProfile = (msg: string): HttpError =>
  new HttpError(422, { error: "invalid_input", message: msg, hint: "fix the listed fields", retryable: false });

const int = (min: number, max = Number.MAX_SAFE_INTEGER) => (v: unknown): boolean => Number.isInteger(v) && (v as number) >= min && (v as number) <= max;

/** PUT /profile field checks (core profile.Profile: extra="forbid", ranges, loopback endpoints). */
const PROFILE_FIELDS: Record<string, (v: unknown) => boolean> = {
  display_name: (v) => typeof v === "string",
  units: (v) => v === "mm",
  default_material: (v) => typeof v === "string",
  default_agent: (v) => typeof v === "string",
  default_model: (v) => v === null || typeof v === "string",
  onshape_calls_year_cap: int(0),
  onshape_calls_run_cap: int(0),
  // Plan 4: "auto" (the core sizes it from this Mac and the model) or 1–16
  max_parallel_agents: (v) => v === "auto" || int(1, 16)(v),
  theme: (v) => v === "system" || v === "light" || v === "dark",
  hidden_agents: (v) => Array.isArray(v) && v.every((x) => typeof x === "string"),
  local_endpoints: (v) => typeof v === "object" && v !== null && !Array.isArray(v)
    && Object.values(v).every((x) => typeof x === "string" && loopback(x)),
  setup_completed_at: (v) => v === null || typeof v === "number",
  schema_version: int(1),
};

const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;

/** Core context_tools.build_context, for what the fake knows (parts; no Onshape, no sims). */
function buildContext(st: FakeSetup, parts: () => readonly PartSummary[]): Record<string, unknown> {
  const c = st.context;
  const part = c.selected_part_id ? parts().find((p) => p.part_id === c.selected_part_id) : undefined;
  return {
    selected_part_id: c.selected_part_id, selected_run_id: c.selected_run_id, picks: [...c.picks],
    metrics: part?.metrics ?? null, vars: part?.vars ?? null, doc_url: null, recent_sim: null,
  };
}

function claudeBody(st: FakeSetup): ClaudeCodeStatus {
  return st.claude;
}

export function setupRoutes(get: () => FakeSetup, parts: () => readonly PartSummary[] = () => []): Route[] {
  return [
    { method: "GET", re: /^\/profile$/, h: () => withVersion(get().profile) },
    {
      method: "PUT", re: /^\/profile$/, h: (_m, _q, raw) => {
        if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw invalidProfile(raw === undefined ? "body must be JSON" : "body must be a JSON object");
        const b = raw as Body;
        const st = get();
        const next: Record<string, unknown> = { ...st.profile };
        const errs: string[] = [];
        for (const [k, v] of Object.entries(b)) {
          const ok = PROFILE_FIELDS[k];
          if (!ok) { errs.push(`${k}: Extra inputs are not permitted`); continue; }
          if (k === "schema_version") {     // a field of the core's Profile (an int >= 1); always saved as the current one
            if (!ok(v)) errs.push(`${k}: Input should be valid`);
            continue;
          }
          if (k === "setup_completed_at") {
            if (v !== st.profile.setup_completed_at) throw invalidProfile("invalid profile: setup_completed_at is set by POST /setup/complete");
            continue;
          }
          if (!ok(v)) { errs.push(`${k}: Input should be valid`); continue; }
          next[k] = v;
        }
        if (errs.length) throw invalidProfile(`invalid profile: ${errs.join("; ")}`);
        st.profile = next as unknown as Profile;
        return withVersion(st.profile);
      },
    },
    { method: "GET", re: /^\/keys$/, h: () => KEY_VENDORS.map((v) => keyStatus(get(), v)) },
    {
      method: "POST", re: /^\/keys\/([^/]+)$/, h: (m, _q, raw) => {
        const vendor = keyVendor(decodeURIComponent(m[1]));
        const b = keysBody(raw);
        if (vendor === "onshape") {
          const access = secretField(b, "access_key");
          secretField(b, "secret_key");
          if (access.includes(":")) throw bad("access_key has an invalid format");
        } else secretField(b, "secret");
        const st = get();
        st.keys = { ...st.keys, [vendor]: "keychain" };
        return keyStatus(st, vendor);
      },
    },
    {
      method: "DELETE", re: /^\/keys\/([^/]+)$/, h: (m) => {
        const vendor = keyVendor(decodeURIComponent(m[1]));
        const st = get();
        // env keys stay (only the Keychain copy goes), like the real core
        if (st.keys[vendor] === "keychain") {
          const { [vendor]: _gone, ...rest } = st.keys;
          st.keys = rest;
        }
        return keyStatus(st, vendor);
      },
    },
    {
      method: "POST", re: /^\/onshape\/test$/, h: () => {
        const st = get();
        if (!("onshape" in st.keys)) return { ok: false, error: "not_configured" };
        return st.onshapeUser ? { ok: true, user_name: st.onshapeUser } : { ok: false, error: "denied" };
      },
    },
    {
      method: "POST", re: /^\/local\/test$/, h: (_m, _q, raw) => {
        if (raw === undefined) throw bad("body must be JSON");
        const url = typeof raw === "object" && raw !== null && !Array.isArray(raw) ? (raw as Body).url : undefined;
        if (typeof url !== "string" || !url || url.length > 2048) throw bad("url is required", 'send {"url": "http://localhost:11434/v1"}');
        if (!loopback(url)) throw bad("a local model URL must be http(s) on this Mac (localhost or 127.0.0.1)", "e.g. http://localhost:11434/v1");
        return answers(get(), url) ? { ok: true } : { ok: false, error: "unreachable" };
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
        return claudeBody(st);
      },
    },
    {
      method: "POST", re: /^\/claude-code\/login$/, h: () => {
        const st = get();
        if (!st.claude.installed) throw mises("not_found", "Claude Code is not installed", "install it from code.claude.com, then retry");
        if (st.loginPending !== null) return { started: false };
        if (!st.claude.signed_in && st.loginAfterPolls !== null) st.loginPending = st.loginAfterPolls;
        return { started: true };
      },
    },
    {
      method: "POST", re: /^\/claude-code\/logout$/, h: () => {
        const st = get();
        if (!st.claude.installed) throw mises("not_found", "Claude Code is not installed");
        st.loginPending = null;
        st.claude = { ...st.claude, signed_in: false, auth_method: "none" };
        return claudeBody(st);
      },
    },
    {
      method: "PUT", re: /^\/context$/, h: (_m, _q, raw) => {
        const b = asBody(raw);
        const pid = b.selected_part_id ?? null;
        const rid = b.selected_run_id ?? null;
        const picks = b.picks ?? [];
        if (pid !== null && (typeof pid !== "string" || !PART_ID_RE.test(pid))) {
          throw new HttpError(422, { detail: [{ type: "value_error", loc: ["body", "selected_part_id"], msg: "Value error, part id must match ^[a-z][a-z0-9_]{1,40}$", input: pid }] });
        }
        if (rid !== null && (typeof rid !== "string" || rid.length > 64)) {
          throw new HttpError(422, { detail: [{ type: "string_type", loc: ["body", "selected_run_id"], msg: "Input should be a valid string", input: rid }] });
        }
        if (!Array.isArray(picks)) throw new HttpError(422, { detail: [{ type: "list_type", loc: ["body", "picks"], msg: "Input should be a valid list", input: picks }] });
        const st = get();
        st.context = { selected_part_id: pid as string | null, selected_run_id: rid as string | null, picks };
        return buildContext(st, parts);
      },
    },
    { method: "GET", re: /^\/context$/, h: () => buildContext(get(), parts) },
  ];
}
