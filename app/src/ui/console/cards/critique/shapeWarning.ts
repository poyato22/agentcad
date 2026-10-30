// G-R6: warn when shape-heavy work runs on a model that cannot see its renders, or on a small local
// model. Shape-heaviness is the core's `shape_complexity` on the run (G1), read leniently: a score
// (0…1, or 0…10), a level ("low" | "medium" | "high") or {level?, score?, signals? | keywords?,
// vision_recommended?} (G1's run row). No guess is
// made without it.
import type { AgentRun } from "../../../../api/types";

export type ShapeLevel = "low" | "medium" | "high";
export interface ShapeComplexity { level: ShapeLevel; score: number | null; keywords: string[] }

function levelOf(score: number): ShapeLevel { return score >= 0.6 ? "high" : score >= 0.3 ? "medium" : "low"; }
function norm(score: number): number { return score > 1 && score <= 10 ? score / 10 : Math.min(1, Math.max(0, score)); }

export function shapeComplexity(raw: unknown): ShapeComplexity | null {
  if (typeof raw === "number" && Number.isFinite(raw)) { const s = norm(raw); return { level: levelOf(s), score: s, keywords: [] }; }
  if (raw === "low" || raw === "medium" || raw === "high") return { level: raw, score: null, keywords: [] };
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const score = typeof r.score === "number" && Number.isFinite(r.score) ? norm(r.score) : null;
  const level = r.level === "low" || r.level === "medium" || r.level === "high" ? r.level : score !== null ? levelOf(score) : null;
  if (!level) return null;
  // G1 names them `signals`
  const words = Array.isArray(r.signals) ? r.signals : r.keywords;
  const keywords = Array.isArray(words) ? words.filter((k): k is string => typeof k === "string").map((k) => k.slice(0, 40)).slice(0, 6) : [];
  return { level, score, keywords };
}

/** Providers whose runs get images (the core's sketch.VISION_PROVIDERS). */
const VISION_PROVIDERS = new Set(["anthropic", "claude-code", "openai", "gemini"]);
/** Text-only models of those providers. */
const NO_VISION_MODELS = /(gpt-3\.5|o1-mini|o3-mini|text-)/i;
const LOCAL_PROVIDERS = new Set(["ollama", "lmstudio", "mlx", "local", "llamacpp"]);
/** Local models this size (billions of parameters) and up are not "small". */
const BIG_LOCAL_B = 60;

export type ModelConcern = "no-vision" | "small-local";

/** What is wrong with this run's model for shape work, or null. */
export function modelConcern(run: Pick<AgentRun, "provider" | "model">): ModelConcern | null {
  const provider = run.provider.toLowerCase();
  if (LOCAL_PROVIDERS.has(provider)) {
    const b = /(\d+(?:\.\d+)?)\s*b\b/i.exec(run.model.replace(/[:_-]/g, " "));
    if (!b || Number(b[1]) < BIG_LOCAL_B) return "small-local";
  }
  if (!VISION_PROVIDERS.has(provider) || NO_VISION_MODELS.test(run.model)) return "no-vision";
  return null;
}

export interface ShapeWarning { concern: ModelConcern; level: ShapeLevel; text: string; detail: string }

export function shapeWarning(run: Pick<AgentRun, "provider" | "model" | "shape_complexity">): ShapeWarning | null {
  const shape = shapeComplexity(run.shape_complexity);
  if (!shape || shape.level === "low") return null;
  const concern = modelConcern(run);
  if (!concern) return null;
  const what = shape.keywords.length ? ` (${shape.keywords.join(", ")})` : "";
  return {
    concern, level: shape.level,
    text: concern === "no-vision" ? "can't see its renders" : "small model for shape work",
    detail: `${concern === "no-vision" ? "This model can't look at its own renders" : "A small local model"}, and the request looks shape-heavy${what}. `
      + "Curved and layered shapes come out much better with a strong vision model, such as Claude Code (Opus).",
  };
}
