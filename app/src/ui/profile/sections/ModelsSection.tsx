import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { KeyStatus } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { pollUntil } from "../../setup/logic";
import {
  Card, ConfirmRemove, ErrorLine, LOCAL_URLS, LOCAL_VENDORS, SECRET_INPUT, SectionBox, useTask, type SectionProps,
} from "../common";

const SIGN_IN_POLL_MS = 2000;
const SIGN_IN_TIMEOUT_MS = 120_000;
const NOT_API_KEYS = new Set<string>([...LOCAL_VENDORS, "onshape"]);
const SOURCE_TEXT = { keychain: "Keychain", env: "environment", none: "no key" } as const;

export function ModelsSection(p: SectionProps): JSX.Element {
  return (
    <SectionBox title="Models">
      <ClaudeCard {...p} />
      <KeysCard {...p} />
      <LocalCard {...p} />
    </SectionBox>
  );
}

// ---- the user's own Claude Code: status from `claude auth status`, sign-in is Anthropic's browser flow
function ClaudeCard({ data, update }: SectionProps): JSX.Element {
  const c = data.claude;
  const [phase, setPhase] = useState<"idle" | "waiting" | "timeout">("idle");
  const poll = useRef<{ stop: () => void } | null>(null);
  const task = useTask();
  useEffect(() => () => { poll.current?.stop(); }, []);

  async function signIn(): Promise<void> {
    const started = await task.run(() => actions.client().claudeCodeLogin());
    if (!started) return;
    setPhase("waiting");
    const run = pollUntil(() => actions.client().claudeCodeStatus(), (s) => s.signed_in, { intervalMs: SIGN_IN_POLL_MS, timeoutMs: SIGN_IN_TIMEOUT_MS });
    poll.current = run;
    const r = await run.result;
    if (poll.current === run) poll.current = null;
    if (r.outcome === "done") {
      update({ claude: r.value });
      setPhase("idle");
    } else if (r.outcome === "timeout") {
      setPhase("timeout");
    }
  }
  function cancel(): void { poll.current?.stop(); poll.current = null; setPhase("idle"); }
  async function signOut(): Promise<void> {
    await task.run(async () => {
      await actions.client().claudeCodeLogout();
      update({ claude: await actions.client().claudeCodeStatus() });
    });
  }

  const tone = !c.installed ? "amber" : c.signed_in ? "green" : "faint";
  const word = !c.installed ? "not installed" : c.signed_in ? "signed in" : "signed out";
  return (
    <Card title="Your Claude Code">
      <p class="dim">Runs your Claude Code on this Mac, with your own sign-in and usage. Mises never sees your credentials.</p>
      <div class="profile-status"><Led tone={tone} /><span>{word}</span></div>
      {c.installed && (
        <dl class="profile-dl">
          <dt class="label">Version</dt><dd>{c.version ?? "unknown"}</dd>
          <dt class="label">Signed in with</dt><dd>{c.signed_in ? (c.auth_method ?? "unknown") : "—"}</dd>
        </dl>
      )}
      {!c.installed && (
        <p class="dim">Install it first: <code>npm install -g @anthropic-ai/claude-code</code>, then reopen this panel.</p>
      )}
      {phase === "waiting" && (
        <div class="profile-status"><Led tone="blue" /><span>finish signing in in your browser</span>
          <Button variant="ghost" onClick={cancel}>Cancel</Button>
        </div>
      )}
      {phase === "timeout" && <div class="profile-hint">Sign-in did not finish. Try again when you are ready.</div>}
      <div class="profile-row">
        {c.installed && !c.signed_in && phase !== "waiting" && <Button disabled={task.busy} onClick={() => { void signIn(); }}>Sign in</Button>}
        {c.installed && c.signed_in && <Button disabled={task.busy} onClick={() => { void signOut(); }}>Sign out</Button>}
      </div>
      <ErrorLine error={task.error} />
    </Card>
  );
}

// ---- API keys: status only; a secret is typed into a password field, sent once, and cleared
type KeyMode = { kind: "replace" | "remove"; vendor: string } | null;

function KeysCard({ data, update }: SectionProps): JSX.Element {
  const [mode, setMode] = useState<KeyMode>(null);
  const [secret, setSecret] = useState("");
  const task = useTask();
  const rows = data.keys.filter((k) => !NOT_API_KEYS.has(k.vendor));

  const replaceKey = (next: KeyStatus): void => update({ keys: data.keys.map((k) => (k.vendor === next.vendor ? next : k)) });
  function open(m: KeyMode): void { setMode(m); setSecret(""); task.clear(); }

  async function save(vendor: string): Promise<void> {
    const value = secret.trim();
    if (!value) return;
    const ok = await task.run(async () => { replaceKey(await actions.client().setKey(vendor, value)); });
    setSecret("");
    if (ok) setMode(null);
  }
  async function remove(vendor: string): Promise<void> {
    const ok = await task.run(async () => { replaceKey(await actions.client().deleteKey(vendor)); });
    if (ok) setMode(null);
  }

  return (
    <Card title="API keys">
      <p class="dim">Keys go to the macOS Keychain and are never shown again.</p>
      <table class="profile-table" aria-label="API keys">
        <thead><tr><th class="label">Vendor</th><th class="label">Status</th><th class="label">Source</th><th class="label"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {rows.map((k) => {
            const m = mode?.vendor === k.vendor ? mode : null;
            return (
              <tr key={k.vendor}>
                <td>{k.vendor}</td>
                <td><span class="profile-status"><Led tone={k.present ? "green" : "faint"} /><span>{k.present ? "ready" : "off"}</span></span></td>
                <td class={k.present ? "" : "faint"}>{SOURCE_TEXT[k.source]}</td>
                <td class="profile-actions">
                  {m?.kind === "replace" && (
                    <span class="profile-inline">
                      <input {...SECRET_INPUT} aria-label={`${k.vendor} API key`} value={secret}
                        onInput={(e) => setSecret(e.currentTarget.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(k.vendor); } }} />
                      <Button variant="ink" disabled={!secret.trim() || task.busy} onClick={() => { void save(k.vendor); }}>Save key</Button>
                      <Button variant="ghost" onClick={() => { setSecret(""); setMode(null); }}>Cancel</Button>
                    </span>
                  )}
                  {m?.kind === "remove" && (
                    <ConfirmRemove question={`Remove the ${k.vendor} key from the Keychain?`} busy={task.busy}
                      onYes={() => { void remove(k.vendor); }} onKeep={() => setMode(null)} />
                  )}
                  {!m && (
                    <span class="profile-inline">
                      <Button onClick={() => open({ kind: "replace", vendor: k.vendor })} aria-label={`${k.present ? "Replace" : "Add"} ${k.vendor} key`}>{k.present ? "Replace" : "Add"}</Button>
                      {k.present && k.source === "keychain" && (
                        <Button variant="ghost" onClick={() => open({ kind: "remove", vendor: k.vendor })} aria-label={`Remove ${k.vendor} key`}>Remove</Button>
                      )}
                      {k.present && k.source === "env" && <span class="faint">change it in the environment</span>}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <ErrorLine error={task.error} />
    </Card>
  );
}

// ---- local model servers (loopback only): the endpoint lives in the profile
function LocalCard({ data, update }: SectionProps): JSX.Element {
  const saved = data.profile.local_endpoints;
  const [urls, setUrls] = useState<Record<string, string>>(() => ({ ...saved }));
  const [results, setResults] = useState<Record<string, { ok: boolean; text: string }>>({});
  const [confirming, setConfirming] = useState<string | null>(null);
  const task = useTask();

  const setUrl = (vendor: string, v: string): void => {
    setUrls((u) => ({ ...u, [vendor]: v }));
    setResults((r) => { const { [vendor]: _gone, ...rest } = r; return rest; });
    task.clear();
  };
  async function writeEndpoints(next: Record<string, string>): Promise<void> {
    update({ profile: await actions.client().updateProfile({ local_endpoints: next }) });
  }
  async function save(vendor: string): Promise<void> {
    await task.run(() => writeEndpoints({ ...saved, [vendor]: (urls[vendor] ?? "").trim() }));
  }
  async function test(vendor: string): Promise<void> {
    const url = (urls[vendor] ?? "").trim();
    await task.run(async () => {
      const r = await actions.client().testLocal(url);
      const why = r.error && r.error !== "no answer" ? ` — ${r.error}` : "";
      setResults((all) => ({ ...all, [vendor]: r.ok ? { ok: true, text: "answering" } : { ok: false, text: `no answer from ${url}${why}` } }));
    });
  }
  async function remove(vendor: string): Promise<void> {
    const { [vendor]: _gone, ...rest } = saved;
    const ok = await task.run(() => writeEndpoints(rest));
    if (ok) { setConfirming(null); setUrls((u) => ({ ...u, [vendor]: "" })); }
  }

  return (
    <Card title="Local models">
      <p class="dim">Ollama or LM Studio running on this Mac. The address must be on this Mac (localhost).</p>
      {LOCAL_VENDORS.map((v) => {
        const url = urls[v] ?? "";
        const isSaved = v in saved;
        const res = results[v];
        return (
          <div key={v} class="profile-local">
            <span class="profile-local-name">{v}</span>
            <input aria-label={`${v} endpoint`} placeholder={LOCAL_URLS[v]} value={url} spellcheck={false} autocapitalize="off"
              onInput={(e) => setUrl(v, e.currentTarget.value)} />
            <Button variant="ink" disabled={task.busy || !url.trim() || saved[v] === url.trim()} onClick={() => { void save(v); }} aria-label={`Save ${v}`}>Save</Button>
            <Button disabled={task.busy || !url.trim()} onClick={() => { void test(v); }} aria-label={`Test ${v}`}>Test</Button>
            {isSaved && confirming !== v && <Button variant="ghost" onClick={() => setConfirming(v)} aria-label={`Remove ${v} endpoint`}>Remove</Button>}
            {confirming === v && (
              <ConfirmRemove question={`Remove the ${v} endpoint?`} busy={task.busy} onYes={() => { void remove(v); }} onKeep={() => setConfirming(null)} />
            )}
            {res && <span class="profile-status"><Led tone={res.ok ? "green" : "red"} /><span class="text">{res.text}</span></span>}
          </div>
        );
      })}
      <ErrorLine error={task.error} />
    </Card>
  );
}
