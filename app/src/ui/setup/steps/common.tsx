import type { ComponentChildren, JSX } from "preact";
import { CoreError } from "../../../api/client";
import type { KeyStatus } from "../../../api/types";
import { Led } from "../../common/Led";

export function describeError(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** Where a saved secret lives. The secret itself is never shown, only a fixed mask. */
export function KeyLine({ status }: { status: KeyStatus | undefined }): JSX.Element {
  if (status?.present && status.source === "keychain") {
    return <div class="setup-status"><Led tone="green" /><span>saved ●●●● from Keychain</span></div>;
  }
  if (status?.present && status.source === "env") {
    return <div class="setup-status"><Led tone="green" /><span>set in the environment</span></div>;
  }
  return <div class="setup-status"><Led tone="faint" /><span class="faint">no key saved</span></div>;
}

export function Card({ title, children }: { title: string; children: ComponentChildren }): JSX.Element {
  const id = `setup-card-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section class="setup-card" role="region" aria-labelledby={id}>
      <h3 id={id} class="setup-card-title">{title}</h3>
      {children}
    </section>
  );
}

export function StepHead({ title, sub }: { title: string; sub?: string }): JSX.Element {
  return (
    <div class="setup-step-head">
      <h2 class="setup-h">{title}</h2>
      {sub && <p class="dim setup-sub">{sub}</p>}
    </div>
  );
}

/** Props for a secret input: password field, no autofill/spellcheck/autocorrect, never echoed. */
export const SECRET_INPUT = { type: "password", autocomplete: "off", spellcheck: false, autocapitalize: "off", autocorrect: "off" } as const;
