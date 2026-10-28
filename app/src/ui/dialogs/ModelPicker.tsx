// The model picker used everywhere a model is chosen (inline composer, new-run dialog, setup,
// Profile defaults): a chip naming the current model; opening it shows the groups inline
// (Recommended · Your Claude Code · On this Mac (Ollama) · Free (OpenRouter) · API keys), with
// badges, "Start Ollama" and "Download (19 GB)" for a recommended local model, and a typed ref
// as a last resort. Model names and notes are untrusted: text only.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Button } from "../common/Button";
import { Led } from "../common/Led";
import { catalog, ollamaStart, pullModel, pullText, pulls, refreshCatalog, startOllama } from "./catalog";
import { buildModelGroups, refLabel, type ModelChoice, type ModelGroup } from "./modelGroups";
import "./modelPicker.css";

export interface ModelPickerProps {
  value: string | null;
  onChange(ref: string): void;
  /** Accessible name of the chip (default "Model"). */
  label?: string;
  /** Text when no model is chosen (default "choose a model"). */
  placeholder?: string;
  disabled?: boolean;
}

export function ModelPicker({ value, onChange, label = "Model", placeholder = "choose a model", disabled }: ModelPickerProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const chip = useRef<HTMLButtonElement>(null);
  useEffect(() => { void refreshCatalog(); }, []);
  const close = (): void => { setOpen(false); chip.current?.focus(); };
  return (
    <div class="mp" data-model-picker>
      <button ref={chip} type="button" class="mp-chip" aria-label={`${label}: ${value ? refLabel(value) : placeholder}`} aria-expanded={open}
        disabled={disabled} onClick={() => { if (!open) void refreshCatalog(); setOpen(!open); }}>
        <span class="label">{label}</span>
        <span class={`mp-chip-v text${value ? "" : " faint"}`}>{value ? refLabel(value) : placeholder}</span>
        <span class="faint" aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && <Panel value={value} onPick={(ref) => { onChange(ref); close(); }} onClose={close} />}
    </div>
  );
}

function Panel({ value, onPick, onClose }: { value: string | null; onPick(ref: string): void; onClose(): void }): JSX.Element {
  const cat = catalog.value;
  const [typed, setTyped] = useState("");
  const groups = cat.models ? buildModelGroups(cat.models, { local: cat.local, profile: { default_model: cat.profileDefault } }) : [];
  const typedOk = /^[a-z0-9-]+\/\S+$/i.test(typed.trim());
  return (
    <div class="mp-panel" role="region" aria-label="Choose a model"
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); } }}>
      {!cat.models && cat.loading && <div class="faint mp-pad">loading models…</div>}
      {cat.error && <div class="mp-err text mp-pad" role="alert">{cat.error}</div>}
      {groups.map((g) => <Group key={g.id} group={g} value={value} onPick={onPick} />)}
      <div class="mp-other">
        <input aria-label="Other model" placeholder="other model: vendor/model" value={typed} spellcheck={false} autocapitalize="off"
          onInput={(e) => setTyped(e.currentTarget.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && typedOk) { e.preventDefault(); onPick(typed.trim()); } }} />
        <Button disabled={!typedOk} onClick={() => onPick(typed.trim())}>Use</Button>
      </div>
    </div>
  );
}

/**
 * Ollama on this Mac on its own (setup's local model card, Profile): its state with "Start
 * Ollama", the models already downloaded, and "Download (19 GB)" for recommended ones that fit.
 * Renders nothing on a core without GET /local/status.
 */
export function LocalModels(): JSX.Element | null {
  useEffect(() => { void refreshCatalog(); }, []);
  const cat = catalog.value;
  if (!cat.models || !cat.local) return null;
  const g = buildModelGroups(cat.models, { local: cat.local }).find((x) => x.id === "local");
  if (!g) return null;
  return (
    <div class="mp-standalone" role="group" aria-label="Ollama on this Mac" data-local-models>
      <LocalBar group={g} />
      {g.local?.state === "running" && g.choices.length === 0 && <div class="faint mp-pad">no models downloaded yet</div>}
      {g.local?.state === "running" && g.choices.map((c) => <div key={c.ref} class="mp-pad text">{c.label}</div>)}
      {g.local?.downloads.map((d) => <Download key={d.name} name={d.name} sizeGb={d.size_gb} note={d.note} running={g.local?.state === "running"} />)}
    </div>
  );
}

function Group({ group, value, onPick }: { group: ModelGroup; value: string | null; onPick(ref: string): void }): JSX.Element {
  const id = `mp-g-${group.id}`;
  return (
    <div class="mp-group" role="group" aria-label={group.title} data-group={group.id}>
      <div class="label mp-group-h" id={id}>{group.title}</div>
      {group.note && <div class="faint text mp-note mp-pad">{group.note}</div>}
      {group.local && <LocalBar group={group} />}
      {group.choices.map((c) => <Choice key={`${group.id}:${c.ref}`} c={c} selected={c.ref === value} onPick={onPick} />)}
      {group.local?.downloads.map((d) => <Download key={d.name} name={d.name} sizeGb={d.size_gb} note={d.note} running={group.local?.state === "running"} />)}
    </div>
  );
}

function Choice({ c, selected, onPick }: { c: ModelChoice; selected: boolean; onPick(ref: string): void }): JSX.Element {
  return (
    <button type="button" class={`mp-opt${selected ? " sel" : ""}`} aria-pressed={selected} disabled={!c.usable} data-ref={c.ref}
      data-selected={selected ? "" : undefined} onClick={() => onPick(c.ref)}>
      <span class="mp-opt-l text">{c.label}</span>
      {c.badges.map((b) => <span key={b.kind} class="mp-badge" data-badge={b.kind}>{b.text}</span>)}
      {(c.note || c.why) && <span class="mp-opt-n faint text">{c.why ?? c.note}</span>}
    </button>
  );
}

function LocalBar({ group }: { group: ModelGroup }): JSX.Element | null {
  const st = ollamaStart.value;
  const state = group.local?.state;
  if (state === "running" || state === "unknown") return null;
  if (state === "not_installed") {
    return (
      <div class="mp-local mp-pad">
        <div class="mp-row"><Led tone="faint" /><span>Ollama isn't installed. It runs open models on this Mac, free.</span></div>
        <div class="faint">Install it from ollama.com, or in Terminal:</div>
        <pre class="mp-code text">brew install ollama</pre>
      </div>
    );
  }
  return (
    <div class="mp-local mp-pad">
      <div class="mp-row">
        <Led tone="faint" /><span>Ollama isn't running.</span>
        <span class="mp-spacer" />
        <Button disabled={st.busy} onClick={() => { void startOllama(); }}>{st.busy ? "Starting…" : "Start Ollama"}</Button>
      </div>
      {st.error && <div class="mp-err text" role="alert">{st.error}</div>}
    </div>
  );
}

function Download({ name, sizeGb, note, running }: { name: string; sizeGb: number; note: string | null; running: boolean }): JSX.Element {
  const p = pulls.value.get(name);
  const size = `${Number.isInteger(sizeGb) ? sizeGb : sizeGb.toFixed(1)} GB`;
  const active = p && !p.error && p.status !== "success";
  const pct = p && p.total > 0 ? Math.min(100, Math.floor((p.completed / p.total) * 100)) : 0;
  return (
    <div class="mp-dl" data-download={name}>
      <div class="mp-row">
        <span class="mp-opt-l text">{name}</span>
        <span class="mp-badge" data-badge="local">runs on this Mac</span>
        <span class="mp-spacer" />
        {!active && (
          <Button disabled={!running} aria-label={`Download ${name} (${size})`} title={running ? undefined : "start Ollama first"}
            onClick={() => { void pullModel(name); }}>{`Download (${size})`}</Button>
        )}
      </div>
      {note && <div class="faint text mp-opt-n">{note}</div>}
      {p && (
        <div class={`mp-pull${p.error ? " mp-err" : ""}`} data-pull={name}>
          {!p.error && <progress max={100} value={pct} aria-label={`Downloading ${name}`} />}
          <span class="text">{pullText(p)}</span>
        </div>
      )}
    </div>
  );
}
