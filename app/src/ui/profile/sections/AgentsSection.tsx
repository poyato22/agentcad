import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { ProfilePatch } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { availableModels } from "../../setup/logic";
import { Card, ErrorLine, SectionBox, useTask, wholeNumber, type SectionProps } from "../common";

export function AgentsSection({ data, update }: SectionProps): JSX.Element {
  const p = data.profile;
  const [agent, setAgent] = useState(p.default_agent);
  const [model, setModel] = useState(p.default_model ?? "");
  const [parallel, setParallel] = useState(String(p.max_parallel_agents));
  const [runCap, setRunCap] = useState(String(p.onshape_calls_run_cap));
  const task = useTask();

  const agentNames = data.agents.map((a) => a.name);
  if (!agentNames.includes(p.default_agent)) agentNames.unshift(p.default_agent);
  const usable = data.models ? availableModels(data.models, p) : [];
  const modelOptions = [...usable];
  if (p.default_model && !usable.some((m) => m.ref === p.default_model)) {
    modelOptions.push({ ref: p.default_model, label: `${p.default_model} (not available now)` });
  }

  const nParallel = wholeNumber(parallel, 1, 16);
  const nRunCap = wholeNumber(runCap, 0);
  const valid = nParallel !== null && nRunCap !== null;
  const patch: ProfilePatch = {};
  if (agent !== p.default_agent) patch.default_agent = agent;
  if (model !== (p.default_model ?? "")) patch.default_model = model === "" ? null : model;
  if (nParallel !== null && nParallel !== p.max_parallel_agents) patch.max_parallel_agents = nParallel;
  if (nRunCap !== null && nRunCap !== p.onshape_calls_run_cap) patch.onshape_calls_run_cap = nRunCap;
  const dirty = Object.keys(patch).length > 0;

  async function save(): Promise<void> {
    await task.run(async () => {
      const next = await actions.client().updateProfile(patch);
      update({ profile: next });
      setAgent(next.default_agent);
      setModel(next.default_model ?? "");
      setParallel(String(next.max_parallel_agents));
      setRunCap(String(next.onshape_calls_run_cap));
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
        <label class="profile-field"><span class="label">Default model</span>
          <select aria-label="Default model" value={model} onChange={edit(setModel)}>
            <option value="">App default</option>
            {modelOptions.map((m) => <option key={m.ref} value={m.ref}>{m.label}</option>)}
          </select>
        </label>
      </Card>
      <Card title="Limits">
        <label class="profile-field"><span class="label">Max parallel agents (1–16)</span>
          <input aria-label="Max parallel agents" type="number" min="1" max="16" step="1" value={parallel} onInput={edit(setParallel)} />
        </label>
        <label class="profile-field"><span class="label">Onshape calls per run</span>
          <input aria-label="Onshape calls per run" type="number" min="0" step="1" value={runCap} onInput={edit(setRunCap)} />
        </label>
        {!valid && <div class="profile-hint">Use whole numbers: parallel agents 1 to 16, calls per run 0 or more.</div>}
      </Card>
      <div class="profile-row">
        <Button variant="ink" disabled={!dirty || !valid || task.busy} onClick={() => { void save(); }}>Save</Button>
        {task.note && <span class="dim">{task.note}</span>}
      </div>
      <ErrorLine error={task.error} />
    </SectionBox>
  );
}
