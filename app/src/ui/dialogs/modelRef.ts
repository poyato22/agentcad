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

/** The agent's own model unless it says "inherit", else the core's default. */
export function defaultModelRef(agent: AgentInfo, models: ModelsInfo): string {
  return agent.model && agent.model !== "inherit" ? agent.model : models.default;
}
