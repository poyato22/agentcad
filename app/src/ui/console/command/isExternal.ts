import type { AgentRun } from "../../../api/types";

/** External (ACP-driven) runs cannot take messages; the core creates them with provider "acp". */
export const isExternal = (run: AgentRun): boolean => run.provider === "acp";
