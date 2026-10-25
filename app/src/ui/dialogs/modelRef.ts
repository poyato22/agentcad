import type { AgentInfo, ModelsInfo } from "../../api/types";

/** "anthropic" + "claude-sonnet-5-5" → "anthropic/claude-sonnet-5-5". */
export function toModelRef(vendor: string, modelId: string): string {
  return `${vendor}/${modelId}`;
}

/** Inverse of toModelRef: splits on the first "/" (model ids may contain more, e.g. openrouter). */
export function splitModelRef(ref: string): { vendor: string; model: string } {
  const i = ref.indexOf("/");
  if (i <= 0) return { vendor: "", model: ref };
  return { vendor: ref.slice(0, i), model: ref.slice(i + 1) };
}

/** Same precedence as the core (Runtime.model_ref_for): the agent's own model unless it says
 *  "inherit", else the profile's default model, else the core's default. */
export function defaultModelRef(agent: AgentInfo, models: ModelsInfo, profileDefault: string | null = null): string {
  if (agent.model && agent.model !== "inherit") return agent.model;
  return profileDefault || models.default;
}
