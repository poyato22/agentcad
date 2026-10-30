// Agents tab of the agent manager: the agent definitions runs start from. Custom agents can be
// created, edited and deleted (POST/PUT/DELETE /agents, written to the Mises agents folder);
// built-in agents can only be hidden or shown again (the profile's hidden_agents). The form has a
// model picker (GET /models) and a tools picker ("default tools", or only the ones ticked).
import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { CoreError, type ToolInfo } from "../../api/client";
import type { AgentInfo } from "../../api/types";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { catalog, refreshCatalog } from "../dialogs/catalog";
import { AGENT_NAME_RE, modelRefs } from "./manager";

function message(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

interface Draft { name: string; description: string; model: string; useDefaults: boolean; tools: string[]; body: string }
const EMPTY: Draft = { name: "", description: "", model: "inherit", useDefaults: true, tools: [], body: "" };

export function AgentsTab(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [registry, setRegistry] = useState<ToolInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** null: no form; "": a new agent; otherwise the custom agent being edited. */
  const [editing, setEditing] = useState<string | null>(null);

  const load = async () => {
    try {
      const client = actions.client();
      // the tools list degrades quietly: without it the picker offers what the agents list
      const [list, profile, tools] = await Promise.all([client.agents(), client.getProfile(), client.tools().catch(() => null)]);
      setAgents(list);
      setRegistry(tools);
      setHidden(profile.hidden_agents ?? list.filter((a) => a.hidden).map((a) => a.name));
      setError(null);
    } catch (e) { setError(message(e)); }
  };
  useEffect(() => { void load(); void refreshCatalog(); }, []);

  const toggleHidden = async (name: string) => {
    const next = hidden.includes(name) ? hidden.filter((n) => n !== name) : [...hidden, name];
    try { await actions.client().updateProfile({ hidden_agents: next }); setHidden(next); await load(); } catch (e) { setError(message(e)); }
  };
  const remove = async (name: string) => {
    try { await actions.client().deleteAgent(name); await load(); } catch (e) { setError(message(e)); }
  };

  /** The picker's choices: every tool the core registers (GET /tools, C-7), plus any an agent
   *  lists that it doesn't (an older core: only those). */
  const knownTools = useMemo(() => [...new Set([...(registry ?? []).map((t) => t.name), ...(agents ?? []).flatMap((a) => a.tools ?? [])])].sort(), [agents, registry]);
  const toolDocs = useMemo(() => new Map((registry ?? []).map((t) => [t.name, t.description])), [registry]);
  const builtins = (agents ?? []).filter((a) => a.builtin !== false);
  const custom = (agents ?? []).filter((a) => a.builtin === false);

  return (
    <div class="am-agents" data-agents-tab>
      <div class="am-toolbar">
        <span class="faint text">Runs start from one of these agents.</span>
        <span class="am-spacer" />
        <Button onClick={() => setEditing("")} disabled={editing === ""}>New agent</Button>
      </div>
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
      {editing !== null && (
        <AgentForm key={editing} existing={editing ? custom.find((a) => a.name === editing) ?? null : null}
          knownTools={knownTools} toolDocs={toolDocs} taken={(agents ?? []).map((a) => a.name)}
          onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); void load(); }} />
      )}
      {agents === null && !error && <div class="am-empty faint">Loading agents…</div>}

      {agents !== null && (
        <>
          <h3 class="am-group-h">Your agents</h3>
          {custom.length === 0
            ? <p class="am-empty faint">None yet. New agent makes one from a prompt, a model and a set of tools.</p>
            : (
              <ul class="am-list">
                {custom.map((a) => (
                  <li key={a.name} class="am-agent" data-agent={a.name}>
                    <div class="am-line"><span class="am-name text">{a.name}</span><span class="am-meta faint text">{a.model}</span></div>
                    {a.description && <div class="am-desc faint text">{a.description}</div>}
                    <div class="am-sub">
                      <span class="faint">{a.tools ? `${a.tools.length} tool${a.tools.length === 1 ? "" : "s"}` : "default tools"}</span>
                      <span class="am-spacer" />
                      <Button variant="ghost" onClick={() => setEditing(a.name)}>Edit</Button>
                      <DeleteAgent name={a.name} onConfirm={() => { void remove(a.name); }} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          <h3 class="am-group-h">Built in</h3>
          <ul class="am-list">
            {builtins.map((a) => {
              const isHidden = hidden.includes(a.name);
              return (
                <li key={a.name} class={`am-agent${isHidden ? " am-hidden" : ""}`} data-agent={a.name} data-hidden={isHidden ? "" : undefined}>
                  <div class="am-line"><span class="am-name text">{a.name}</span><span class="am-meta faint text">{a.model}</span></div>
                  {a.description && <div class="am-desc faint text">{a.description}</div>}
                  <div class="am-sub">
                    <span class="faint">{isHidden ? "hidden from New run" : a.tools ? `${a.tools.length} tools` : "default tools"}</span>
                    <span class="am-spacer" />
                    <Button variant="ghost" aria-pressed={isHidden} onClick={() => { void toggleHidden(a.name); }}>{isHidden ? "Show" : "Hide"}</Button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}

function DeleteAgent({ name, onConfirm }: { name: string; onConfirm: () => void }): JSX.Element {
  const [armed, setArmed] = useState(false);
  return (
    <Button variant={armed ? "danger" : "ghost"} aria-label={armed ? `Confirm delete ${name}` : `Delete ${name}`}
      onClick={() => { if (armed) { setArmed(false); onConfirm(); } else setArmed(true); }} onBlur={() => setArmed(false)}>
      {armed ? "Confirm delete" : "Delete"}
    </Button>
  );
}

function AgentForm({ existing, knownTools, toolDocs, taken, onCancel, onSaved }: {
  existing: AgentInfo | null; knownTools: string[]; toolDocs: ReadonlyMap<string, string>; taken: string[]; onCancel: () => void; onSaved: () => void;
}): JSX.Element {
  const [d, setD] = useState<Draft>(() => existing
    ? { name: existing.name, description: existing.description, model: existing.model, useDefaults: existing.tools == null, tools: existing.tools ?? [], body: "" }
    : EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Editing: the agent's current instructions are loaded (GET /agents/{name}) before saving. */
  const [loaded, setLoaded] = useState(existing === null);
  useEffect(() => {
    if (!existing) return;
    let live = true;
    actions.client().getAgent(existing.name).then((a) => {
      if (!live) return;
      setD((cur) => ({ ...cur, description: a.description, model: a.model, useDefaults: a.tools == null, tools: a.tools ?? [], body: a.body ?? "" }));
      setLoaded(true);
    }, (e: unknown) => { if (live) setError(`Couldn't load this agent's instructions: ${message(e)}`); });
    return () => { live = false; };
  }, [existing?.name]);
  const models = modelRefs(catalog.value.models);
  if (!models.includes(d.model)) models.push(d.model);

  const nameError = existing ? null
    : !d.name ? null
    : !AGENT_NAME_RE.test(d.name) ? "Use 2–41 lowercase letters, digits, - or _, starting with a letter."
    : taken.includes(d.name) ? "An agent with this name already exists." : null;
  // an edit saves only once the current instructions are loaded into the form, and never sends an
  // empty body (instructions are required), so a save can't blank an agent's prompt
  const canSave = !busy && loaded && (existing !== null || (d.name !== "" && nameError === null))
    && (d.useDefaults || d.tools.length > 0) && d.body.trim() !== "";

  const set = (p: Partial<Draft>) => setD((cur) => ({ ...cur, ...p }));
  const toggleTool = (t: string) => set({ tools: d.tools.includes(t) ? d.tools.filter((x) => x !== t) : [...d.tools, t] });

  const save = async (e: Event) => {
    e.preventDefault();
    if (!canSave) return;
    setBusy(true); setError(null);
    const body = { description: d.description, model: d.model, tools: d.useDefaults ? null : d.tools, body: d.body };
    try {
      if (existing) await actions.client().updateAgent(existing.name, body);
      else await actions.client().createAgent({ name: d.name, ...body });
      onSaved();
    } catch (err) { setError(message(err)); setBusy(false); }
  };

  return (
    <form class="am-form" aria-label={existing ? `Edit ${existing.name}` : "New agent"} onSubmit={(e) => { void save(e); }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onCancel(); } }}>
      <label class="am-field">
        <span class="label">Name</span>
        <input type="text" value={d.name} disabled={existing !== null} aria-invalid={nameError ? true : undefined}
          onInput={(e) => set({ name: (e.currentTarget as HTMLInputElement).value })} placeholder="e.g. rib-checker" />
        {nameError && <span class="tone-red text am-hint">{nameError}</span>}
      </label>
      <label class="am-field">
        <span class="label">What it does</span>
        <input type="text" value={d.description} onInput={(e) => set({ description: (e.currentTarget as HTMLInputElement).value })}
          placeholder="One line, shown in New run" />
      </label>
      <label class="am-field">
        <span class="label">Model</span>
        <select value={d.model} onChange={(e) => set({ model: (e.currentTarget as HTMLSelectElement).value })}>
          {models.map((m) => <option key={m} value={m}>{m === "inherit" ? "inherit (your default model)" : m}</option>)}
        </select>
      </label>
      <fieldset class="am-field am-tools">
        <legend class="label">Tools</legend>
        <label class="am-check"><input type="radio" name="tools" checked={d.useDefaults} onChange={() => set({ useDefaults: true })} /> Default tools</label>
        <label class="am-check"><input type="radio" name="tools" checked={!d.useDefaults} onChange={() => set({ useDefaults: false })} /> Only these</label>
        {!d.useDefaults && (
          <div class="am-toolgrid" role="group" aria-label="Allowed tools">
            {[...new Set([...knownTools, ...d.tools])].map((t) => (
              <label key={t} class="am-check text" title={toolDocs.get(t) || undefined}><input type="checkbox" checked={d.tools.includes(t)} onChange={() => toggleTool(t)} />{t}</label>
            ))}
          </div>
        )}
        {!d.useDefaults && d.tools.length === 0 && <span class="faint am-hint">Tick at least one tool.</span>}
      </fieldset>
      <label class="am-field">
        <span class="label">Instructions</span>
        <textarea rows={5} value={d.body} disabled={!loaded} onInput={(e) => set({ body: (e.currentTarget as HTMLTextAreaElement).value })}
          placeholder={loaded ? "How this agent should work" : "Loading the current instructions…"} />
      </label>
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
      <div class="am-form-foot">
        <Button type="submit" variant="ink" disabled={!canSave}>{existing ? "Save agent" : "Create agent"}</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
