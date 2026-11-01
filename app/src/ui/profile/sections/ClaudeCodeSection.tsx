// Profile & settings → Claude Code: the user's own Claude Code (status, sign-in) and its Network
// settings (P10 E3) — the CA bundle and proxy a VPN or HTTPS-filtering proxy needs. A Mac app
// started from the Dock never sees the shell's NODE_EXTRA_CA_CERTS / HTTPS_PROXY, so they are
// kept in the profile (claude_code_env) and the core passes them to every `claude` it starts. The
// core validates each value (an absolute path that exists, a proxy URL, a host list) and never
// sends a proxy's password back: it shows as `***`, and the masked address sent back unchanged
// keeps it. Values are untrusted text shown in inputs only.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Card, ErrorLine, SectionBox, useTask, type SectionProps } from "../common";
import { ClaudeCard } from "./ModelsSection";

/** The Network fields, in order: the variable, its label, a placeholder and what it is for. */
export const NETWORK_FIELDS = [
  { name: "NODE_EXTRA_CA_CERTS", label: "Extra CA certificates", placeholder: "/Users/you/proxy-ca.pem",
    help: "The full path to your VPN or proxy's CA file (PEM), when it inspects HTTPS." },
  { name: "HTTPS_PROXY", label: "HTTPS proxy", placeholder: "http://proxy.example:8080",
    help: "http, https, socks5 or socks5h. A password in it is kept by Mises and shown as ***." },
  { name: "NO_PROXY", label: "No proxy for", placeholder: "localhost,127.0.0.1,.corp.example",
    help: "Hosts, domains or IPs that skip the proxy, comma-separated." },
] as const;
type NetworkName = (typeof NETWORK_FIELDS)[number]["name"];

export function ClaudeCodeSection(p: SectionProps): JSX.Element {
  return (
    <SectionBox title="Claude Code">
      <ClaudeCard {...p} />
      <NetworkCard {...p} />
    </SectionBox>
  );
}

function fieldsOf(env: Record<string, string>): Record<NetworkName, string> {
  return { NODE_EXTRA_CA_CERTS: env.NODE_EXTRA_CA_CERTS ?? "", HTTPS_PROXY: env.HTTPS_PROXY ?? "", NO_PROXY: env.NO_PROXY ?? "" };
}

export function NetworkCard({ data, update }: SectionProps): JSX.Element {
  const saved = data.profile.claude_code_env ?? {};
  const [vals, setVals] = useState<Record<NetworkName, string>>(() => fieldsOf(saved));
  const task = useTask();
  const before = fieldsOf(saved);
  const dirty = NETWORK_FIELDS.some((f) => vals[f.name].trim() !== before[f.name]);
  // the other allowed variables (SSL_CERT_FILE, HTTP_PROXY, …) set elsewhere are kept as they are
  const others = Object.keys(saved).filter((k) => !NETWORK_FIELDS.some((f) => f.name === k));

  async function save(): Promise<void> {
    const env: Record<string, string> = { ...saved };
    for (const f of NETWORK_FIELDS) {
      const v = vals[f.name].trim();
      if (v) env[f.name] = v; else delete env[f.name];
    }
    await task.run(async () => {
      const next = await actions.client().updateProfile({ claude_code_env: env });
      update({ profile: next });
      setVals(fieldsOf(next.claude_code_env ?? {}));
    }, "Saved. Claude Code uses it from its next start.");
  }

  return (
    <Card title="Network">
      <p class="dim">For a VPN or a proxy that inspects HTTPS. Mises passes these to Claude Code when it starts it, so you don't have to launch Mises from a terminal.</p>
      {NETWORK_FIELDS.map((f) => (
        <label key={f.name} class="profile-field" data-env={f.name}>
          <span class="label">{f.label} <code class="faint">{f.name}</code></span>
          <input aria-label={f.label} value={vals[f.name]} placeholder={f.placeholder} spellcheck={false} autocomplete="off"
            autocapitalize="off" autocorrect="off"
            onInput={(e) => { const v = e.currentTarget.value; setVals((s) => ({ ...s, [f.name]: v })); task.clear(); }} />
          <span class="profile-hint">{f.help}</span>
        </label>
      ))}
      {others.length > 0 && <p class="faint" data-env-others>Also set: {others.join(", ")} (kept).</p>}
      <div class="profile-row">
        <Button variant="ink" disabled={!dirty || task.busy} onClick={() => { void save(); }}>Save</Button>
        {dirty && <Button variant="ghost" disabled={task.busy} onClick={() => { setVals(fieldsOf(saved)); task.clear(); }}>Revert</Button>}
        {task.note && <span class="dim" role="status">{task.note}</span>}
      </div>
      <ErrorLine error={task.error} />
    </Card>
  );
}
