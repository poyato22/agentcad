// Test-only control routes (bearer token required, like every other route):
//   POST /__fake/emit          {event, steps?, approval?, run?, parts?} → push an event (+ state changes)
//   POST /__fake/reset         {scenario?: name | Scenario, token?}      → reload a scenario, clear the call log
//   GET  /__fake/calls                                                    → request log of real routes
//   POST /__fake/drop_sockets  {mode?: "close"|"terminate"}              → kill every events socket
//   POST /__fake/rotate_token  {token}                                    → new token; sockets closed, reconnects get 4401
//   POST /__fake/agent_edit    {part_id, vars?, source?, run_id?, summary?} → an agent's part edit (part_changed by "agent")
//   POST /__fake/restart_core                                            → leads become unreachable (next prompt starts a new lead)
//   POST /__fake/jobs          {jobs}                                    → set the running background jobs (GET /jobs)
import type { EmitExtras, EventIn, FakeCore, Scenario } from "./server";

export async function handleControl(core: FakeCore, method: string, path: string, body: unknown): Promise<unknown> {
  const b = (typeof body === "object" && body !== null ? body : {}) as Record<string, unknown>;
  if (method === "POST" && path === "/__fake/emit") {
    const { event, ...extras } = b as { event?: EventIn } & EmitExtras;
    if (!event || typeof event.type !== "string") throw new Error("emit needs {event: {type, run_id?, data?}}");
    return { ok: true, ...core.emit(event, extras) };
  }
  if (method === "POST" && path === "/__fake/reset") {
    core.reset(b.scenario as string | Scenario | undefined, typeof b.token === "string" ? b.token : undefined);
    return { ok: true };
  }
  if (method === "GET" && path === "/__fake/calls") return core.state.calls;
  if (method === "POST" && path === "/__fake/jobs") {          // Plan 9 B3: set the running jobs
    core.state.jobs = Array.isArray(b.jobs) ? structuredClone(b.jobs) as typeof core.state.jobs : [];
    return { ok: true };
  }
  if (method === "POST" && path === "/__fake/agent_edit") return core.agentEdit(b);
  if (method === "POST" && path === "/__fake/restart_core") { core.restartCore(); return { ok: true }; }
  if (method === "POST" && path === "/__fake/drop_sockets") {
    core.dropSockets(b.mode === "terminate" ? "terminate" : "close");
    return { ok: true };
  }
  if (method === "POST" && path === "/__fake/rotate_token") {
    if (typeof b.token !== "string" || !b.token) throw new Error("rotate_token needs {token}");
    core.rotateToken(b.token);
    return { ok: true };
  }
  return undefined;
}
