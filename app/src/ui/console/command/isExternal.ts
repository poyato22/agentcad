import type { AgentRun } from "../../../api/types";

/** External runs (driven by an outside agent) cannot take messages: the core's persisted flag. */
export const isExternal = (run: AgentRun): boolean => run.external === true;
