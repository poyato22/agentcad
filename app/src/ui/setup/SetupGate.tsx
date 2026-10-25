// Mount once at the app root (Task 6). Shows the full-screen setup wizard while GET /setup says
// setup is incomplete, or when re-opened from Profile. Closing keeps what's saved; the wizard
// comes back next launch only while required pieces are still missing.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { actions } from "../../state/store";
import { closeSetupWizard, setupWizardOpen } from "./gate";
import { SetupWizard } from "./SetupWizard";

const RETRY_MS = 1000;

export function SetupGate(): JSX.Element | null {
  const [needed, setNeeded] = useState<boolean | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const check = (): void => {
      // via a promise so "core client not ready" (core still starting) is retried too
      Promise.resolve().then(() => actions.client().setupState()).then(
        (s) => { if (live) setNeeded(!s.complete); },
        () => { if (live) timer = setTimeout(check, RETRY_MS); },
      );
    };
    check();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, []);

  const reopened = setupWizardOpen.value;
  if (!reopened && (needed !== true || dismissed)) return null;
  const close = (): void => { setDismissed(true); closeSetupWizard(); };
  return <SetupWizard reopened={reopened} onClose={close} />;
}
