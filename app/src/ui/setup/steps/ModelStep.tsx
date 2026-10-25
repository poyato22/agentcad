// Connect a model: the user's own Claude Code, an API key, or a local model server.
// Continue unlocks once the core reports has_model; "Skip — local models only" never blocks.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ClaudeCodeStatus, KeyStatus } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { setSkipped } from "../gate";
import { pollUntil } from "../logic";
import type { StepProps } from "../SetupWizard";
import { Card, describeError, KeyLine, SECRET_INPUT, StepHead } from "./common";

/** Vendors that are not API keys (no key) or not models (onshape). */
const NOT_API_KEYS = new Set(["ollama", "lmstudio", "onshape"]);
export const LOCAL_APPS = [
  { vendor: "ollama", label: "Ollama", url: "http://localhost:11434/v1" },
  { vendor: "lmstudio", label: "LM Studio", url: "http://localhost:1234/v1" },
] as const;

export const SIGN_IN_POLL_MS = 2000;
export const SIGN_IN_TIMEOUT_MS = 120_000;

export function ModelStep(p: StepProps): JSX.Element {
  const has = p.data.setup.has_model;
  return (
    <div class="setup-step">
      <StepHead title="Connect a model" sub="Pick one way to run agents. You can add more later in Profile." />
      <div class="setup-cards">
        <ClaudeCard {...p} />
        <ApiKeyCard {...p} />
        <LocalCard {...p} />
      </div>
      <div class="setup-actions">
        <Button variant="ghost" onClick={() => p.go("welcome")}>Back</Button>
        <span class="setup-spacer" />
        <Button variant="ghost" onClick={() => { setSkipped("model", true); p.go("onshape"); }}>Skip — local models only</Button>
        <Button variant="ink" disabled={!has} onClick={() => { setSkipped("model", false); p.go("onshape"); }}>Continue</Button>
      </div>
    </div>
  );
}

type SignIn = "idle" | "starting" | "waiting" | "timeout";

function ClaudeCard({ data, update, refresh }: StepProps): JSX.Element {
  const st = data.claude;
  const [phase, setPhase] = useState<SignIn>("idle");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const poll = useRef<{ stop: () => void } | null>(null);

  useEffect(() => () => poll.current?.stop(), []);

  const setStatus = (claude: ClaudeCodeStatus): void => update({ claude });

  async function signIn(): Promise<void> {
    setError(null);
    setPhase("starting");
    try {
      // Runs the user's own `claude auth login`: Anthropic's sign-in opens in the browser.
      await actions.client().claudeCodeLogin();
    } catch (e) {
      setError(describeError(e));
      setPhase("idle");
      return;
    }
    setPhase("waiting");
    const run = pollUntil(() => actions.client().claudeCodeStatus(), (s) => s.signed_in, { intervalMs: SIGN_IN_POLL_MS, timeoutMs: SIGN_IN_TIMEOUT_MS });
    poll.current = run;
    const r = await run.result;
    if (poll.current === run) poll.current = null;
    if (r.outcome === "done") {
      setStatus(r.value);
      setPhase("idle");
      await refresh().catch(() => undefined);
    } else if (r.outcome === "timeout") {
      setPhase("timeout");
    }
  }

  async function checkAgain(): Promise<void> {
    setChecking(true);
    setError(null);
    try { setStatus(await actions.client().claudeCodeStatus()); } catch (e) { setError(describeError(e)); }
    setChecking(false);
  }

  let body: JSX.Element;
  if (!st.installed) {
    body = (
      <>
        <div class="setup-status"><Led tone="faint" /><span>Claude Code isn't installed on this Mac.</span></div>
        <p class="dim">Install it from Anthropic (see docs.anthropic.com/en/docs/claude-code), for example in Terminal:</p>
        <pre class="setup-code text">npm install -g @anthropic-ai/claude-code</pre>
        <p class="dim">then check again.</p>
        <div class="setup-row"><Button disabled={checking} onClick={() => { void checkAgain(); }}>Check again</Button></div>
      </>
    );
  } else if (st.signed_in) {
    body = <div class="setup-status"><Led tone="green" /><span>{st.auth_method ? `signed in · ${st.auth_method}` : "signed in"}</span></div>;
  } else {
    body = (
      <>
        <div class="setup-status">
          <Led tone={phase === "waiting" ? "blue" : phase === "timeout" ? "amber" : "faint"} />
          <span>
            {phase === "waiting" ? "finish signing in in your browser"
              : phase === "timeout" ? "sign-in timed out — try again"
              : phase === "starting" ? "opening the sign-in…" : "installed, not signed in"}
          </span>
        </div>
        <div class="setup-row">
          {phase !== "waiting" && <Button disabled={phase === "starting"} onClick={() => { void signIn(); }}>Sign in</Button>}
        </div>
      </>
    );
  }

  return (
    <Card title="Use my Claude Code">
      <p class="dim">Runs your Claude Code with your own Claude account and plan. Sign-in happens in Anthropic's own page in your browser; Mises never sees it.</p>
      {st.version && <div class="faint setup-meta text">version {st.version}</div>}
      {body}
      {error && <div class="setup-error text" role="alert">{error}</div>}
    </Card>
  );
}

function ApiKeyCard({ data, update, refresh }: StepProps): JSX.Element {
  const vendors = data.keys.filter((k) => !NOT_API_KEYS.has(k.vendor));
  const [vendor, setVendor] = useState(vendors[0]?.vendor ?? "");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = data.keys.find((k) => k.vendor === vendor);

  async function save(): Promise<void> {
    if (!vendor || !secret.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const st: KeyStatus = await actions.client().setKey(vendor, secret);
      setSecret("");
      update({ keys: data.keys.map((k) => (k.vendor === st.vendor ? st : k)) });
      await refresh().catch(() => undefined);
    } catch (e) {
      setError(describeError(e));
    }
    setBusy(false);
  }

  return (
    <Card title="API key">
      <p class="dim">Use a key from a model vendor. It goes to the macOS Keychain and is never shown again.</p>
      <div class="setup-pair">
        <label class="setup-field"><span class="label">Vendor</span>
          <select aria-label="Vendor" value={vendor} onChange={(e) => { setVendor(e.currentTarget.value); setSecret(""); setError(null); }}>
            {vendors.map((k) => <option key={k.vendor} value={k.vendor}>{k.vendor}</option>)}
          </select>
        </label>
        <label class="setup-field"><span class="label">API key</span>
          <input {...SECRET_INPUT} aria-label="API key" value={secret} placeholder={status?.present ? "replace the saved key" : "paste the key"}
            onInput={(e) => setSecret(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />
        </label>
      </div>
      <div class="setup-row">
        <KeyLine status={status} />
        <span class="setup-spacer" />
        <Button disabled={busy || !secret.trim()} onClick={() => { void save(); }}>Save</Button>
      </div>
      {error && <div class="setup-error text" role="alert">{error}</div>}
    </Card>
  );
}

function LocalCard({ data, update, refresh }: StepProps): JSX.Element {
  const [vendor, setVendor] = useState<string>(LOCAL_APPS[0].vendor);
  const urlFor = (v: string): string => data.profile.local_endpoints[v] ?? LOCAL_APPS.find((a) => a.vendor === v)?.url ?? "";
  const [url, setUrl] = useState(urlFor(LOCAL_APPS[0].vendor));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  async function test(): Promise<void> {
    const endpoint = url.trim();
    if (!endpoint) return;
    setBusy(true);
    setResult(null);
    const before = data.setup.has_model;
    try {
      // The core checks the endpoint (the webview never calls it): save it, then ask GET /setup.
      const profile = await actions.client().updateProfile({ local_endpoints: { ...data.profile.local_endpoints, [vendor]: endpoint } });
      update({ profile });
      const s = await refresh();
      if (before) setResult({ ok: true, text: "saved — a model already works; Mises uses this endpoint when you pick it" });
      else if (s.has_model) setResult({ ok: true, text: "answering" });
      else setResult({ ok: false, text: `no answer from ${endpoint} — is ${LOCAL_APPS.find((a) => a.vendor === vendor)?.label ?? vendor} running?` });
    } catch (e) {
      setResult({ ok: false, text: describeError(e) });
    }
    setBusy(false);
  }

  return (
    <Card title="Local model">
      <p class="dim">Run models on this Mac with Ollama or LM Studio. Nothing leaves the machine.</p>
      <div class="setup-pair">
        <label class="setup-field"><span class="label">Local app</span>
          <select aria-label="Local app" value={vendor} onChange={(e) => { const v = e.currentTarget.value; setVendor(v); setUrl(urlFor(v)); setResult(null); }}>
            {LOCAL_APPS.map((a) => <option key={a.vendor} value={a.vendor}>{a.label}</option>)}
          </select>
        </label>
        <label class="setup-field"><span class="label">Endpoint</span>
          <input aria-label="Endpoint" value={url} spellcheck={false} onInput={(e) => setUrl(e.currentTarget.value)} />
        </label>
      </div>
      <div class="setup-row">
        {result && <div class="setup-status"><Led tone={result.ok ? "green" : "red"} /><span class="text">{result.text}</span></div>}
        <span class="setup-spacer" />
        <Button disabled={busy || !url.trim()} onClick={() => { void test(); }}>Test</Button>
      </div>
    </Card>
  );
}
