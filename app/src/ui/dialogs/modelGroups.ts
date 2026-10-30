// Pure model of the model picker: GET /models (+ GET /local/status) → groups in a fixed order:
// "Recommended", "Your Claude Code", "On this Mac (Ollama)", "Free (OpenRouter)", "API keys".
// Every choice says whether it can run now and, if not, what's missing. Model ids, names and
// notes come from the core and the network: they stay plain strings (rendered as text).
import type { LocalStatus, ModelMeta, ModelsInfo, Profile, Recommended, VendorInfo } from "../../api/types";
import { splitModelRef } from "./modelRef";

export type GroupId = "recommended" | "claude" | "local" | "free" | "keys";

export const GROUP_TITLES: Record<GroupId, string> = {
  recommended: "Recommended", claude: "Your Claude Code", local: "On this Mac (Ollama)", free: "Free (OpenRouter)", keys: "API keys",
};
const ORDER: readonly GroupId[] = ["recommended", "claude", "local", "free", "keys"];

export interface Badge { kind: Recommended; text: string }
const BADGE_TEXT: Record<Recommended, string> = { best: "best", local: "runs on this Mac", free: "free" };

export interface ModelChoice {
  /** "claude-code/opus", "ollama/gpt-oss:20b", "openrouter/qwen/qwen3-coder:free". */
  ref: string;
  vendor: string;
  model: string;
  label: string;
  badges: Badge[];
  /** The core's own recommendation for this model (a free-list entry alone is not one). */
  recommended: Recommended | null;
  note: string | null;
  usable: boolean;
  /** Why it can't run now (null when usable). */
  why: string | null;
  /** The group this choice lives in (a Recommended copy keeps its home group). */
  group: Exclude<GroupId, "recommended">;
}

export interface LocalDownload { name: string; size_gb: number; note: string | null; ref: string }

export type LocalState = "running" | "stopped" | "not_installed" | "unknown";

export interface ModelGroup {
  id: GroupId;
  title: string;
  choices: ModelChoice[];
  /** One line from the core about the whole group (vendor note or hint). */
  note: string | null;
  /** "On this Mac" only: Ollama's state and the recommended models not downloaded yet (that fit). */
  local?: { state: LocalState; downloads: LocalDownload[] };
}

const CLAUDE = "claude-code";
const LOCAL_VENDORS = new Set(["ollama", "lmstudio"]);
const WORKS = new Set(["env", "keychain", "signed_in"]);

/** Live lists send full refs ("ollama/gpt-oss:20b"); claude-code sends bare aliases. */
export function stripVendor(vendor: string, entry: string): string {
  return entry.startsWith(`${vendor}/`) ? entry.slice(vendor.length + 1) : entry;
}

const VENDOR_NAMES: Record<string, string> = { "claude-code": "Claude Code", ollama: "Ollama", lmstudio: "LM Studio", openrouter: "OpenRouter" };

/** "claude-code/opus" → "Claude Code · opus"; other API vendors keep the core's name. */
export function refLabel(ref: string): string {
  const { vendor, model } = splitModelRef(ref);
  if (!vendor) return ref;
  return `${VENDOR_NAMES[vendor] ?? vendor} · ${model}`;
}

function gb(n: number): string {
  return `${Number.isInteger(n) ? n : n.toFixed(1)} GB`;
}

export interface GroupOpts {
  profile?: Pick<Profile, "default_model"> | null;
  /** GET /local/status; null/undefined when the core doesn't have it (older core) or it failed. */
  local?: LocalStatus | null;
}

/**
 * The picker's groups. Empty groups are left out, except "On this Mac" while Ollama can be
 * started or a recommended model downloaded (that's how a user gets a local model at all).
 */
export function buildModelGroups(models: ModelsInfo, opts: GroupOpts = {}): ModelGroup[] {
  const local = opts.local ?? null;
  const groups = new Map<GroupId, ModelGroup>(ORDER.map((id) => [id, { id, title: GROUP_TITLES[id], choices: [], note: null }]));
  const known = [splitModelRef(models.default), ...(opts.profile?.default_model ? [splitModelRef(opts.profile.default_model)] : [])];
  const recommendedLocal = new Map((local?.recommended ?? []).filter((r) => r.fits).map((r) => [r.name, r]));
  const recs: ModelChoice[] = [];

  for (const v of models.vendors) {
    const ids = (v.models ?? []).map((m) => stripVendor(v.vendor, m));
    if (v.vendor === CLAUDE) {
      const g = groups.get("claude") as ModelGroup;
      g.note = v.note ?? null;
      const usable = v.key === "signed_in";
      const why = usable ? null : v.key === "not_installed" ? "not installed — see Profile" : "signed out — sign in in Profile";
      const best = vendorPick(v, ids);
      for (const id of ids) g.choices.push(choice(v, id, `Claude Code · ${id}`, "claude", usable, why, best === id));
    } else if (LOCAL_VENDORS.has(v.vendor)) {
      const g = groups.get("local") as ModelGroup;
      if (v.vendor === "ollama") {
        const state: LocalState = !local ? "unknown" : !local.installed ? "not_installed" : !local.running ? "stopped" : "running";
        const downloaded = local ? local.models : ids.map((name) => ({ name, size_gb: 0 }));
        const usable = local ? local.running : v.key === "not_needed";
        const why = usable ? null : state === "not_installed" ? "Ollama isn't installed" : "Ollama isn't running";
        for (const m of downloaded) {
          const label = m.size_gb !== null && m.size_gb > 0 ? `${m.name} · ${gb(m.size_gb)}` : m.name;
          const c = choice(v, m.name, label, "local", usable, why, false);
          const rec = recommendedLocal.get(m.name);
          if (rec) { if (!c.badges.some((b) => b.kind === "local")) c.badges.push({ kind: "local", text: BADGE_TEXT.local }); c.note = c.note ?? rec.note; }
          g.choices.push(c);
        }
        const have = new Set(downloaded.map((m) => m.name));
        g.local = {
          state,
          downloads: [...recommendedLocal.values()].filter((r) => !have.has(r.name))
            .map((r) => ({ name: r.name, size_gb: r.size_gb, note: r.note, ref: `ollama/${r.name}` })),
        };
        if (state === "unknown" && v.hint) g.note = v.hint;
      } else {
        for (const id of ids) g.choices.push(choice(v, id, `LM Studio · ${id}`, "local", true, null, false));
      }
    } else if (v.vendor === "openrouter") {
      // free models (shown even without a key: the key itself is free); paid ones only once a key works
      const isFree = (id: string) => v.free === true || metaOf(v, id)?.free === true || id.endsWith(":free");
      const usable = WORKS.has(v.key);
      const g = groups.get("free") as ModelGroup;
      if (ids.some(isFree)) g.note = v.note ?? null;
      for (const id of ids.filter(isFree)) {
        const c = choice(v, id, id, "free", usable, usable ? null : "needs a free OpenRouter key — add it in Profile", false);
        if (!c.badges.some((b) => b.kind === "free")) c.badges.push({ kind: "free", text: BADGE_TEXT.free });
        g.choices.push(c);
      }
      if (usable) {
        const paid = [...new Set([...ids.filter((id) => !isFree(id)), ...known.filter((k) => k.vendor === v.vendor && k.model).map((k) => k.model)])];
        const keys = groups.get("keys") as ModelGroup;
        for (const id of paid) keys.choices.push(choice(v, id, `OpenRouter · ${id}`, "keys", true, null, false));
      }
    } else {
      const g = groups.get("keys") as ModelGroup;
      // the core's list (anthropic: only its best models, from `entries`) plus the default models
      const list = [...new Set([...ids, ...known.filter((k) => k.vendor === v.vendor && k.model).map((k) => k.model)])];
      const usable = WORKS.has(v.key);
      const best = vendorPick(v, list);
      for (const id of list) g.choices.push(choice(v, id, `${v.vendor} · ${id}`, "keys", usable, usable ? null : "no API key — add one in Profile", best === id));
    }
  }

  for (const id of ORDER) {
    if (id === "recommended") continue;
    for (const c of (groups.get(id) as ModelGroup).choices) {
      if (c.usable && c.badges.some((b) => b.kind === "best" || b.kind === "local" || (b.kind === "free" && c.recommended === "free"))) recs.push(c);
    }
  }
  (groups.get("recommended") as ModelGroup).choices = recs;

  return ORDER.map((id) => groups.get(id) as ModelGroup).filter((g) => g.choices.length > 0
    || (g.id === "local" && g.local !== undefined && (g.local.state !== "running" || g.local.downloads.length > 0 || g.note !== null)));
}

/** The model a vendor-level badge applies to: the per-model badges win, else the strongest alias. */
function vendorPick(v: VendorInfo, ids: readonly string[]): string | null {
  if (!v.recommended || ids.length === 0) return null;
  if (v.model_meta && Object.values(v.model_meta).some((m) => m.recommended)) return null;
  return ids.find((id) => /opus/.test(id)) ?? ids[0];
}

/** A model's badges: `model_meta` is keyed as the core listed it (bare id or "vendor/id"). */
function metaOf(v: VendorInfo, id: string): ModelMeta | undefined {
  return v.model_meta?.[id] ?? v.model_meta?.[`${v.vendor}/${id}`];
}

function choice(v: VendorInfo, id: string, label: string, group: ModelChoice["group"], usable: boolean, why: string | null, vendorBadge: boolean): ModelChoice {
  const meta = metaOf(v, id);
  const rec = meta?.recommended ?? (vendorBadge ? v.recommended ?? null : null);
  const c: ModelChoice = {
    ref: `${v.vendor}/${id}`, vendor: v.vendor, model: id, label, badges: [], recommended: rec, note: meta?.note ?? (vendorBadge ? v.note ?? null : null),
    usable, why, group,
  };
  if (rec) c.badges.push({ kind: rec, text: BADGE_TEXT[rec] });
  if (meta?.free && rec !== "free") c.badges.push({ kind: "free", text: BADGE_TEXT.free });
  return c;
}

/** The ref a picker starts on: the preferred one if it can run now, else the first recommended
 *  usable choice, else the first usable one, else null. */
export function pickDefault(groups: readonly ModelGroup[], preferred: string | null | undefined): string | null {
  const all = groups.flatMap((g) => g.choices);
  if (preferred && all.some((c) => c.ref === preferred && c.usable)) return preferred;
  return all.find((c) => c.usable)?.ref ?? null;
}

/** The model a new run starts on, shared by ⌘N's dialog and the console's inline composer (I6:
 *  they used to disagree): the agent's own model when one is picked, else the profile default the user
 *  chose (even when it can't run now: the form says why and offers Sign in), else the best usable model. */
export function newRunModel(cat: { models: ModelsInfo | null; local?: GroupOpts["local"]; profileDefault: string | null }, agentModel?: string | null): string | null {
  if (!cat.models) return null;
  const own = agentModel && agentModel !== "inherit" ? agentModel : null;
  const preferred = cat.profileDefault ?? cat.models.default;
  const groups = buildModelGroups(cat.models, { local: cat.local ?? null, profile: { default_model: cat.profileDefault } });
  return own ?? cat.profileDefault ?? pickDefault(groups, preferred) ?? preferred;
}

/** A ref's choice in its home group (not the Recommended copy), with that group's id. */
export function choiceFor(groups: readonly ModelGroup[], ref: string): ModelChoice | null {
  for (const g of groups) {
    if (g.id === "recommended") continue;
    const c = g.choices.find((x) => x.ref === ref);
    if (c) return c;
  }
  return null;
}
