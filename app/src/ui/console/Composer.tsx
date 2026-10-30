// Inline composer for a new run ("+ New" in the conversation view). No agent picker: the core
// routes to the right agent from the prompt (R8), and the thread shows the choice as a chip once
// the run exists. "Use a specific agent…" in the ⋯ menu reveals a picker for the rare override.
// The model chip is prefilled from the profile (or the overridden agent's own model, as in the
// core). Enter starts, Shift+Enter is a new line, Esc closes. Edit scope is every part ("*").
// The composer stays usable while the build's lead works: the core queues the message (U3c) and
// the thread shows it with a "queued" chip until the lead reads it; a full queue (429) says so.
import { showRoutedNotes } from "./routedNotes";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AgentInfo } from "../../api/types";
import { startableAgents } from "../../lib/agents";
import { agentChips, chipsPromptLine, clearAgentChips, removeAgentChip } from "../../state/agentChips";
import { runBuild } from "../../state/builds";
import { actions, coreStatus, runs } from "../../state/store";
import { Button } from "../common/Button";
import { OverflowMenu } from "../common/OverflowMenu";
import { catalog } from "../dialogs/catalog";
import { buildModelGroups, choiceFor, pickDefault } from "../dialogs/modelGroups";
import { ModelPicker } from "../dialogs/ModelPicker";
import { noteRun } from "./queued";
import { startErrorText as describeError } from "./startError";
import { composerOpen } from "./view";

export function Composer(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  /** The explicit agent override, or null to let the core route from the prompt (R8). */
  const [override, setOverride] = useState<string | null>(null);
  const [showPicker, setShowPicker] = useState(false);
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
    // agents are loaded only for the "specific agent" override; routing needs none
    Promise.resolve().then(() => actions.client().agents()).then((all) => {
      if (live) setAgents(startableAgents(all));   // hidden agents are not offered (C-4)
    }, (e: unknown) => { if (live) setError(describeError(e)); });
    taskRef.current?.focus();
    return () => { live = false; };
  }, []);

  const groups = cat.models ? buildModelGroups(cat.models, { local: cat.local, profile: { default_model: cat.profileDefault } }) : [];
  const current = override ? agents?.find((a) => a.name === override) ?? null : null;
  // prefill: the overridden agent's own model, else the profile default, else the best one usable now
  useEffect(() => {
    // a ref, not the state: an effect queued before the pick may run after it with a stale closure
    if (pickedRef.current || !cat.models) return;
    const own = current?.model && current.model !== "inherit" ? current.model : null;
    const preferred = cat.profileDefault ?? cat.models.default;
    setModel(own ?? pickDefault(groups, preferred) ?? preferred);
  }, [cat.models, cat.local, cat.profileDefault, current?.name, picked]);

  const choice = model ? choiceFor(groups, model) : null;
  const blocked = choice && !choice.usable ? choice.why : null;
  const text = task.trim();
  const ready = !!model && !!text && !blocked && !busy;
  const par = coreStatus.value?.parallel;
  const working = [...runs.value.values()].filter((r) => r.status === "running").length;
  const willQueue = par ? working >= par.limit : false;

  async function start(): Promise<void> {
    if (!ready || !model) return;
    setBusy(true);
    setError(null);
    try {
      const chipLine = chipsPromptLine(agentChips.peek());
      const prompt = chipLine ? `${text}\n\n${chipLine}` : text;
      const res = await actions.client().createRun({
        ...(override ? { agent: override } : {}), prompt, model_ref: model, edit_scope: ["*"], read_scope: ["*"], ...runBuild(),
      });
      const { run_id } = res;
      showRoutedNotes(res);
      if (chipLine) clearAgentChips();
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
        {override
          ? (
            <label class="cv-chip">
              <span class="label">Agent</span>
              <select aria-label="Agent" value={override} disabled={!agents} onChange={(e) => setOverride(e.currentTarget.value)}>
                {(agents ?? []).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
              </select>
            </label>
          )
          : <span class="cv-auto faint text" data-auto>Mises picks the right agent from your prompt.</span>}
        <ModelPicker value={model} onChange={(ref) => { pickedRef.current = true; setModel(ref); setPicked(true); }} />
        <span class="cv-spacer" />
        {override
          ? <Button variant="ghost" onClick={() => { setOverride(null); setShowPicker(false); }}>Auto</Button>
          : showPicker
            ? (
              <select class="cv-override-sel" aria-label="Choose an agent" disabled={!agents} value="" onChange={(e) => { if (e.currentTarget.value) setOverride(e.currentTarget.value); }}>
                <option value="" disabled>Choose an agent</option>
                {(agents ?? []).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
              </select>
            )
            : <OverflowMenu label="More options" items={[{ label: "Use a specific agent…", onSelect: () => setShowPicker(true) }]} />}
      </div>
      {agentChips.value.length > 0 && (
        <div class="cv-context" role="list" aria-label="Context for the agent">
          {agentChips.value.map((c) => (
            <span key={c.id} class="cv-context-chip" role="listitem" data-chip={c.id} title={`${c.id}: the agent reads it with catalog_get`}>
              <span class="cv-context-label">{c.label}</span>
              <button type="button" class="cv-context-x" aria-label={`Remove ${c.label}`} onClick={() => removeAgentChip(c.id)}>×</button>
            </span>
          ))}
        </div>
      )}
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
