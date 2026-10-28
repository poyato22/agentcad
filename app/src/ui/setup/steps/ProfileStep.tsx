// Profile basics: display name, default material, default model (only ones that work now), parallel agents.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { availableModels } from "../logic";
import { LOCAL_APPS } from "./ModelStep";
import type { StepProps } from "../SetupWizard";
import { describeError, StepHead } from "./common";

const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "Nylon"];
const AGENTS = [1, 2, 3, 4];

/** Local servers the core finds answering at their default URL (nothing saved for them yet). */
export function useDetectedLocal(data: StepProps["data"]): string[] {
  const [found, setFound] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    const unsaved = LOCAL_APPS.filter((a) => !(a.vendor in data.profile.local_endpoints)
      && data.models.vendors.some((v) => v.vendor === a.vendor && v.key === "not_needed"));
    void Promise.all(unsaved.map((a) => actions.client().testLocal(a.url).then((r) => (r.ok ? a.vendor : null), () => null)))
      .then((vs) => { if (live) setFound(vs.filter((v): v is (typeof LOCAL_APPS)[number]["vendor"] => v !== null)); });
    return () => { live = false; };
  }, []);
  return found;
}

export function ProfileStep({ data, update, refresh, go }: StepProps): JSX.Element {
  const p = data.profile;
  const [name, setName] = useState(p.display_name);
  const [material, setMaterial] = useState(p.default_material);
  const [model, setModel] = useState(p.default_model ?? "");
  const [agents, setAgents] = useState<number | "auto">(
    typeof p.max_parallel_agents === "number" ? Math.min(4, Math.max(1, p.max_parallel_agents)) : "auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detected = useDetectedLocal(data);
  const options = availableModels(data.models, p, detected);
  const ready = !!name.trim() && !!material.trim() && !busy;

  async function save(): Promise<void> {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const profile = await actions.client().updateProfile({
        display_name: name.trim(), default_material: material.trim(), default_model: model || null, max_parallel_agents: agents,
      });
      update({ profile });
      await refresh().catch(() => undefined);
      go("done");
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  return (
    <div class="setup-step">
      <StepHead title="Profile basics" sub="Defaults for new runs. All of it can be changed later in Profile." />
      <div class="setup-card setup-form">
        <label class="setup-field"><span class="label">Display name</span>
          <input aria-label="Display name" value={name} spellcheck={false} onInput={(e) => setName(e.currentTarget.value)} />
        </label>
        <label class="setup-field"><span class="label">Default material</span>
          <input aria-label="Default material" list="setup-materials" value={material} spellcheck={false} onInput={(e) => setMaterial(e.currentTarget.value)} />
          <datalist id="setup-materials">{MATERIALS.map((m) => <option key={m} value={m} />)}</datalist>
        </label>
        <label class="setup-field"><span class="label">Default model</span>
          <select aria-label="Default model" value={model} onChange={(e) => setModel(e.currentTarget.value)}>
            <option value="">agent default</option>
            {options.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}
            {model && !options.some((o) => o.ref === model) && <option value={model}>{`${model} · not available now`}</option>}
          </select>
        </label>
        {options.length === 0 && <div class="faint">no connected model yet — runs use the agent's default</div>}
        <label class="setup-field"><span class="label">Parallel agents</span>
          <select aria-label="Parallel agents" value={String(agents)} onChange={(e) => { const v = e.currentTarget.value; setAgents(v === "auto" ? "auto" : Number(v)); }}>
            <option value="auto">Auto</option>
            {AGENTS.map((n) => <option key={n} value={String(n)}>{n}</option>)}
          </select>
        </label>
        {error && <div class="setup-error text" role="alert">{error}</div>}
      </div>
      <div class="setup-actions">
        <Button variant="ghost" onClick={() => go("onshape")}>Back</Button>
        <span class="setup-spacer" />
        <Button variant="ink" disabled={!ready} onClick={() => { void save(); }}>Save and continue</Button>
      </div>
    </div>
  );
}
