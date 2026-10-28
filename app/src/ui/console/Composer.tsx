// Inline composer for a new run ("+ New" in the conversation view): agent and model chips
// prefilled from the profile (an agent's own model wins, as in the core), the task, Start.
// Enter starts, Shift+Enter is a new line, Esc closes. Edit scope is every part ("*"), as the
// dialog's default; the dialog (⌘N) still offers a narrower scope.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../api/client";
import type { AgentInfo } from "../../api/types";
import { actions, coreStatus, runs } from "../../state/store";
import { Button } from "../common/Button";
import { catalog } from "../dialogs/catalog";
import { buildModelGroups, choiceFor, pickDefault } from "../dialogs/modelGroups";
import { ModelPicker } from "../dialogs/ModelPicker";
import { noteRun } from "./queued";
import { composerOpen } from "./view";

function describeError(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

export function Composer(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [agent, setAgent] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [picked, setPicked] = useState(false);
  const pickedRef = useRef(false);
  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taskRef = useRef<HTMLTextAreaElement>(null);
  const cat = catalog.value;

  useEffect(() => {
    let live = true;
    const profile = Promise.resolve().then(() => actions.client().getProfile()).catch(() => null);
    Promise.resolve().then(() => Promise.all([actions.client().agents(), profile])).then(([as, p]) => {
      if (!live) return;
      setAgents(as);
      const dflt = p && as.some((a) => a.name === p.default_agent) ? p.default_agent : as[0]?.name ?? "";
      setAgent(dflt);
    }, (e: unknown) => { if (live) setError(describeError(e)); });
    taskRef.current?.focus();
    return () => { live = false; };
  }, []);

  const groups = cat.models ? buildModelGroups(cat.models, { local: cat.local, profile: { default_model: cat.profileDefault } }) : [];
  const current = agents?.find((a) => a.name === agent);
  // prefill: the agent's own model, else the profile default, else the best one that can run now
  useEffect(() => {
    // a ref, not the state: an effect queued before the pick may run after it with a stale closure
    if (pickedRef.current || !cat.models || !current) return;
    const own = current.model && current.model !== "inherit" ? current.model : null;
    const preferred = cat.profileDefault ?? cat.models.default;
    setModel(own ?? pickDefault(groups, preferred) ?? preferred);
  }, [cat.models, cat.local, cat.profileDefault, current?.name, picked]);

  const choice = model ? choiceFor(groups, model) : null;
  const blocked = choice && !choice.usable ? choice.why : null;
  const text = task.trim();
  const ready = !!current && !!model && !!text && !blocked && !busy;
  const par = coreStatus.value?.parallel;
  const working = [...runs.value.values()].filter((r) => r.status === "running").length;
  const willQueue = par ? working >= par.limit : false;

  async function start(): Promise<void> {
    if (!ready || !model) return;
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await actions.client().createRun({ agent, prompt: text, model_ref: model, edit_scope: ["*"], read_scope: ["*"] });
      actions.selectRun(run_id);
      noteRun(run_id);          // shown at once, even before the store lists it (e.g. queued)
      actions.setFollow(true);
      composerOpen.value = false;
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  return (
    <form class="cv-composer" aria-label="New run" onSubmit={(e) => { e.preventDefault(); void start(); }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); composerOpen.value = false; } }}>
      <div class="cv-chips">
        <label class="cv-chip">
          <span class="label">Agent</span>
          <select aria-label="Agent" value={agent} disabled={!agents} onChange={(e) => setAgent(e.currentTarget.value)}>
            {(agents ?? []).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>
        </label>
        <ModelPicker value={model} onChange={(ref) => { pickedRef.current = true; setModel(ref); setPicked(true); }} />
      </div>
      {current?.description && <div class="cv-desc faint text">{current.description}</div>}
      {blocked && <div class="cv-desc dim text" data-hint>{`${model} · ${blocked}`}</div>}
      <textarea ref={taskRef} aria-label="Task" rows={3} value={task} placeholder="e.g. stiffen the upright, keep mass under 40 g"
        onInput={(e) => setTask(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); void start(); } }} />
      {willQueue && par && <div class="cv-desc faint">{`${working} running · queues for a free slot (${par.auto ? "Auto" : "limit"}: ${par.limit})`}</div>}
      {error && <div class="cv-err text" role="alert">{error}</div>}
      <div class="cv-actions">
        <Button variant="ghost" onClick={() => { composerOpen.value = false; }}>Cancel</Button>
        <Button variant="ink" type="submit" disabled={!ready}>Start</Button>
      </div>
    </form>
  );
}
