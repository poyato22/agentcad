import type { JSX } from "preact";
import { Button } from "../../common/Button";
import type { StepProps } from "../SetupWizard";
import { StepHead } from "./common";

export function WelcomeStep({ go }: StepProps): JSX.Element {
  return (
    <div class="setup-step">
      <StepHead title="Welcome to AgentCAD" />
      <p>AgentCAD designs and checks mechanical parts with AI agents. Describe a change; agents edit the part, measure it, and ask before anything reaches Onshape.</p>
      <p>Stored on this Mac: keys in the macOS Keychain, settings and parts in the AgentCAD data folder.</p>
      <div class="setup-actions">
        <Button variant="ink" onClick={() => go("profile")}>Start</Button>
      </div>
    </div>
  );
}
