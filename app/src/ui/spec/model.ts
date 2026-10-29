// Pure logic of the spec panel: how requirements, targets and measurements read; the interview
// answers a batch sends; the Pareto front and sort order of a study's iterations; the spec
// editor's draft ⇄ Spec round trip. No IO, no signals (tests drive these directly).
import {
  LOAD_CASE_KINDS, NUMERIC_KINDS, type AskItem, type CheckRow, type LoadCase, type ReqStatus, type Requirement,
  type RequirementKind, type Spec, type StudyIteration,
} from "../../api/spec";

// ---- reading requirements

export const KIND_LABEL: Record<RequirementKind, string> = {
  max_mass_g: "Mass", min_safety_factor: "Safety factor", max_deflection_mm: "Deflection",
  envelope_mm: "Envelope", mount_pattern: "Mount pattern", printable: "Printable", custom: "Custom",
};

/** Short names for narrow tables. */
export const KIND_SHORT: Record<RequirementKind, string> = {
  max_mass_g: "Mass", min_safety_factor: "SF", max_deflection_mm: "Deflection", envelope_mm: "Envelope",
  mount_pattern: "Mount", printable: "Printable", custom: "Custom",
};

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Significant-ish digits for engineering readouts: 1234 g, 48.2 g, 6.21 g, 0.436 mm. */
export function fmtNum(v: number): string {
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1).replace(/\.0$/, "");
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return String(Number(v.toPrecision(3)));
}

function short(v: unknown): string {
  if (typeof v === "string") return v;
  if (isNum(v)) return fmtNum(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (v === null || v === undefined) return "—";
  const s = JSON.stringify(v);
  return s.length > 40 ? `${s.slice(0, 39)}…` : s;
}

/** "≤ 8 g", "≥ 2.00", "≤ 50×30×12 mm", "20x20 M3", "yes". */
export function fmtTarget(kind: RequirementKind, t: unknown): string {
  switch (kind) {
    case "max_mass_g": return isNum(t) ? `≤ ${fmtNum(t)} g` : short(t);
    case "min_safety_factor": return isNum(t) ? `SF ≥ ${t.toFixed(2)}` : short(t);
    case "max_deflection_mm": return isNum(t) ? `≤ ${fmtNum(t)} mm` : short(t);
    case "envelope_mm": return Array.isArray(t) && t.every(isNum) ? `≤ ${t.map(fmtNum).join("×")} mm` : short(t);
    default: return short(t);
  }
}

/** The measured value in the requirement's unit; "—" when there is none. */
export function fmtMeasured(kind: RequirementKind, m: unknown): string {
  if (m === null || m === undefined) return "—";
  switch (kind) {
    case "max_mass_g": return isNum(m) ? `${fmtNum(m)} g` : short(m);
    case "min_safety_factor": return isNum(m) ? `SF ${m.toFixed(2)}` : short(m);
    case "max_deflection_mm": return isNum(m) ? `${fmtNum(m)} mm` : short(m);
    case "envelope_mm": return Array.isArray(m) && m.every(isNum) ? `${m.map(fmtNum).join("×")} mm` : short(m);
    default: return short(m);
  }
}

/** ●●●○○ for priority 3 of 5 (1 = nice to have, 5 = must). */
export function priorityDots(p: number): string {
  const n = Math.max(1, Math.min(5, Math.round(p)));
  return "●".repeat(n) + "○".repeat(5 - n);
}

export const STATUS_WORD: Record<ReqStatus, string> = { pass: "pass", warn: "warn", fail: "fail", not_checked: "not checked" };
export const STATUS_TONE: Record<ReqStatus, "green" | "amber" | "red" | "faint"> = { pass: "green", warn: "amber", fail: "red", not_checked: "faint" };

export interface StatusTally { pass: number; warn: number; fail: number; not_checked: number }
export function tally(rows: readonly { status: ReqStatus }[]): StatusTally {
  const t: StatusTally = { pass: 0, warn: 0, fail: 0, not_checked: 0 };
  for (const r of rows) t[r.status]++;
  return t;
}

/** "2 pass · 1 fail · 3 not checked" (zero counts left out). */
export function tallyText(t: StatusTally): string {
  const parts = (["pass", "warn", "fail", "not_checked"] as const).filter((k) => t[k] > 0).map((k) => `${t[k]} ${STATUS_WORD[k]}`);
  return parts.length ? parts.join(" · ") : "no requirements";
}

/** Spec requirements joined with the latest check rows (by id); rows the check doesn't know read
 *  as not checked. Highest priority first, then spec order. */
export interface ReqView { req: Requirement; row: CheckRow | null; status: ReqStatus }
export function reqViews(spec: Spec, check: readonly CheckRow[] | null): ReqView[] {
  const byId = new Map((check ?? []).map((r) => [r.id, r]));
  return spec.requirements
    .map((req, i) => ({ req, row: byId.get(req.id) ?? null, i }))
    .sort((a, b) => b.req.priority - a.req.priority || a.i - b.i)
    .map(({ req, row }) => ({ req, row, status: row?.status ?? "not_checked" }));
}

// ---- one study iteration against the spec (same thresholds as core spec.evaluate_requirement)

/** pass / warn / fail of one requirement for one variant from its study metrics, or null when the
 *  iteration has no evidence for it (geometry-only study, failed solve, non-metric kind). */
export function iterStatus(req: Requirement, it: StudyIteration): { status: ReqStatus; measured: unknown } {
  const m = it.metrics;
  const t = req.target;
  const no = { status: "not_checked" as const, measured: null };
  if (!isNum(t) && req.kind !== "envelope_mm") return no;
  switch (req.kind) {
    case "max_mass_g": {
      if (!isNum(m.mass_g) || !isNum(t)) return no;
      return { measured: m.mass_g, status: m.mass_g <= t ? "pass" : m.mass_g <= t * 1.05 ? "warn" : "fail" };
    }
    case "min_safety_factor": {
      const v = req.load_case_id ? m.sf_by_case[req.load_case_id] : null;
      if (!isNum(v) || !isNum(t)) return no;
      return { measured: v, status: v >= t ? "pass" : v >= t * 0.9 ? "warn" : "fail" };
    }
    case "max_deflection_mm": {
      const v = req.load_case_id ? m.disp_by_case[req.load_case_id] : null;
      if (!isNum(v) || !isNum(t)) return no;
      return { measured: v, status: v <= t ? "pass" : v <= t * 1.1 ? "warn" : "fail" };
    }
    case "envelope_mm": {
      const size = m.bbox?.size;
      if (!Array.isArray(t) || !t.every(isNum) || !size || size.length !== 3) return no;
      const s = [...size].sort((a, b) => a - b);
      const tt = [...(t as number[])].sort((a, b) => a - b);
      const fits = s.every((x, i) => x <= tt[i] + 1e-6);
      const near = s.every((x, i) => x <= tt[i] * 1.05);
      return { measured: size, status: fits ? "pass" : near ? "warn" : "fail" };
    }
    default: return no;
  }
}

// ---- the interview (a ui_ask_user batch)

/** What the user has picked or typed for one question. `order` is the rank order (rank kind). */
export interface AnswerDraft { picks: string[]; text: string; order: string[] }
export type InterviewDraft = Record<string, AnswerDraft>;

export function emptyDraft(items: readonly AskItem[]): InterviewDraft {
  return Object.fromEntries(items.map((q) => [q.id, { picks: [], text: "", order: q.rank ? [...q.options] : [] }]));
}

/** Toggle an option chip: single replaces, multi adds/removes. Picking clears typed text for a
 *  single question (one answer per question). */
export function pick(draft: InterviewDraft, q: AskItem, option: string): InterviewDraft {
  const cur = draft[q.id] ?? { picks: [], text: "", order: [] };
  const picks = q.multi
    ? (cur.picks.includes(option) ? cur.picks.filter((p) => p !== option) : [...cur.picks, option])
    : (cur.picks[0] === option ? [] : [option]);
  return { ...draft, [q.id]: { ...cur, picks, text: q.multi ? cur.text : "" } };
}

export function setText(draft: InterviewDraft, q: AskItem, text: string): InterviewDraft {
  const cur = draft[q.id] ?? { picks: [], text: "", order: [] };
  return { ...draft, [q.id]: { ...cur, text, picks: q.multi || q.rank || !text.trim() ? cur.picks : [] } };
}

/** Move a ranked option up (-1) or down (+1). */
export function move(draft: InterviewDraft, q: AskItem, option: string, delta: -1 | 1): InterviewDraft {
  const cur = draft[q.id];
  if (!cur) return draft;
  const i = cur.order.indexOf(option);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= cur.order.length) return draft;
  const order = [...cur.order];
  [order[i], order[j]] = [order[j], order[i]];
  return { ...draft, [q.id]: { ...cur, order } };
}

/** The value sent for one question, or null while it is unanswered. Single: the typed text, else
 *  the chip. Multi: the chips, plus the typed text. Rank: the order (typed text replaces it). */
export function answerOf(q: AskItem, a: AnswerDraft | undefined): string | string[] | null {
  if (!a) return null;
  const text = q.allow_text ? a.text.trim() : "";
  if (q.rank) return text || (a.order.length ? a.order : null);
  if (q.multi) {
    const all = text ? [...a.picks, text] : a.picks;
    return all.length ? all : null;
  }
  return text || a.picks[0] || null;
}

/** {id: value} for every answered question (the core's batch answer). */
export function buildAnswers(items: readonly AskItem[], draft: InterviewDraft): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const q of items) {
    const v = answerOf(q, draft[q.id]);
    if (v !== null) out[q.id] = v;
  }
  return out;
}

export function unanswered(items: readonly AskItem[], draft: InterviewDraft): AskItem[] {
  return items.filter((q) => answerOf(q, draft[q.id]) === null);
}

// ---- a study's iterations

export interface Point { it: StudyIteration; mass: number; sf: number }

/** Iterations that can sit on the mass-vs-SF plot (both measured). */
export function plotPoints(its: readonly StudyIteration[]): Point[] {
  const out: Point[] = [];
  for (const it of its) {
    const mass = it.metrics.mass_g;
    const sf = it.metrics.min_sf;
    if (isNum(mass) && isNum(sf)) out.push({ it, mass, sf });
  }
  return out;
}

/** The front as a line: the core's pareto flags (feasible, non-dominated on mass ↓ and SF ↑),
 *  lightest first. */
export function paretoLine(points: readonly Point[]): Point[] {
  return points.filter((p) => p.it.pareto).sort((a, b) => a.mass - b.mass || b.sf - a.sf);
}

/** Non-dominated feasible points, computed here (a cross-check of the core's flags in tests). */
export function computePareto(points: readonly Point[]): Set<number> {
  const feas = points.filter((p) => p.it.feasible);
  const front = new Set<number>();
  for (const p of feas) {
    const dominated = feas.some((q) => q !== p && q.mass <= p.mass && q.sf >= p.sf && (q.mass < p.mass || q.sf > p.sf));
    if (!dominated) front.add(p.it.n);
  }
  return front;
}

export type SortKey = "n" | "mass_g" | "min_sf" | "max_disp_mm" | "feasible" | `var:${string}`;
export interface SortState { key: SortKey; dir: "asc" | "desc" }

function sortValue(it: StudyIteration, key: SortKey): number {
  if (key === "n") return it.n;
  if (key === "feasible") return (it.feasible ? 2 : 0) + (it.pareto ? 1 : 0);
  if (key.startsWith("var:")) return it.vars[key.slice(4)] ?? Number.NaN;
  const v = it.metrics[key as "mass_g" | "min_sf" | "max_disp_mm"];
  return isNum(v) ? v : Number.NaN;
}

/** Sorted copy; missing values always last, ties by n. */
export function sortIterations(its: readonly StudyIteration[], s: SortState): StudyIteration[] {
  const sign = s.dir === "asc" ? 1 : -1;
  return [...its].sort((a, b) => {
    const va = sortValue(a, s.key);
    const vb = sortValue(b, s.key);
    const na = Number.isNaN(va);
    const nb = Number.isNaN(vb);
    if (na || nb) return na === nb ? a.n - b.n : na ? 1 : -1;
    return (va - vb) * sign || a.n - b.n;
  });
}

/** Clicking a header: a new column starts in its natural direction (n asc, SF desc, else asc). */
export function nextSort(cur: SortState, key: SortKey): SortState {
  if (cur.key === key) return { key, dir: cur.dir === "asc" ? "desc" : "asc" };
  return { key, dir: key === "min_sf" || key === "feasible" ? "desc" : "asc" };
}

/** Round tick values covering [lo, hi] (about `count` of them). */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (hi <= lo) hi = lo + 1;
  const raw = (hi - lo) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

// ---- the spec editor

/** Text fields while editing (so "2." can be typed); parsed back by specFromDraft. */
export interface ReqDraft { id: string; kind: RequirementKind; target: string; load_case_id: string; priority: number; source: "user" | "agent"; touched: boolean }
export interface CaseDraft { id: string; name: string; fix: string; load: string; newtons: string; direction: LoadCase["direction"]; note: string; estimate: boolean; touched: boolean }
export interface SpecDraft { goal: string; requirements: ReqDraft[]; load_cases: CaseDraft[]; answers: Record<string, unknown> }

function targetText(kind: RequirementKind, t: unknown): string {
  if (kind === "envelope_mm" && Array.isArray(t)) return t.join(" × ");
  if (kind === "printable") return t === false ? "no" : "yes";
  if (typeof t === "string") return t;
  if (t === null || t === undefined) return "";
  return isNum(t) ? String(t) : JSON.stringify(t);
}

export function draftFromSpec(s: Spec): SpecDraft {
  return {
    goal: s.goal,
    requirements: s.requirements.map((r) => ({
      id: r.id, kind: r.kind, target: targetText(r.kind, r.target), load_case_id: r.load_case_id ?? "", priority: r.priority,
      source: r.source, touched: false,
    })),
    load_cases: s.load_cases.map((c) => ({ ...c, newtons: String(c.newtons), touched: false })),
    answers: s.answers,
  };
}

export type DraftResult = { ok: true; spec: Spec } | { ok: false; errors: string[] };

function parseTarget(r: ReqDraft): { ok: true; v: unknown } | { ok: false; why: string } {
  const t = r.target.trim();
  if (NUMERIC_KINDS.has(r.kind)) {
    const v = Number(t);
    return t !== "" && Number.isFinite(v) && v > 0 ? { ok: true, v } : { ok: false, why: "needs a positive number" };
  }
  if (r.kind === "envelope_mm") {
    const xs = t.split(/[x×*,\s]+/).filter(Boolean).map(Number);
    return xs.length === 3 && xs.every((x) => Number.isFinite(x) && x > 0) ? { ok: true, v: xs } : { ok: false, why: "needs three positive sizes, e.g. 50 × 30 × 12" };
  }
  if (r.kind === "printable") return { ok: true, v: !/^(no|false|0)$/i.test(t) };
  if (r.kind === "mount_pattern") return t ? { ok: true, v: t } : { ok: false, why: "needs a pattern, e.g. 20x20 M3" };
  // custom: JSON when it parses (e.g. {"max_drag_n": 2}), else the text
  if (!t) return { ok: true, v: null };
  try { return { ok: true, v: JSON.parse(t) as unknown }; } catch { return { ok: true, v: t }; }
}

/** Back to a Spec, or the problems to show before sending. Rows the user touched become
 *  source "user". The core re-validates everything (and answers 422 with the field). */
export function specFromDraft(d: SpecDraft): DraftResult {
  const errors: string[] = [];
  const caseIds = new Set(d.load_cases.map((c) => c.id));
  const requirements: Requirement[] = d.requirements.map((r) => {
    const t = parseTarget(r);
    if (!t.ok) errors.push(`${r.id}: ${t.why}`);
    const lc = r.load_case_id || null;
    if (LOAD_CASE_KINDS.has(r.kind) && !lc) errors.push(`${r.id}: pick a load case`);
    if (lc && !caseIds.has(lc)) errors.push(`${r.id}: unknown load case ${lc}`);
    return { id: r.id, kind: r.kind, target: t.ok ? t.v : null, load_case_id: lc, priority: r.priority, source: r.touched ? "user" : r.source };
  });
  const load_cases: LoadCase[] = d.load_cases.map((c) => {
    const n = Number(c.newtons);
    if (!(c.newtons.trim() !== "" && Number.isFinite(n) && n > 0)) errors.push(`${c.id}: force needs a positive number of newtons`);
    if (c.fix === c.load) errors.push(`${c.id}: fixed and loaded faces must differ`);
    return { id: c.id, name: c.name, fix: c.fix, load: c.load, newtons: n, direction: c.direction, note: c.note, estimate: c.estimate };
  });
  const ids = d.requirements.map((r) => r.id);
  if (new Set(ids).size !== ids.length) errors.push("requirement ids must be unique");
  return errors.length ? { ok: false, errors } : { ok: true, spec: { goal: d.goal, requirements, load_cases, answers: d.answers } };
}

/** A fresh id for a new requirement: the kind's word, then a word, never a bare number suffix
 *  (core naming forbids names ending in a number). */
export function newReqId(kind: RequirementKind, taken: ReadonlySet<string>): string {
  const base = { max_mass_g: "mass", min_safety_factor: "strength", max_deflection_mm: "stiffness", envelope_mm: "envelope", mount_pattern: "mount", printable: "printable", custom: "custom" }[kind];
  const words = ["", "_extra", "_also", "_more", "_again", "_other", "_further", "_added"];
  for (const w of words) if (!taken.has(base + w)) return base + w;
  let s = `${base}_x`;
  while (taken.has(s)) s += "x";
  return s;
}
