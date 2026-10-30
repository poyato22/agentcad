// Agents & budgets: default agent and model (the shared model picker), how many agents may work at
// once — "Auto" by default, sized by the core from this Mac and the model ("M5 Pro · 24 GB ·
// cloud model → 3"), or a number you choose — and the Onshape calls a run may make.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { ProfilePatch } from "../../../api/types";
import { startableAgents } from "../../../lib/agents";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { ModelPicker } from "../../dialogs/ModelPicker";
import { Card, ErrorLine, SectionBox, useTask, wholeNumber, type SectionProps } from "../common";

export function AgentsSection({ data, update }: SectionProps): JSX.Element {
  const p = data.profile;
  const savedAuto = p.max_parallel_agents === "auto";
  const [agent, setAgent] = useState(p.default_agent);
  const [model, setModel] = useState<string | null>(p.default_model);
  const [auto, setAuto] = useState(savedAuto);
  const [parallel, setParallel] = useState(savedAuto ? "2" : String(p.max_parallel_agents));
  const [runCap, setRunCap] = useState(String(p.onshape_calls_run_cap));
  const task = useTask();

  // hidden agents leave the picker (C-4), except the saved default (kept so the field shows it)
  const agentNames = startableAgents(data.agents, p.default_agent).map((a) => a.name);
  if (!agentNames.includes(p.default_agent)) agentNames.unshift(p.default_agent);
  const par = data.status?.parallel;
  // the core's limit is "N on this Mac" only when it picked it; a manual limit reads "Manual: N"
  const autoLabel = par?.auto ? `Auto (${par.limit} on this Mac)` : "Auto";

  const nParallel = auto ? null : wholeNumber(parallel, 1, 16);
  const nRunCap = wholeNumber(runCap, 0);
  const valid = (auto || nParallel !== null) && nRunCap !== null;
  const patch: ProfilePatch = {};
  if (agent !== p.default_agent) patch.default_agent = agent;
  if (model !== p.default_model) patch.default_model = model;
  const nextParallel = auto ? "auto" : nParallel;
  if (nextParallel !== null && nextParallel !== p.max_parallel_agents) patch.max_parallel_agents = nextParallel;
  if (nRunCap !== null && nRunCap !== p.onshape_calls_run_cap) patch.onshape_calls_run_cap = nRunCap;
  const dirty = Object.keys(patch).length > 0;

  async function save(): Promise<void> {
    await task.run(async () => {
      const next = await actions.client().updateProfile(patch);
      update({ profile: next });
      setAgent(next.default_agent);
      setModel(next.default_model);
      setAuto(next.max_parallel_agents === "auto");
      if (next.max_parallel_agents !== "auto") setParallel(String(next.max_parallel_agents));
      setRunCap(String(next.onshape_calls_run_cap));
      // the limit the core now uses (and why) comes from /status; a failed read keeps the old one
      const st = await actions.client().status().catch(() => null);
      if (st) update({ status: st });
    }, "saved");
  }
  const edit = (set: (v: string) => void) => (e: { currentTarget: { value: string } }): void => { set(e.currentTarget.value); task.clear(); };

  return (
    <SectionBox title="Agents & budgets">
      <Card title="Defaults for new runs">
        <label class="profile-field"><span class="label">Default agent</span>
          <select aria-label="Default agent" value={agent} onChange={edit(setAgent)}>
            {agentNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <div class="profile-field">
          <ModelPicker label="Default model" value={model} placeholder="App default" onChange={(ref) => { setModel(ref); task.clear(); }} />
          {model !== null && <Button variant="ghost" onClick={() => { setModel(null); task.clear(); }}>Reset</Button>}
        </div>
      </Card>
      <Card title="Limits">
        <fieldset class="profile-field" style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "3px" }}>
          <legend class="label">Parallel agents</legend>
          <label><input type="radio" name="parallel" checked={auto} onChange={() => { setAuto(true); task.clear(); }} /> {autoLabel}</label>
          {par?.auto && par.reason && <div class="profile-hint text">{par.reason}</div>}
          {par && !par.auto && <div class="profile-hint">{`Manual: ${par.limit}`}</div>}
          {!par && <div class="profile-hint">Set from this Mac's chip, memory and the model.</div>}
          <label><input type="radio" name="parallel" checked={!auto} onChange={() => { setAuto(false); task.clear(); }} /> Manual</label>
          {!auto && (
            <input aria-label="Max parallel agents" type="number" min="1" max="16" step="1" value={parallel} onInput={edit(setParallel)} />
          )}
        </fieldset>
        <label class="profile-field"><span class="label">Onshape calls per run</span>
          <input aria-label="Onshape calls per run" type="number" min="0" step="1" value={runCap} onInput={edit(setRunCap)} />
        </label>
        {!valid && <div class="profile-hint">Whole numbers: parallel agents 1–16, calls per run 0 or more.</div>}
      </Card>
      <div class="profile-row">
        <Button variant="ink" disabled={!dirty || !valid || task.busy} onClick={() => { void save(); }}>Save</Button>
        {task.note && <span class="dim">{task.note}</span>}
      </div>
      <ErrorLine error={task.error} />
    </SectionBox>
  );
}
