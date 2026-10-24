import type { AgentRun } from "../../../api/types";
import { runLabel } from "../../../lib/names";

export type SlashName = "new" | "stop" | "stopall" | "follow" | "filter" | "clear" | "help";

export type Command =
  | { kind: "message"; target: "selected"; text: string }
  | { kind: "message"; target: { label: string }; text: string }
  | { kind: "message"; target: "all"; text: string }
  | { kind: "slash"; name: SlashName; arg: string }
  | { kind: "invalid"; reason: string };

export const SLASH_NAMES: readonly SlashName[] = ["new", "stop", "stopall", "follow", "filter", "clear", "help"];

const invalid = (reason: string): Command => ({ kind: "invalid", reason });

/**
 * Parses one command line. Deliberately there is no "approve" command: approving is only ever
 * done on the approval card, so "/approve" is rejected with a pointer to the card.
 */
export function parseCommand(line: string, runs: readonly AgentRun[]): Command {
  const text = line.trim();
  if (!text) return invalid("nothing to send");

  if (text.startsWith("/")) {
    const m = /^\/(\S*)\s*([\s\S]*)$/.exec(text);
    const name = (m?.[1] ?? "").toLowerCase();
    const arg = (m?.[2] ?? "").trim();
    if (name === "approve") return invalid("approve on the card");
    if ((SLASH_NAMES as readonly string[]).includes(name)) return { kind: "slash", name: name as SlashName, arg };
    return invalid(`unknown command /${name} — try /help`);
  }

  if (text.startsWith("@")) {
    const rest = text.slice(1);
    const all = /^all(?:\s+([\s\S]*))?$/i.exec(rest);
    if (all) {
      const body = (all[1] ?? "").trim();
      return body ? { kind: "message", target: "all", text: body } : invalid("nothing to send to @all");
    }
    const lower = rest.toLowerCase();
    let best: { key: string; run: AgentRun; label: string } | null = null;
    for (const run of runs) {
      const label = runLabel(run, runs);
      for (const key of [label.toLowerCase(), run.agent.toLowerCase()]) {
        if (lower !== key && !(lower.startsWith(key) && /\s/.test(lower.charAt(key.length)))) continue;
        if (!best || key.length > best.key.length || (key.length === best.key.length && better(run, best.run))) {
          best = { key, run, label };
        }
      }
    }
    if (!best) {
      const word = /^\S*/.exec(rest)?.[0] ?? "";
      return invalid(`no run matches @${word} — use /new to start one`);
    }
    const body = rest.slice(best.key.length).trim();
    return body ? { kind: "message", target: { label: best.label }, text: body } : invalid(`nothing to send to @${best.run.agent}`);
  }

  return { kind: "message", target: "selected", text };
}

/** Among runs of one agent, an idle run can take a message and a newer run wins. */
function better(a: AgentRun, b: AgentRun): boolean {
  const aIdle = a.status !== "running";
  const bIdle = b.status !== "running";
  if (aIdle !== bIdle) return aIdle;
  return a.created_at > b.created_at;
}
