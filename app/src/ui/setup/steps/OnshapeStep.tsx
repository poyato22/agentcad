// Onshape (optional): access key + secret key → Keychain via the core, then one test call.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { OnshapeTestResult } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { setSkipped } from "../gate";
import { onshapeTestText as testText } from "../logic";
import type { StepProps } from "../SetupWizard";
import { Card, describeError, KeyLine, SECRET_INPUT, StepHead } from "./common";

export function OnshapeStep({ data, update, refresh, go }: StepProps): JSX.Element {
  const [access, setAccess] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OnshapeTestResult | null>(null);
  const status = data.keys.find((k) => k.vendor === "onshape");
  const canSave = !!access.trim() && !!secretKey.trim() && !busy;

  async function save(): Promise<void> {
    if (!canSave) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const st = await actions.client().setOnshapeKeys(access.trim(), secretKey.trim());
      setAccess("");
      setSecretKey("");
      update((d) => ({ keys: d.keys.some((k) => k.vendor === st.vendor) ? d.keys.map((k) => (k.vendor === st.vendor ? st : k)) : [...d.keys, st] }));
      await refresh().catch(() => undefined);
    } catch (e) {
      setError(describeError(e));
    }
    setBusy(false);
  }

  async function test(): Promise<void> {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await actions.client().testOnshape()); // exactly one call: no retry
    } catch (e) {
      setError(describeError(e));
    }
    setBusy(false);
  }

  return (
    <div class="setup-step">
      <StepHead title="Connect Onshape" sub="Optional. Without it, agents work on the local CAD mirror only." />
      <Card title="Onshape API keys">
        <p class="dim">Create an API key pair in Onshape's developer portal. Both keys go to the macOS Keychain and are never shown again.</p>
        <div class="setup-pair">
          <label class="setup-field"><span class="label">Access key</span>
            <input {...SECRET_INPUT} aria-label="Access key" value={access} onInput={(e) => setAccess(e.currentTarget.value)} />
          </label>
          <label class="setup-field"><span class="label">Secret key</span>
            <input {...SECRET_INPUT} aria-label="Secret key" value={secretKey} onInput={(e) => setSecretKey(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />
          </label>
        </div>
        <div class="setup-row">
          <KeyLine status={status} />
          <span class="setup-spacer" />
          <Button disabled={!canSave} onClick={() => { void save(); }}>Save</Button>
          <Button disabled={busy || !status?.present} onClick={() => { void test(); }}>Test</Button>
        </div>
        {result?.ok && <div class="setup-status"><Led tone="green" /><span class="text">{testText(result)}</span></div>}
        {result && !result.ok && <div class="setup-error text" role="alert">{testText(result)}</div>}
        {error && <div class="setup-error text" role="alert">{error}</div>}
      </Card>
      <div class="setup-actions">
        <Button variant="ghost" onClick={() => go("model")}>Back</Button>
        <span class="setup-spacer" />
        <Button variant="ghost" onClick={() => { setSkipped("onshape", true); go("done"); }}>Skip for now</Button>
        <Button variant="ink" disabled={!data.setup.has_onshape} onClick={() => { setSkipped("onshape", false); go("done"); }}>Continue</Button>
      </div>
    </div>
  );
}
