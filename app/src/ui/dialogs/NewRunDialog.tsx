import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../api/client";
import type { AgentInfo, ModelsInfo } from "../../api/types";
import { registerShortcut } from "../../lib/keys";
import { partLabel } from "../../lib/names";
import { actions, newRunOpen, parts } from "../../state/store";
import { Button } from "../common/Button";
import { defaultModelRef, splitModelRef, toModelRef } from "./modelRef";
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

  function prefill(a: AgentInfo, m: ModelsInfo): void {
    const ref = splitModelRef(defaultModelRef(a, m));
    setVendor(ref.vendor);
    setModelId(ref.model);
  }

  useEffect(() => {
    let live = true;
    Promise.all([actions.client().agents(), actions.client().models()]).then(([as, ms]) => {
      if (!live) return;
      setAgents(as);
      setModels(ms);
      const first = as[0];
      if (first) { setAgent(first.name); prefill(first, ms); }
    }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, []);

  useEffect(() => { agentSelect.current?.focus(); }, [agents]);

  const current = agents?.find((a) => a.name === agent);
  const vendorInfo = models?.vendors.find((v) => v.vendor === vendor);
  const noKey = vendorInfo?.key === "none";
  const scope = allParts ? ["*"] : [...picked];
  const taskText = task.trim();
  const ready = !!current && !!models && !!taskText && !!modelId.trim() && !!vendor && !noKey && scope.length > 0 && !busy;

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
    if (e.key === "Escape") {
      e.preventDefault();
      actions.closeNewRun();
      return;
    }
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
            <select id="nr-vendor" value={vendor} disabled={!models} aria-label="Vendor" onChange={(e) => setVendor(e.currentTarget.value)}>
              {(models?.vendors ?? []).map((v) => (
                <option key={v.vendor} value={v.vendor} disabled={v.key === "none"}>
                  {v.key === "none" ? `${v.vendor} · no API key — run \`mises keys set ${v.vendor}\`` : v.vendor}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label class="dlg-field" for="nr-model"><span class="label">Model</span></label>
            <input id="nr-model" value={modelId} aria-label="Model" spellcheck={false} onInput={(e) => setModelId(e.currentTarget.value)} />
          </div>
        </div>
        {noKey && <div class="dlg-hint dim" data-hint>no API key for {vendor} — run `mises keys set {vendor}`</div>}

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
          <Button variant="primary" disabled={!ready} onClick={() => { void start(); }}>Start</Button>
        </div>
      </div>
    </div>
  );
}
