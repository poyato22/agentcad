// Your profile (the first real step): display name, units, default material, theme. This is what
// "signing in" to Mises sets up — there is no account; it's saved on this Mac. Models come next;
// how many agents run at once is automatic (Profile → Agents & budgets to override).
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { Profile } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import type { StepProps } from "../SetupWizard";
import { describeError, StepHead } from "./common";

const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "Nylon"];
type Theme = NonNullable<Profile["theme"]>;
const THEMES: { id: Theme; label: string }[] = [{ id: "system", label: "Match this Mac" }, { id: "light", label: "Light" }, { id: "dark", label: "Dark" }];

export function ProfileStep({ data, update, refresh, go }: StepProps): JSX.Element {
  const p = data.profile;
  const [name, setName] = useState(p.display_name);
  const [material, setMaterial] = useState(p.default_material);
  const [theme, setTheme] = useState<Theme>(p.theme ?? "system");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = !!name.trim() && !!material.trim() && !busy;

  async function save(): Promise<void> {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const profile = await actions.client().updateProfile({ display_name: name.trim(), default_material: material.trim(), theme });
      update({ profile });
      await refresh().catch(() => undefined);
      go("model");
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  return (
    <div class="setup-step">
      <StepHead title="Your profile" sub="This is what signing in to Mises sets up. There's no account: it's saved on this Mac, and you can change it later in Profile." />
      <div class="setup-card setup-form">
        <label class="setup-field"><span class="label">Display name</span>
          <input aria-label="Display name" value={name} spellcheck={false} onInput={(e) => setName(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />
        </label>
        <label class="setup-field"><span class="label">Units</span>
          <select aria-label="Units" value="mm" disabled={busy}>
            <option value="mm">millimetres (mm)</option>
          </select>
        </label>
        <label class="setup-field"><span class="label">Default material</span>
          <input aria-label="Default material" list="setup-materials" value={material} spellcheck={false} onInput={(e) => setMaterial(e.currentTarget.value)} />
          <datalist id="setup-materials">{MATERIALS.map((m) => <option key={m} value={m} />)}</datalist>
        </label>
        <label class="setup-field"><span class="label">Theme</span>
          <select aria-label="Theme" value={theme} onChange={(e) => setTheme(e.currentTarget.value as Theme)}>
            {THEMES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </label>
        {error && <div class="setup-error text" role="alert">{error}</div>}
      </div>
      <div class="setup-actions">
        <Button variant="ghost" onClick={() => go("welcome")}>Back</Button>
        <span class="setup-spacer" />
        <Button variant="ink" disabled={!ready} onClick={() => { void save(); }}>Save and continue</Button>
      </div>
    </div>
  );
}
