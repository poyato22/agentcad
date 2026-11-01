// Which agents a picker offers. "Hide" in the agent manager (the profile's hidden_agents; GET
// /agents reports it as `hidden`) takes an agent out of the agent lists, the composer and the default-agent
// picker (C-4). `keep` names an agent that stays listed even when hidden — the one a form already
// holds (the profile's saved default), so a picker never silently shows another value.
import type { AgentInfo } from "../api/types";

export function startableAgents<T extends Pick<AgentInfo, "name" | "hidden">>(list: readonly T[], keep: string | null = null): T[] {
  return list.filter((a) => !a.hidden || a.name === keep);
}
