// New run (⌘N, /new, "+ new run" in the Details roster): agent, model (the shared model picker),
// task and edit scope. The conversation view's "+ New" uses the inline composer instead.
import { showRoutedNotes } from "../console/routedNotes";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AgentInfo, ModelsInfo } from "../../api/types";
import { startableAgents } from "../../lib/agents";
import { registerShortcut } from "../../lib/keys";
import { partLabel, providerLabel } from "../../lib/names";
import { runBuild } from "../../state/builds";
import { actions, newRunOpen, parts } from "../../state/store";
import { Button } from "../common/Button";
import { OverflowMenu } from "../common/OverflowMenu";
import { startErrorText } from "../console/startError";
import { openProfile } from "../profile/open";
import { catalog, refreshCatalog } from "./catalog";
import { newRunModel } from "./modelGroups";
import { ModelPicker } from "./ModelPicker";
import { defaultModelRef, splitModelRef } from "./modelRef";
import "./dialog.css";

type VendorInfo = ModelsInfo["vendors"][number];

/** Why a vendor cannot run now (null: it can). */
function unavailable(v: VendorInfo | undefined): "no_key" | "signed_out" | "not_installed" | null {
  if (!v) return null;
  if (v.key === "none") return "no_key";
  if (v.key === "signed_out" || v.key === "not_installed") return v.key;
  return null;
}

const describeError = startErrorText;

/** Mounted once at the app root; open/close state is the store's `newRunOpen`. */
export function NewRunDialog(): JSX.Element | null {
  useEffect(() => registerShortcut("Meta+N", () => actions.openNewRun()), []);
  if (!newRunOpen.value) return null;
  return <DialogBody />;
}

function DialogBody(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  /** The explicit agent override, or null to let the core route from the prompt (R8). */
  const [override, setOverride] = useState<string | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [task, setTask] = useState("");
  const [allParts, setAllParts] = useState(true);
  const [picked, setPicked] = useState<readonly string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const taskRef = useRef<HTMLTextAreaElement>(null);
  const cat = catalog.value;
  const models = cat.models;

  /** Same precedence as the core: the agent's own model, else the profile default, else the core default. */
  function prefill(a: AgentInfo): void {
    const m = catalog.peek().models;
    if (!m) return;
    setModel(defaultModelRef(a, m, catalog.peek().profileDefault));
  }

  useEffect(() => {
    let live = true;
    // via a promise so "core client not ready" (⌘N before the core is up) shows as an error.
    // agents are loaded only for the "specific agent" override; routing needs none.
    Promise.resolve().then(() => Promise.all([actions.client().agents(), refreshCatalog()])).then(([all]) => {
      if (!live) return;
      const as = startableAgents(all);   // hidden agents are not offered (C-4)
      setAgents(as);
      // the model the router would land on: the first agent's own model, else the profile default,
      // else the core default. Set once, so a later user pick is never overridden (catalog is ready).
      const c = catalog.peek();
      // (the same default as the console's inline composer: newRunModel — I6)
      if (c.models) setModel((cur) => cur ?? newRunModel(c));
      if (c.error && !c.models) setError(c.error);
    }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, []);

  useEffect(() => { taskRef.current?.focus(); }, []);

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

  const current = override ? agents?.find((a) => a.name === override) ?? null : null;
  const ref = model ? splitModelRef(model) : null;
  const vendorInfo = ref ? models?.vendors.find((v) => v.vendor === ref.vendor) : undefined;
  const blocked = unavailable(vendorInfo);
  const scope = allParts ? ["*"] : [...picked];
  const taskText = task.trim();
  const ready = !!models && !!taskText && !!model && !!ref?.vendor && !!ref.model && !blocked && scope.length > 0 && !busy;

  function signInInProfile(): void {
    actions.closeNewRun();
    openProfile("models");
  }

  async function start(): Promise<void> {
    if (!ready || !model) return;
    setBusy(true);
    setError(null);
    try {
      const res = await actions.client().createRun({
        ...(override ? { agent: override } : {}), prompt: taskText, model_ref: model, edit_scope: scope, read_scope: ["*"], ...runBuild(),
      });
      const { run_id } = res;
      showRoutedNotes(res);
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

        <div class="dlg-field"><span class="label">Agent</span></div>
        {override
          ? (
            <div class="dlg-agent-override">
              <select value={override} disabled={!agents} aria-label="Agent"
                onChange={(e) => {
                  const a = agents?.find((x) => x.name === e.currentTarget.value);
                  setOverride(e.currentTarget.value);
                  if (a) prefill(a);
                }}>
                {(agents ?? []).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
              </select>
              <Button variant="ghost" onClick={() => { setOverride(null); setShowPicker(false); }}>Auto</Button>
            </div>
          )
          : (
            <>
              <div class="dlg-auto-row">
                <span class="dlg-auto dim text" data-auto>Mises picks the right agent from your prompt.</span>
                {!showPicker && <OverflowMenu label="More options" items={[{ label: "Use a specific agent…", onSelect: () => setShowPicker(true) }]} />}
              </div>
              {showPicker && (
                <select class="dlg-override" aria-label="Choose an agent" disabled={!agents} value=""
                  onChange={(e) => { const v = e.currentTarget.value; if (v) { setOverride(v); const a = agents?.find((x) => x.name === v); if (a) prefill(a); } }}>
                  <option value="" disabled>Choose an agent</option>
                  {(agents ?? []).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
                </select>
              )}
            </>
          )}
        <div class="dlg-desc dim text">{current?.description ?? ""}</div>

        <div class="dlg-field"><span class="label">Model</span></div>
        <ModelPicker value={model} onChange={setModel} disabled={!models} />
        {blocked === "no_key" && ref && (
          <div class="dlg-hint dim" data-hint>
            <span>{`No API key for ${providerLabel(ref.vendor)} on this Mac.`}</span>
            {" "}
            <Button variant="ghost" onClick={signInInProfile}>Add a key</Button>
          </div>
        )}
        {(blocked === "signed_out" || blocked === "not_installed") && ref && (
          <div class="dlg-hint dim" data-hint>
            <span>{`${providerLabel(ref.vendor)} is ${blocked === "signed_out" ? "signed out" : "not installed"} on this Mac.`}</span>
            {" "}
            <Button variant="ghost" onClick={signInInProfile}>Sign in</Button>
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
