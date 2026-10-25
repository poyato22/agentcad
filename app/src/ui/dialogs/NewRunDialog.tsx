import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../api/client";
import type { AgentInfo, ModelsInfo } from "../../api/types";
import { registerShortcut } from "../../lib/keys";
import { partLabel, providerLabel } from "../../lib/names";
import { actions, newRunOpen, parts } from "../../state/store";
import { Button } from "../common/Button";
import { openProfile } from "../profile/open";
import { defaultModelRef, splitModelRef, toModelRef } from "./modelRef";

type VendorInfo = ModelsInfo["vendors"][number];

/** Why a vendor cannot run now (null: it can). */
function unavailable(v: VendorInfo | undefined): "no_key" | "signed_out" | "not_installed" | null {
  if (!v) return null;
  if (v.key === "none") return "no_key";
  if (v.key === "signed_out" || v.key === "not_installed") return v.key;
  return null;
}

function vendorOption(v: VendorInfo): string {
  const name = providerLabel(v.vendor);
  switch (unavailable(v)) {
    case "no_key": return `${name} · no API key — run \`mises keys set ${v.vendor}\``;
    case "signed_out": return `${name} · signed out — Sign in in Profile`;
    case "not_installed": return `${name} · not installed — Sign in in Profile`;
    default: return name;
  }
}
import "./dialog.css";

function describeError(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** Mounted once at the app root; open/close state is the store's `newRunOpen`. */
export function NewRunDialog(): JSX.Element | null {
  useEffect(() => registerShortcut("Meta+N", () => actions.openNewRun()), []);
  if (!newRunOpen.value) return null;
  return <DialogBody />;
}

function DialogBody(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [profileDefault, setProfileDefault] = useState<string | null>(null);
  const [agent, setAgent] = useState("");
  const [vendor, setVendor] = useState("");
  const [modelId, setModelId] = useState("");
  const [task, setTask] = useState("");
  const [allParts, setAllParts] = useState(true);
  const [picked, setPicked] = useState<readonly string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const agentSelect = useRef<HTMLSelectElement>(null);

  function prefill(a: AgentInfo, m: ModelsInfo, dflt: string | null = profileDefault): void {
    const ref = splitModelRef(defaultModelRef(a, m, dflt));
    setVendor(ref.vendor);
    setModelId(ref.model);
  }

  useEffect(() => {
    let live = true;
    // via a promise so "core client not ready" (⌘N before the core is up) shows as an error;
    // the profile only adds a default model, so a failure there is quiet
    const profile = Promise.resolve().then(() => actions.client().getProfile()).then((p) => p.default_model, () => null);
    Promise.resolve().then(() => Promise.all([actions.client().agents(), actions.client().models(), profile])).then(([as, ms, dflt]) => {
      if (!live) return;
      setAgents(as);
      setModels(ms);
      setProfileDefault(dflt);
      const first = as[0];
      if (first) { setAgent(first.name); prefill(first, ms, dflt); }
    }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, []);

  useEffect(() => { agentSelect.current?.focus(); }, [agents]);

  // Esc closes from anywhere while open; focus goes back to the command line.
  useEffect(() => {
    const onEsc = (e: KeyboardEvent): void => {
      if (e.key === "Escape") { e.preventDefault(); actions.closeNewRun(); }
    };
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("keydown", onEsc);
      document.querySelector<HTMLElement>('[aria-label="Command line"]')?.focus();
    };
  }, []);

  const current = agents?.find((a) => a.name === agent);
  const vendorInfo = models?.vendors.find((v) => v.vendor === vendor);
  const blocked = unavailable(vendorInfo);
  const noKey = blocked === "no_key";
  /** Vendors that list their models (the user's Claude Code): pick one, don't type it. */
  const modelList = vendorInfo?.models ?? null;
  const scope = allParts ? ["*"] : [...picked];
  const taskText = task.trim();
  const ready = !!current && !!models && !!taskText && !!modelId.trim() && !!vendor && !blocked && scope.length > 0 && !busy;

  function pickVendor(next: string): void {
    setVendor(next);
    const list = models?.vendors.find((v) => v.vendor === next)?.models;
    if (list && !list.includes(modelId)) setModelId(list[0] ?? "");
  }

  function signInInProfile(): void {
    actions.closeNewRun();
    openProfile("models");
  }

  async function start(): Promise<void> {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await actions.client().createRun({
        agent, prompt: taskText, model_ref: toModelRef(vendor, modelId.trim()), edit_scope: scope, read_scope: ["*"],
      });
      actions.selectRun(run_id);
      actions.closeNewRun();
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (e.key === "Tab" && panel.current) {
      const f = [...panel.current.querySelectorAll<HTMLElement>("select, input, textarea, button")].filter((x) => !(x as HTMLInputElement).disabled);
      const first = f[0];
      const last = f[f.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function togglePart(id: string, on: boolean): void {
    setPicked(on ? [...picked, id] : picked.filter((p) => p !== id));
  }

  return (
    <div class="dlg-backdrop">
      <div ref={panel} class="dlg" role="dialog" aria-modal="true" aria-label="New run" onKeyDown={onKeyDown}>
        <h2 class="pane-title">New run</h2>

        <label class="dlg-field" for="nr-agent"><span class="label">Agent</span></label>
        <select id="nr-agent" ref={agentSelect} value={agent} disabled={!agents} aria-label="Agent"
          onChange={(e) => {
            const a = agents?.find((x) => x.name === e.currentTarget.value);
            setAgent(e.currentTarget.value);
            if (a && models) prefill(a, models);
          }}>
          {(agents ?? []).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
        </select>
        <div class="dlg-desc dim text">{current?.description ?? ""}</div>

        <div class="dlg-pair">
          <div>
            <label class="dlg-field" for="nr-vendor"><span class="label">Vendor</span></label>
            <select id="nr-vendor" value={vendor} disabled={!models} aria-label="Vendor" onChange={(e) => pickVendor(e.currentTarget.value)}>
              {(models?.vendors ?? []).map((v) => (
                <option key={v.vendor} value={v.vendor} disabled={unavailable(v) !== null}>{vendorOption(v)}</option>
              ))}
            </select>
          </div>
          <div>
            <label class="dlg-field" for="nr-model"><span class="label">Model</span></label>
            {modelList
              ? (
                <select id="nr-model" value={modelId} aria-label="Model" onChange={(e) => setModelId(e.currentTarget.value)}>
                  {!modelList.includes(modelId) && modelId && <option value={modelId}>{modelId}</option>}
                  {modelList.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              )
              : <input id="nr-model" value={modelId} aria-label="Model" spellcheck={false} onInput={(e) => setModelId(e.currentTarget.value)} />}
          </div>
        </div>
        {noKey && <div class="dlg-hint dim" data-hint>no API key for {vendor} — run `mises keys set {vendor}`</div>}
        {(blocked === "signed_out" || blocked === "not_installed") && (
          <div class="dlg-hint dim" data-hint>
            <span>{`${providerLabel(vendor)} is ${blocked === "signed_out" ? "signed out" : "not installed"} on this Mac.`}</span>
            {" "}
            <Button variant="ghost" onClick={signInInProfile}>Sign in in Profile</Button>
          </div>
        )}

        <label class="dlg-field" for="nr-task"><span class="label">Task</span></label>
        <textarea id="nr-task" rows={4} value={task} aria-label="Task" required onInput={(e) => setTask(e.currentTarget.value)} />

        <div class="dlg-field"><span class="label">Edit scope</span></div>
        <div class="dlg-scope">
          <label><input type="checkbox" checked={allParts} onChange={(e) => setAllParts(e.currentTarget.checked)} /> all parts</label>
          {!allParts && parts.value.map((p) => (
            <label key={p.part_id}>
              <input type="checkbox" checked={picked.includes(p.part_id)} onChange={(e) => togglePart(p.part_id, e.currentTarget.checked)} /> {partLabel(p.part_id)}
            </label>
          ))}
          {!allParts && parts.value.length === 0 && <span class="faint">no parts yet</span>}
        </div>

        {error && <div class="dlg-error text" role="alert">{error}</div>}

        <div class="dlg-actions">
          <Button variant="ghost" onClick={() => actions.closeNewRun()}>Close</Button>
          <Button variant="ink" disabled={!ready} onClick={() => { void start(); }}>Start</Button>
        </div>
      </div>
    </div>
  );
}
