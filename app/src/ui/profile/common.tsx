import type { ComponentChildren, JSX } from "preact";
import { useState } from "preact/hooks";
import { CoreError } from "../../api/client";
import type { AgentInfo, ClaudeCodeStatus, CoreStatusInfo, KeyStatus, ModelsInfo, Profile } from "../../api/types";
import { Button } from "../common/Button";
import { Led } from "../common/Led";

/** Everything the Profile panel shows, loaded once and kept current as sections save. */
export interface ProfileData {
  profile: Profile;
  keys: KeyStatus[];
  claude: ClaudeCodeStatus;
  /** null when /status could not be read (usage then reads "unavailable"). */
  status: CoreStatusInfo | null;
  models: ModelsInfo | null;
  agents: AgentInfo[];
}

export interface SectionProps {
  data: ProfileData;
  /** Merge a patch; pass a function to derive it from the latest data (use after an await). */
  update(patch: Partial<ProfileData> | ((d: ProfileData) => Partial<ProfileData>)): void;
}

export function describeError(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** Props for a secret input: password field, no autofill/spellcheck, never echoed. */
export const SECRET_INPUT = { type: "password", autocomplete: "off", spellcheck: false, autocapitalize: "off", autocorrect: "off" } as const;

/** Runs one async action at a time and keeps its busy / error / note state. */
export function useTask(): { busy: boolean; error: string | null; note: string | null; run(fn: () => Promise<void>, note?: string): Promise<boolean>; clear(): void } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  return {
    busy, error, note,
    clear: () => { setError(null); setNote(null); },
    async run(fn, doneNote) {
      setBusy(true);
      setError(null);
      setNote(null);
      let ok = false;
      try {
        await fn();
        ok = true;
        if (doneNote) setNote(doneNote);
      } catch (e) {
        setError(describeError(e));
      }
      setBusy(false);
      return ok;
    },
  };
}

export function SectionBox({ title, children }: { title: string; children: ComponentChildren }): JSX.Element {
  return (
    <section class="profile-section" role="region" aria-label={title}>
      <h2 class="profile-h">{title}</h2>
      {children}
    </section>
  );
}

export function Card({ title, children }: { title: string; children: ComponentChildren }): JSX.Element {
  const id = `profile-card-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section class="profile-card" role="region" aria-labelledby={id}>
      <h3 id={id} class="profile-card-title">{title}</h3>
      {children}
    </section>
  );
}

export function ErrorLine({ error }: { error: string | null }): JSX.Element | null {
  return error ? <div class="profile-error text" role="alert">{error}</div> : null;
}

/** The inline "are you sure" that replaces window.confirm: no native dialog, stays in the panel. */
export function ConfirmRemove({ question, busy, onYes, onKeep }: { question: string; busy: boolean; onYes: () => void; onKeep: () => void }): JSX.Element {
  return (
    <span class="profile-confirm" role="group" aria-label="Confirm removal">
      <span class="text">{question}</span>
      <Button variant="danger" disabled={busy} onClick={onYes}>Yes, remove</Button>
      <Button variant="ghost" disabled={busy} onClick={onKeep}>Keep</Button>
    </span>
  );
}

/** Where a saved secret lives. The secret itself is never shown, only a fixed mask. */
export function KeyLine({ status }: { status: KeyStatus | undefined }): JSX.Element {
  if (status?.present && status.source === "keychain") {
    return <div class="profile-status"><Led tone="green" /><span>saved ●●●● from Keychain</span></div>;
  }
  if (status?.present && status.source === "env") {
    return <div class="profile-status"><Led tone="green" /><span>{ENV_TEXT}</span></div>;
  }
  return <div class="profile-status"><Led tone="faint" /><span class="faint">no key saved</span></div>;
}

/** Whole non-negative integer from an input string, or null. */
export function wholeNumber(s: string, min: number, max = Number.MAX_SAFE_INTEGER): number | null {
  if (!/^\d+$/.test(s.trim())) return null;
  const n = Number(s);
  return n >= min && n <= max ? n : null;
}

export const ENV_TEXT = "from environment variable — it takes precedence over a saved key";
export const ENV_SAVED_NOTE = "Saved to Keychain. The environment variable still takes precedence; the saved key is used only when it is unset.";

/** Replace (or append) one vendor's status in a key list. */
export function upsertKey(keys: KeyStatus[], next: KeyStatus): KeyStatus[] {
  return keys.some((k) => k.vendor === next.vendor) ? keys.map((k) => (k.vendor === next.vendor ? next : k)) : [...keys, next];
}

export const LOCAL_VENDORS = ["ollama", "lmstudio"] as const;
export const LOCAL_URLS: Record<string, string> = { ollama: "http://localhost:11434/v1", lmstudio: "http://localhost:1234/v1" };
