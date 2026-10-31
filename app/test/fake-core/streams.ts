// Plan 9 D4: the stream an event or step belongs to. Same rules as core events.stream_of.
import type { StreamKind as Stream } from "../../src/api/types";

const SIM_TOOLS = new Set(["design_review", "design_review_get", "study_run", "study_get", "job_wait"]);
const VIEW_TOOLS = new Set(["render_views", "present_view", "critique_model", "part_show", "section_check", "assembly_animate"]);
const SIM_EVENTS = new Set(["sim_progress", "sim_finished", "study_progress"]);
const VIEW_EVENTS = new Set(["part_shown", "assembly_animate"]);
/** Announcement cards that are pictures of the model (core events.VIEW_ANNOUNCE). */
const VIEW_ANNOUNCE = new Set(["view_presented", "critique_round", "part_shown"]);

export function toolStream(name: string): Stream {
  const bare = name.split("mcp__mises__").pop() ?? name;
  if (bare.startsWith("sim_") || SIM_TOOLS.has(bare)) return "sim";
  return VIEW_TOOLS.has(bare) ? "view" : "text";
}

/** An event's type (or a step's kind) and its data (or payload). */
export function streamOf(type: string, data: Record<string, unknown> | null | undefined): Stream {
  if (SIM_EVENTS.has(type) || type === "sim_run") return "sim";
  if (VIEW_EVENTS.has(type) || type.startsWith("view_")) return "view";
  const card = type === "agent_announce" ? data : data?.agent_announce;
  if (card && typeof card === "object" && VIEW_ANNOUNCE.has(String((card as { kind?: unknown }).kind))) return "view";
  const name = data?.name;
  return typeof name === "string" ? toolStream(name) : "text";
}
