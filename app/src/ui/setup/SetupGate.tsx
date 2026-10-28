// Mounted once at the app root (App.tsx). GET /setup decides:
//   complete === false            → the full-screen setup wizard (closable: "finish later")
//   complete, but has_model false → a small "connect a model" notice (local-only users finished
//                                   the wizard on purpose; they are not sent through it again)
// The wizard also opens when Profile → "Run setup again" sets setupWizardOpen. The core marks
// setup complete only from the wizard's last step (POST /setup/complete).
// While the core is not ready the check retries with backoff; a 401 stops it (the session
// rebuilds with a new token) and the next successful connect checks again.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../api/client";
import type { SetupState } from "../../api/types";
import { actions, conn } from "../../state/store";
import { Button } from "../common/Button";
import { Led } from "../common/Led";
import { noteProfileChanged, openProfile, profileOpen } from "../profile/open";
import { closeSetupWizard, setupWizardOpen } from "./gate";
import { SetupWizard } from "./SetupWizard";
import "./setup.css";

export const RETRY_MS = [1000, 2000, 4000, 8000, 15000];

export function SetupGate(): JSX.Element | null {
  const [state, setState] = useState<SetupState | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [noticeHidden, setNoticeHidden] = useState(false);
  const [closes, setCloses] = useState(0);

  const reopened = setupWizardOpen.value;
  // check again after Profile or the re-opened wizard closes, after this wizard closes, and on
  // every (re)connect
  const paused = profileOpen.value || reopened;
  const connected = conn.value === "open";
  const [reconnects, setReconnects] = useState(0);
  const seenOpen = useRef(false);
  const stoppedOn401 = useRef(false);

  // check again on a reconnect (the core may have restarted); the first connect only when the
  // boot check was stopped by a 401 (a check already in flight or retrying answers by itself)
  useEffect(() => {
    if (!connected) return;
    const first = !seenOpen.current;
    seenOpen.current = true;
    if (!first || stoppedOn401.current) setReconnects((n) => n + 1);
  }, [connected]);

  useEffect(() => {
    if (paused) return;
    stoppedOn401.current = false;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    const check = (): void => {
      // via a promise so "core client not ready" (core still starting) is retried too
      Promise.resolve().then(() => actions.client().setupState()).then(
        (s) => { if (live) setState(s); },
        (e: unknown) => {
          if (!live) return;
          if (e instanceof CoreError && e.code === "unauthorized") { stoppedOn401.current = true; return; } // wait for the reconnect
          timer = setTimeout(check, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
        },
      );
    };
    check();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [paused, closes, reconnects]);

  if (reopened || (state && !state.complete && !dismissed)) {
    const close = (): void => { setDismissed(true); closeSetupWizard(); setCloses((n) => n + 1); noteProfileChanged(); };
    return <SetupWizard reopened={reopened} onClose={close} />;
  }
  if (state && state.complete && !state.has_model && !noticeHidden && !profileOpen.value) {
    return (
      <div class="setup-notice" role="status" aria-label="Connect a model">
        <Led tone="amber" />
        <span>No model connected. Runs need one.</span>
        <Button onClick={() => openProfile("models")}>Connect</Button>
        <Button variant="ghost" onClick={() => setNoticeHidden(true)}>Dismiss</Button>
      </div>
    );
  }
  return null;
}
