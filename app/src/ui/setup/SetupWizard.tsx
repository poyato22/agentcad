// First-run setup: Welcome → Profile → Model → Onshape (optional) → Done. The step bar at the top
// jumps to any step.
// Every step saves to the core as it goes, so closing or reloading mid-way loses nothing and the
// wizard resumes at the first piece still missing. Secrets go straight to the core (Keychain) and
// are never shown again: inputs are password fields that clear after a save.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ClaudeCodeStatus, KeyStatus, ModelsInfo, Profile, SetupState } from "../../api/types";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { useFocusTrap } from "../common/focusTrap";
import { skipped } from "./gate";
import { resumeStep, stepDone, STEPS, type StepId, type StepStatus } from "./logic";
import { DoneStep } from "./steps/DoneStep";
import { describeError } from "./steps/common";
import { ModelStep } from "./steps/ModelStep";
import { OnshapeStep } from "./steps/OnshapeStep";
import { ProfileStep } from "./steps/ProfileStep";
import { WelcomeStep } from "./steps/WelcomeStep";
import "./setup.css";

export interface SetupData {
  setup: SetupState; profile: Profile; keys: KeyStatus[]; claude: ClaudeCodeStatus; models: ModelsInfo;
}

/** What every step gets: the loaded data, setters that keep it current, and navigation. */
export interface StepProps {
  data: SetupData;
  /** Merge a patch; pass a function to derive it from the latest data (use after an await, so a
   *  save that finished meanwhile is not overwritten with stale data). */
  update(patch: Partial<SetupData> | ((d: SetupData) => Partial<SetupData>)): void;
  /** Re-reads GET /setup (and /models, whose key status follows). */
  refresh(): Promise<SetupState>;
  go(step: StepId): void;
  close(): void;
}

async function load(): Promise<SetupData> {
  const c = actions.client();
  const [setup, profile, keys, claude, models] = await Promise.all([
    c.setupState(), c.getProfile(), c.listKeys(), c.claudeCodeStatus(), c.models(),
  ]);
  return { setup, profile, keys, claude, models };
}

export function SetupWizard({ reopened, onClose }: { reopened: boolean; onClose: () => void }): JSX.Element {
  const [data, setData] = useState<SetupData | null>(null);
  const [step, setStep] = useState<StepId | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setError(null);
    Promise.resolve().then(load).then((d) => {
      if (!live) return;
      setData(d);
      setStep((s) => s ?? resumeStep(d.setup, {
        reopened, modelSkipped: skipped("model"), onshapeSkipped: skipped("onshape"), profileNamed: !!d.profile.display_name.trim(),
      }));
    }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, [attempt]);

  // Esc never closes the wizard by accident; the explicit Close button does.
  const update: StepProps["update"] = (patch) => setData((d) => (d ? { ...d, ...(typeof patch === "function" ? patch(d) : patch) } : d));
  const refresh = async (): Promise<SetupState> => {
    const c = actions.client();
    const [setup, models] = await Promise.all([c.setupState(), c.models().catch(() => null)]);
    setData((d) => (d ? { ...d, setup, models: models ?? d.models } : d));
    return setup;
  };

  const props: StepProps | null = data ? { data, update, refresh, go: setStep, close: onClose } : null;
  const current = STEPS.findIndex((s) => s.id === step);
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root);
  // a new step: focus its first control (the trap keeps Tab inside the wizard)
  useEffect(() => {
    const body = root.current?.querySelector<HTMLElement>(".setup-body");
    // (not when the step was picked in the step bar: focus stays on the bar)
    if (!body || !step || body.contains(document.activeElement) || document.activeElement?.closest(".setup-steps")) return;
    body.querySelector<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled])")?.focus();
  }, [step]);

  const status = (id: StepId, i: number): StepStatus => (id === step ? "current" : data && stepDone(id, data, i < current) ? "done" : "todo");
  // ←/→ (and Home/End) move between the step buttons; Enter or Space opens the focused step.
  const onStepKey = (e: KeyboardEvent): void => {
    const btns = [...(root.current?.querySelectorAll<HTMLButtonElement>(".setup-step-btn") ?? [])];
    const i = btns.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    const n = btns.length;
    const next = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    btns[next].focus();
  };

  return (
    <div ref={root} class="setup" role="dialog" aria-modal="true" aria-label="Set up Mises">
      <header class="setup-head">
        <span class="setup-title">Set up Mises</span>
        <nav aria-label="Setup steps">
          <ol class="setup-steps" onKeyDown={onStepKey}>
            {STEPS.map((s, i) => {
              const st = status(s.id, i);
              return (
                <li key={s.id}>
                  <button type="button" class="setup-step-btn" data-status={st} aria-current={st === "current" ? "step" : undefined}
                    aria-label={st === "current" ? s.label : `${s.label} (${st === "done" ? "done" : "to do"})`}
                    disabled={!props} onClick={() => setStep(s.id)}>
                    <span class="setup-step-mark" aria-hidden="true">{st === "done" ? "✓" : st === "current" ? "●" : "○"}</span>
                    <span class="setup-step-name">{s.label}</span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>
        <Button class="setup-close" onClick={onClose}>Close</Button>
      </header>
      <div class="setup-body">
        {error && (
          <div class="setup-card">
            <div class="setup-error text" role="alert">{error}</div>
            <div class="setup-actions"><Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button></div>
          </div>
        )}
        {!error && !props && <div class="faint">loading…</div>}
        {props && step === "welcome" && <WelcomeStep {...props} />}
        {props && step === "model" && <ModelStep {...props} />}
        {props && step === "onshape" && <OnshapeStep {...props} />}
        {props && step === "profile" && <ProfileStep {...props} />}
        {props && step === "done" && <DoneStep {...props} />}
      </div>
    </div>
  );
}
