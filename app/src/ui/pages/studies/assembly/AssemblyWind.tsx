// Wind over the whole build: the placed parts as one body in the wind solver. Set the speed and run;
// the build's wind studies are listed here and open in the immersive view (airflow, surface
// pressure). Low fidelity with the built-in solver, as every wind result says.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { StudySummary } from "../../../../api/studies";
import { actions } from "../../../../state/store";
import { Button } from "../../../common/Button";
import { sig3 } from "../model";
import { openImmersive } from "../immersive/state";

export interface AssemblyWindClient {
  listStudies(f: { projectId: string }): Promise<StudySummary[]>;
  startStudy(b: { type: "wind"; project_id: string; settings: Record<string, unknown>; reason: string }): Promise<unknown>;
}

const POLL_MS = 3000;

export function AssemblyWind({ projectId, client }: { projectId: string; client?: AssemblyWindClient }): JSX.Element {
  const api = (): AssemblyWindClient => client ?? actions.client();
  const [speed, setSpeed] = useState(20);
  const [rows, setRows] = useState<StudySummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const running = rows.some((r) => r.status === "running");
  const load = async () => { setRows((await api().listStudies({ projectId })).filter((r) => r.type === "wind")); };
  useEffect(() => { void load().catch((e: unknown) => setError(String(e))); }, [projectId]);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => { void load().catch(() => {}); }, POLL_MS);
    return () => clearInterval(t);
  }, [running, projectId]);
  const run = async () => {
    setError(null);
    try {
      await api().startStudy({ type: "wind", project_id: projectId, settings: { speed_m_s: speed }, reason: "wind over the whole build" });
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const done = rows.filter((r) => r.status === "done").map((r) => r.sim_id);
  return (
    <div class="asy-wind" data-testid="assembly-wind">
      <h3 class="label">Wind over the whole build</h3>
      <label class="asy-field">
        <span class="label">speed (m/s)</span>
        <input type="number" min={0.1} max={150} step="any" value={speed} data-testid="asy-wind-speed"
          onInput={(e) => setSpeed(Number((e.target as HTMLInputElement).value))} />
      </label>
      <Button onClick={() => { void run(); }} disabled={running || !(speed > 0)} data-testid="asy-wind-run">{running ? "Running…" : "Run wind study"}</Button>
      {error && <p class="tone-red text" role="alert">{error}</p>}
      {rows.map((r) => {
        const res = r.results as { cd?: unknown; drag_n?: unknown } | null | undefined;
        const nums = typeof res?.cd === "number" && typeof res?.drag_n === "number" ? `Cd ${sig3(res.cd)} · ${sig3(res.drag_n)} N` : r.status;
        return (
          <button key={r.sim_id} type="button" class="st-row-btn" data-testid="asy-wind-row" disabled={r.status !== "done"}
            onClick={() => openImmersive({ simId: r.sim_id, ids: done })}>Wind · {nums}</button>
        );
      })}
    </div>
  );
}
