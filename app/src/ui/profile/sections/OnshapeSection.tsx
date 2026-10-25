import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { KeyStatus, OnshapeTestResult } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import {
  Card, ConfirmRemove, ErrorLine, KeyLine, SECRET_INPUT, SectionBox, useTask, wholeNumber, type SectionProps,
} from "../common";

const fmt = (n: number): string => n.toLocaleString("en-US");

function testText(r: OnshapeTestResult): string {
  if (r.ok) return r.user_name ? `connected as ${r.user_name}` : "connected";
  if (r.error === "denied") return "Onshape refused these keys (denied). Check the access key and secret key in Onshape's developer portal, then save them again.";
  return `Onshape test failed: ${r.error ?? "unknown error"}`;
}

export function OnshapeSection({ data, update }: SectionProps): JSX.Element {
  return (
    <SectionBox title="Onshape">
      <KeysCard data={data} update={update} />
      <UsageCard data={data} update={update} />
    </SectionBox>
  );
}

function KeysCard({ data, update }: SectionProps): JSX.Element {
  const status = data.keys.find((k) => k.vendor === "onshape");
  const [mode, setMode] = useState<"replace" | "remove" | null>(null);
  const [access, setAccess] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [result, setResult] = useState<OnshapeTestResult | null>(null);
  const task = useTask();
  const canSave = !!access.trim() && !!secretKey.trim() && !task.busy;

  const put = (next: KeyStatus): void => {
    update({ keys: data.keys.some((k) => k.vendor === next.vendor) ? data.keys.map((k) => (k.vendor === next.vendor ? next : k)) : [...data.keys, next] });
  };
  function reset(m: "replace" | "remove" | null): void { setMode(m); setAccess(""); setSecretKey(""); setResult(null); task.clear(); }

  async function save(): Promise<void> {
    if (!canSave) return;
    const a = access.trim();
    const s = secretKey.trim();
    const ok = await task.run(async () => { put(await actions.client().setOnshapeKeys(a, s)); });
    setAccess("");
    setSecretKey("");
    if (ok) setMode(null);
  }
  async function remove(): Promise<void> {
    const ok = await task.run(async () => { put(await actions.client().deleteKey("onshape")); });
    if (ok) reset(null);
  }
  async function test(): Promise<void> {
    setResult(null);
    await task.run(async () => { setResult(await actions.client().testOnshape()); }); // exactly one call: no retry
  }

  return (
    <Card title="Onshape API keys">
      <p class="dim">An API key pair from Onshape's developer portal. Both keys go to the macOS Keychain and are never shown again.</p>
      <KeyLine status={status} />
      {mode === "replace" && (
        <div class="profile-pair">
          <label class="profile-field"><span class="label">Access key</span>
            <input {...SECRET_INPUT} aria-label="Access key" value={access} onInput={(e) => setAccess(e.currentTarget.value)} />
          </label>
          <label class="profile-field"><span class="label">Secret key</span>
            <input {...SECRET_INPUT} aria-label="Secret key" value={secretKey} onInput={(e) => setSecretKey(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />
          </label>
        </div>
      )}
      <div class="profile-row">
        {mode === "replace" && (<>
          <Button variant="ink" disabled={!canSave} onClick={() => { void save(); }}>Save keys</Button>
          <Button variant="ghost" onClick={() => reset(null)}>Cancel</Button>
        </>)}
        {mode === "remove" && (
          <ConfirmRemove question="Remove the Onshape keys from the Keychain?" busy={task.busy} onYes={() => { void remove(); }} onKeep={() => reset(null)} />
        )}
        {mode === null && (<>
          <Button onClick={() => reset("replace")}>{status?.present ? "Replace keys" : "Add keys"}</Button>
          {status?.present && status.source === "keychain" && <Button variant="ghost" onClick={() => reset("remove")}>Remove keys</Button>}
          <Button disabled={task.busy || !status?.present} onClick={() => { void test(); }}>Test</Button>
        </>)}
      </div>
      {result?.ok && <div class="profile-status"><Led tone="green" /><span class="text">{testText(result)}</span></div>}
      {result && !result.ok && <div class="profile-error text" role="alert">{testText(result)}</div>}
      <ErrorLine error={task.error} />
    </Card>
  );
}

function UsageCard({ data, update }: SectionProps): JSX.Element {
  const cap = data.profile.onshape_calls_year_cap;
  const used = data.status?.onshape_calls_year ?? null;
  const [capText, setCapText] = useState(String(cap));
  const task = useTask();
  const next = wholeNumber(capText, 0);
  const dirty = next !== null && next !== cap;

  const ratio = used === null || cap <= 0 ? 0 : Math.min(1, used / cap);
  const tone = used !== null && cap > 0 && used >= cap ? "red" : ratio >= 0.8 ? "amber" : "ink";

  async function save(): Promise<void> {
    if (next === null) return;
    await task.run(async () => {
      const p = await actions.client().updateProfile({ onshape_calls_year_cap: next });
      update({ profile: p });
      setCapText(String(p.onshape_calls_year_cap));
    }, "saved");
  }

  return (
    <Card title="Usage this year">
      {used === null
        ? <div class="dim">usage unavailable</div>
        : (<>
          <div class="text">{fmt(used)} of {fmt(cap)} calls this year</div>
          <div class="profile-bar" role="progressbar" aria-label="Onshape calls this year" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={used} data-tone={tone}>
            <div class="profile-bar-fill" style={{ width: `${Math.round(ratio * 100)}%` }} />
          </div>
        </>)}
      <label class="profile-field"><span class="label">Yearly call cap</span>
        <input aria-label="Yearly call cap" type="number" min="0" step="1" value={capText}
          onInput={(e) => { setCapText(e.currentTarget.value); task.clear(); }} />
      </label>
      <div class="profile-row">
        <Button variant="ink" disabled={!dirty || task.busy} onClick={() => { void save(); }}>Save caps</Button>
        {task.note && <span class="dim">{task.note}</span>}
      </div>
      <p class="dim">The per-run cap is under Agents &amp; budgets.</p>
      <ErrorLine error={task.error} />
    </Card>
  );
}
