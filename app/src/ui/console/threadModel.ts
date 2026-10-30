// Pure model of the conversation view: one run's log items (logModel.buildLog, run scope,
// everything) → a thread of user messages, agent replies and compact activity lines
// ("measured mount plate · 8.0 cm³"). Nothing safety-relevant is dropped: every approval and
// question stays a card, every failed call and error stays its own (red) line with its hint;
// only successful calls fold (logModel's rule). Text stays plain strings (rendered as text).
import { pinOf, type FeedbackPin } from "../../api/measure";
import type { Approval, Question, Step } from "../../api/types";
import { foldKey, itemKey, type LogItem } from "./logModel";
import { g4Announcement, type CritiqueRound, type PresentedView } from "./cards/critique/announce";

type Call = Extract<LogItem, { kind: "call" }>;
type Diff = Extract<LogItem, { kind: "diff" }>;

export type ThreadItem =
  | {
      kind: "user"; key: string; text: string; ts: number; stepIds: number[];
      /** A comment pinned on the model (U2a/U3c): shown as the words plus the pin chip. */
      feedback?: FeedbackPin | null;
    }
  | { kind: "agent"; key: string; text: string; ts: number | null; live: boolean; stepIds: number[] }
  | {
      kind: "activity"; key: string; text: string; tone: "ok" | "running" | "fail"; ts: number;
      /** The tool call behind the line (raw input/output on expand), if any. */
      call: Call | null;
      /** The part change it made (diff box on expand). */
      diff: Diff | null;
      /** Other log rows folded into this line (decision / file rows), shown raw on expand. */
      extra: LogItem[];
      hint: string | null;
      /** Steps this line stands for (evidence links land here). */
      stepIds: number[];
    }
  | { kind: "fold"; key: string; text: string; foldKey: string; count: number }
  | { kind: "error"; key: string; code: string; message: string; hint: string | null; ts: number; stepIds: number[] }
  | { kind: "note"; key: string; text: string; stepIds: number[] }
  | { kind: "approval"; key: string; approval: Approval }
  | { kind: "question"; key: string; question: Question }
  // Plan 8 (G4): a render-and-critique round, and a view an agent presents (with the other views of
  // the same reply, which ⌘[ / ⌘] step through in the immersive view)
  | { kind: "critique"; key: string; round: CritiqueRound; stepIds: number[] }
  | { kind: "view"; key: string; view: PresentedView; group: PresentedView[]; stepIds: number[] };

const PREFIXES = ["mcp__mises__"];

function bareName(name: string): string {
  let n = name;
  for (const p of PREFIXES) if (n.startsWith(p)) n = n.slice(p.length);
  return n;
}

function str(v: unknown): string | null { return typeof v === "string" && v.trim() !== "" ? v : null; }
function rec(v: unknown): Record<string, unknown> { return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {}; }
function partName(id: string | null): string | null { return id ? id.replace(/[_-]+/g, " ").trim() : null; }
function firstLine(s: string, max = 80): string {
  const l = s.split("\n")[0] ?? "";
  return l.length > max ? `${l.slice(0, max - 1)}…` : l;
}
function cm3(mm3: number): string { return `${(mm3 / 1000).toFixed(1)} cm³`; }
function signedCm3(mm3: number): string { return `${mm3 >= 0 ? "+" : "−"}${(Math.abs(mm3) / 1000).toFixed(1)} cm³`; }
function num(v: number): string { return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3))); }

/** [past, in progress] per tool; the part name goes after. */
const VERBS: Record<string, [string, string]> = {
  mirror_measure: ["measured", "measuring"],
  mirror_part_create: ["created", "creating"],
  mirror_part_set_vars: ["changed", "changing"],
  mirror_export: ["exported", "exporting"],
};

/** The one line a tool call reads as in the conversation. */
export function activityText(call: Call, diff: Diff | null): string {
  const name = bareName(call.name);
  const out = call.result ? rec(call.result.payload.output) : {};
  const data = rec(out.data);
  const part = partName(str(call.input.part_id) ?? str(data.part_id) ?? diff?.partId ?? null);
  const running = call.result === null;
  const failed = call.ok === false;
  const withFail = (what: string) => (failed ? `${what} failed${call.summary ? ` · ${firstLine(call.summary)}` : ""}` : what);

  const verbs = VERBS[name];
  if (verbs) {
    const what = `${running || failed ? verbs[1] : verbs[0]}${part ? ` ${part}` : ""}`;
    if (running || failed) return withFail(what);
    const bits = [what];
    if (name === "mirror_part_set_vars") {
      const vars = diff?.vars.length ? diff.vars : Object.entries(rec(call.input.vars)).filter(([, v]) => typeof v === "number")
        .map(([k, v]) => ({ name: k, prev: null, now: v as number }));
      for (const v of vars) bits.push(v.prev !== null && v.prev !== v.now ? `${v.name} ${num(v.prev)} → ${num(v.now)}` : `${v.name} ${num(v.now)}`);
      const dv = diff?.delta.volume_mm3 ?? (typeof rec(data.delta).volume_mm3 === "number" ? rec(data.delta).volume_mm3 as number : undefined);
      if (typeof dv === "number") bits.push(signedCm3(dv));
    } else if (typeof data.volume_mm3 === "number") {
      bits.push(cm3(data.volume_mm3));
    }
    return bits.join(" · ");
  }
  if (name === "mirror_part_list") return withFail(running || failed ? "listing parts" : "listed parts");
  if (name.startsWith("onshape_propose")) return withFail(running || failed ? "proposing a change on an Onshape branch" : "proposed change on Onshape branch");
  if (/merge/.test(name)) return withFail(running || failed ? `asking to merge${part ? ` ${part}` : ""}` : `merged${part ? ` ${part}` : ""}`);
  if (name === "agent_spawn") {
    const who = str(call.input.agent) ?? "a helper agent";
    return withFail(running || failed ? `starting ${who}` : `started ${who}`);
  }
  const human = name.replace(/_+/g, " ").trim() || "tool";
  if (running) return `${human} · running`;
  if (failed) return withFail(human);
  return call.summary ? `${human} · ${firstLine(call.summary)}` : human;
}

function callSteps(c: Call): number[] {
  const ids: number[] = [];
  if (c.result) ids.push(c.result.id);
  for (const s of c.attached) ids.push(s.id);
  return ids;
}

/** A decision step with nothing to say: Claude Code's {claude_code: "session_started" | "init"}
 *  or a stored {agent_thinking} record, without a reason. */
export function isBookkeeping(step: Step): boolean {
  const p = step.payload;
  if (step.kind !== "decision" || str(p.reason) || str(rec(p.output).summary)) return false;
  // a feedback pin's stored record: the user's message step (or the queue) shows it in the thread
  return typeof p.claude_code === "string" || "agent_thinking" in p || pinOf(p) !== null;
}

function decisionText(step: Step): string {
  const p = step.payload;
  if (step.kind === "plan") {
    const entries = Array.isArray(p.entries) ? p.entries : [];
    const t = entries.map((e) => str(rec(e).content) ?? "").filter(Boolean).join(" · ");
    return t ? `plan · ${firstLine(t, 120)}` : "plan";
  }
  const reason = str(p.reason) ?? str(rec(p.output).summary);
  return reason ? `decided · ${firstLine(reason)}` : "decided";
}

/** One run's log items (run scope, every kind) → the conversation thread. */
export function buildThread(items: readonly LogItem[]): ThreadItem[] {
  const out: ThreadItem[] = [];
  let last: Extract<ThreadItem, { kind: "activity" }> | null = null;
  const lastCallResult = (): number | null => last?.call?.result?.id ?? null;
  let views: PresentedView[] | null = null;

  for (const it of items) {
    const key = itemKey(it);
    switch (it.kind) {
      case "message":
        if (it.role === "user") views = null;   // the reply to it gets its own set of presented views
        out.push(it.role === "user"
          ? { kind: "user", key, text: it.text, ts: it.step.ts, stepIds: [it.step.id], feedback: pinOf(it.step.payload) }
          : { kind: "agent", key, text: it.text, ts: it.step.ts, live: false, stepIds: [it.step.id] });
        last = null;
        break;
      case "live":
        out.push({ kind: "agent", key, text: it.text, ts: null, live: true, stepIds: [] });
        last = null;
        break;
      case "call": {
        const a: Extract<ThreadItem, { kind: "activity" }> = {
          kind: "activity", key, text: activityText(it, null), tone: it.ok === false ? "fail" : it.result === null ? "running" : "ok",
          ts: it.startedTs, call: it, diff: null, extra: [], hint: it.ok === false ? it.hint : null, stepIds: callSteps(it),
        };
        out.push(a);
        last = a;
        break;
      }
      case "diff": {
        // the diff of the call just before (its result step): one line, not two
        if (last && last.call && lastCallResult() === it.step.id) {
          last.diff = it;
          last.text = activityText(last.call, it);
          break;
        }
        const part = partName(it.partId);
        const bits = [`changed${part ? ` ${part}` : ""}`];
        for (const v of it.vars) bits.push(v.prev !== null && v.prev !== v.now ? `${v.name} ${num(v.prev)} → ${num(v.now)}` : `${v.name} ${num(v.now)}`);
        if (typeof it.delta.volume_mm3 === "number") bits.push(signedCm3(it.delta.volume_mm3));
        const a: Extract<ThreadItem, { kind: "activity" }> = {
          kind: "activity", key, text: bits.join(" · "), tone: "ok", ts: it.step.ts, call: null, diff: it, extra: [], hint: null, stepIds: [it.step.id],
        };
        out.push(a);
        last = a;
        break;
      }
      case "decision": {
        const g4 = g4Announcement(it.step);
        if (g4?.kind === "critique_round") { out.push({ kind: "critique", key, round: g4.round, stepIds: [it.step.id] }); last = null; break; }
        if (g4?.kind === "view_presented") {
          const view: PresentedView = { ...g4.view, key };
          views ??= [];
          views.push(view);
          out.push({ kind: "view", key, view, group: views, stepIds: [it.step.id] });
          last = null;
          break;
        }
        // U3c's other announcements (part created, agent spawned, added to the assembly or the
        // BOM, part shown, drawing imported): the acting run's own thread already shows the call
        // (and U4a's part card for part_shown), so only a copy on the lead from a sub-agent is a
        // line here; its title is core text (ids and counts), shown as text
        const ann = rec(it.step.payload.agent_announce);
        if (typeof ann.kind === "string") {
          if ((str(ann.run_id) ?? it.step.run_id) !== it.step.run_id && str(ann.title)) {
            out.push({ kind: "note", key, text: firstLine(str(ann.title) as string), stepIds: [it.step.id] });
            last = null;
          }
          break;
        }
        if (last && last.call && lastCallResult() === it.step.id) { last.extra.push(it); break; }
        // Claude Code's bookkeeping (session start, init, stored thinking) is not a decision the
        // reader needs; it stays in the step log (C-10)
        if (isBookkeeping(it.step)) break;
        out.push({ kind: "note", key, text: decisionText(it.step), stepIds: [it.step.id] });
        last = null;
        break;
      }
      case "file": {
        const a: Extract<ThreadItem, { kind: "activity" }> = {
          kind: "activity", key, text: `edited ${it.path} · +${it.added} −${it.removed}`, tone: "ok", ts: it.step.ts, call: null, diff: null,
          extra: [it], hint: null, stepIds: [it.step.id],
        };
        out.push(a);
        last = a;
        break;
      }
      case "fold": {
        const n = it.calls.length;
        const names = [...new Set(it.calls.map((c) => bareName(c.name)))].slice(0, 3);
        out.push({ kind: "fold", key, text: `${n} more step${n === 1 ? "" : "s"}${names.map((x) => ` · ${x}`).join("")}`, foldKey: foldKey(it), count: n });
        last = null;
        break;
      }
      case "error":
        out.push({ kind: "error", key, code: it.code, message: it.message, hint: it.hint, ts: it.step.ts, stepIds: [it.step.id] });
        last = null;
        break;
      case "note":
        out.push({ kind: "note", key, text: it.text, stepIds: it.stepId !== null ? [it.stepId] : [] });
        last = null;
        break;
      case "approval":
        out.push({ kind: "approval", key, approval: it.approval });
        last = null;
        break;
      case "question":
        out.push({ kind: "question", key, question: it.question });
        last = null;
        break;
    }
  }
  return out;
}
