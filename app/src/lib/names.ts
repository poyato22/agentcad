// All user-visible naming goes through here (design §5.1: no auto-numbered names).
// Rows read "Type · purpose"; parts read "Part · mount plate"; runs read "designer · stiffen upright".
// When two runs would read the same, add what differs (provider, model, start time), never a counter.
import type { AgentRun } from "../api/types";
import { fmtClock, fmtHM } from "./format";

const TRAILING_NUMBER = /\d+\s*$/;          // same rule as core naming.py
const CODE_NAME = /^[A-Z]+-\d+$/;           // GEOM-01, SIM-02 …

export function isAutoNumbered(name: string): boolean {
  return TRAILING_NUMBER.test(name) || CODE_NAME.test(name.trim());
}

/** Port of core `naming.display_name`. */
export function displayName(featureType: string, name: string, params?: Record<string, unknown>): string {
  if (!TRAILING_NUMBER.test(name)) return name;
  let purpose = "unnamed";
  for (const k of ["purpose", "name", "label"]) {
    const v = params?.[k];
    if (typeof v === "string" && v.trim()) { purpose = v.trim(); break; }
  }
  return `${featureType} · ${purpose}`;
}

/** "mount_plate" → "Part · mount plate". */
export function partLabel(partId: string): string {
  return `Part · ${partId.replace(/[_-]+/g, " ").trim()}`;
}

const ARTICLES = new Set(["the", "a", "an"]);
const MAX_WORDS = 4;

/** First words of a task: lower-case, articles dropped, never ending in a number. */
export function taskWords(task: string): string {
  const firstLine = task.trim().split("\n")[0] ?? "";
  const words = firstLine.toLowerCase().replace(/[.,;:!?"'()[\]{}]+/g, " ").split(/\s+/)
    .filter((w) => w && !ARTICLES.has(w)).slice(0, MAX_WORDS);
  while (words.length && /\d$/.test(words[words.length - 1])) words.pop();
  return words.length ? words.join(" ") : "task";
}

/**
 * "designer · stiffen upright". On a clash with another run, add the provider if that differs,
 * else the model, else the start time ("14:02", then "14:02:05" for the same minute).
 */
/** The app's own run (provider "ui": slices, prints and other actions started from a page). */
export const UI_RUN_LABEL = "You (Mises)";

export function runLabel(run: AgentRun, all: readonly AgentRun[]): string {
  if (run.provider === "ui") return UI_RUN_LABEL;
  const base = `${run.agent} · ${taskWords(run.task)}`;
  const clash = all.filter((r) => r.id !== run.id && `${r.agent} · ${taskWords(r.task)}` === base);
  if (clash.length === 0) return base;
  if (clash.every((r) => r.provider !== run.provider)) return `${base} · ${run.provider}`;
  if (clash.every((r) => r.model !== run.model)) return `${base} · ${run.model}`;
  const hm = fmtHM(run.created_at);
  if (clash.every((r) => fmtHM(r.created_at) !== hm)) return `${base} · ${hm}`;
  return `${base} · ${fmtClock(run.created_at)}`;
}

/** Shown provider names. The user's own Claude Code is a provider "Claude Code (your account)",
 *  never a Mises feature name. Others show as the core names them. */
export function providerLabel(provider: string): string {
  return provider === "claude-code" ? "Claude Code (your account)" : provider;
}

/** A part row's own name: the user's label when it has one (Plan 5 part tree), else its id
 *  with spaces ("quad_arm" → "quad arm"). No "Part ·" prefix: the tree and edit lines name parts. */
export function partName(p: { part_id: string; label?: string | null }): string {
  const label = p.label?.trim();
  if (label && label !== p.part_id) return label;
  return p.part_id.replace(/[_-]+/g, " ").trim();
}
