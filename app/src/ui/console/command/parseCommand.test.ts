import { describe, expect, it } from "vitest";
import type { AgentRun } from "../../../api/types";
import { parseCommand } from "./parseCommand";

function run(id: string, agent: string, task: string, status: AgentRun["status"] = "done", created = 1000): AgentRun {
  return { id, parent_id: null, agent, provider: "fake", model: "m", task, status, branch: null, onshape_calls: 0,
    input_tokens: 0, output_tokens: 0, usd: 0, created_at: created, updated_at: created };
}
const runs = [run("r1", "designer", "stiffen upright", "running"), run("r2", "planner", "plan the bracket")];

describe("parseCommand", () => {
  it("parse_plain_targets_selected", () => {
    expect(parseCommand("make it thicker", runs)).toEqual({ kind: "message", target: "selected", text: "make it thicker" });
  });
  it("parse_at_label", () => {
    expect(parseCommand("@designer make it 6 mm", runs)).toEqual({ kind: "message", target: { label: "designer · stiffen upright" }, text: "make it 6 mm" });
    expect(parseCommand("@PLANNER again", runs)).toEqual({ kind: "message", target: { label: "planner · plan bracket" }, text: "again" });
    expect(parseCommand("@planner · plan bracket again", runs)).toEqual({ kind: "message", target: { label: "planner · plan bracket" }, text: "again" });
  });
  it("parse_at_all", () => {
    expect(parseCommand("@all stop and summarize", runs)).toEqual({ kind: "message", target: "all", text: "stop and summarize" });
  });
  it("parse_unknown_at_is_invalid", () => {
    expect(parseCommand("@ghost hi", runs).kind).toBe("invalid");
    expect(parseCommand("@planners hi", runs).kind).toBe("invalid");
    expect(parseCommand("@designer", runs).kind).toBe("invalid");
    expect(parseCommand("@all", runs).kind).toBe("invalid");
    expect(parseCommand("   ", runs).kind).toBe("invalid");
  });
  it("parse_slash_commands", () => {
    for (const name of ["new", "stop", "stopall", "follow", "filter", "clear", "help"] as const) {
      expect(parseCommand(`/${name}`, runs)).toEqual({ kind: "slash", name, arg: "" });
    }
    expect(parseCommand("/filter  errors ", runs)).toEqual({ kind: "slash", name: "filter", arg: "errors" });
    expect(parseCommand("/bogus", runs).kind).toBe("invalid");
  });
  it("no_approve_command", () => {
    expect(parseCommand("/approve", runs)).toEqual({ kind: "invalid", reason: "approve on the card" });
    expect(parseCommand("/APPROVE abc", runs)).toEqual({ kind: "invalid", reason: "approve on the card" });
  });
  it("multiline_input_is_never_a_command", () => {
    expect(parseCommand("/stopall\nrest", runs)).toEqual({ kind: "message", target: "selected", text: "/stopall\nrest" });
    expect(parseCommand("/new\n/stop", runs).kind).toBe("message");
  });
  it("no_arg_commands_reject_arguments", () => {
    for (const n of ["stop", "stopall", "new", "clear"]) expect(parseCommand(`/${n} now`, runs).kind).toBe("invalid");
    expect(parseCommand("/follow off", runs).kind).toBe("slash");
  });
  it("same_agent_prefers_idle_run", () => {
    const two = [run("a", "designer", "one", "running", 2000), run("b", "designer", "two", "done", 1000)];
    expect(parseCommand("@designer go", two)).toMatchObject({ target: { label: "designer · two" } });
  });
});
