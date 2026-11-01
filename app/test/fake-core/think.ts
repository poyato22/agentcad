// Fake core: Plan 11 F8 (core effort.py, server/effort_api.py). GET/PUT /projects/{id}/effort hold
// a build's default Think choice ({default, choices, levels}, the real core's shape: fixtures
// core/tests/fixtures/ui/p11c); POST /projects/{id}/chat validates `effort` like ChatBody (422 for
// anything else) and, for a build that has a lead, records the turn's effort decision step just
// before the user message (the real core records one on every turn; the fake only when the
// message carries a pick, so older scenarios keep their threads).
import { HttpError, mises, type Route, type StepIn } from "./server";

const CHOICES = ["auto", "normal", "hard", "max"] as const;
const LEVEL: Record<string, string | null> = { auto: null, normal: "medium", hard: "high", max: "max" };
const LABEL: Record<string, string> = { medium: "Normal", high: "Hard", max: "Max" };

function bad(field: string, input: unknown): HttpError {
  return new HttpError(422, { detail: [{ type: "literal_error", loc: ["body", field], msg: "Input should be 'auto', 'normal', 'hard' or 'max'", input, ctx: { expected: "'auto', 'normal', 'hard' or 'max'" } }] });
}

const view = (choice: string) => ({ default: choice, choices: [...CHOICES], levels: { ...LEVEL } });

export function thinkRoutes(defaults: Map<string, string>, liveBuild: (id: string) => boolean): Route[] {
  const build = (m: RegExpMatchArray): string => {
    const id = decodeURIComponent(m[1]);
    if (!liveBuild(id)) throw mises("not_found", `unknown build: ${id}`);
    return id;
  };
  return [
    { method: "GET", re: /^\/projects\/([^/]+)\/effort$/, h: (m) => view(defaults.get(build(m)) ?? "auto") },
    {
      method: "PUT", re: /^\/projects\/([^/]+)\/effort$/, h: (m, _q, raw) => {
        const id = build(m);
        const v = (raw as Record<string, unknown> | null)?.default;
        if (typeof v !== "string" || !(CHOICES as readonly string[]).includes(v)) throw bad("default", v);
        if (v === "auto") defaults.delete(id); else defaults.set(id, v);
        return view(v);
      },
    },
  ];
}

/** The chat body's `effort`: validated; a pick on a build with a lead is recorded on it. */
export function thinkOnChat(body: Record<string, unknown>, leadId: string | null, append: (s: StepIn, runId: string) => unknown): void {
  const e = body.effort;
  if (e === undefined || e === null) return;
  if (typeof e !== "string" || !(CHOICES as readonly string[]).includes(e)) throw bad("effort", e);
  const level = LEVEL[e];
  if (!leadId || !level) return;
  append({ kind: "decision", payload: { effort: {
    level, choice: e, source: "user", reasons: [`the user picked ${LABEL[level]}`], label: LABEL[level],
    control: { runtime: "claude-code", param: "--effort / apply_flag_settings.effortLevel", value: level },
  } } }, leadId);
}
