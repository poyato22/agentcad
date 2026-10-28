import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import type { StepProps } from "../SetupWizard";
import { describeError, StepHead } from "./common";

export function DoneStep({ data, refresh, go, close }: StepProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const s = data.setup;

  async function finish(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await actions.client().completeSetup();
      await refresh().catch(() => undefined);
      close();
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  return (
    <div class="setup-step">
      <StepHead title="Ready" />
      <ul class="setup-summary">
        <li class="setup-status"><Led tone={s.has_model ? "green" : "faint"} />
          <span>{s.has_model ? "a model is connected" : "local models only — connect a model any time in Profile"}</span></li>
        <li class="setup-status"><Led tone={s.has_onshape ? "green" : "faint"} />
          <span>{s.has_onshape ? "Onshape keys saved" : "Onshape skipped — add keys any time in Profile"}</span></li>
        <li class="setup-status"><Led tone={data.profile.display_name ? "green" : "faint"} />
          <span class="text">{data.profile.display_name ? `profile · ${data.profile.display_name}` : "profile defaults"}</span></li>
      </ul>
      {error && <div class="setup-error text" role="alert">{error}</div>}
      <div class="setup-actions">
        <Button variant="ghost" onClick={() => go("onshape")}>Back</Button>
        <span class="setup-spacer" />
        <Button variant="ink" disabled={busy} onClick={() => { void finish(); }}>Start using Mises</Button>
      </div>
    </div>
  );
}
