import type { JSX } from "preact";
import { Button } from "../../common/Button";
import type { StepProps } from "../SetupWizard";
import { StepHead } from "./common";

export function WelcomeStep({ go }: StepProps): JSX.Element {
  return (
    <div class="setup-step">
      <StepHead title="Welcome to Mises" />
      <p>Mises designs and checks mechanical parts with AI agents. You describe the change; agents edit the part, measure it and ask before anything reaches Onshape.</p>
      <p>Everything stays on this Mac: keys go to the macOS Keychain, settings and parts to your Mises data folder. There is no Mises account.</p>
      <p class="dim">Setup takes a minute: connect a model, optionally connect Onshape, and set a few defaults. You can close it at any time; what you saved stays saved.</p>
      <div class="setup-actions">
        <Button variant="ink" onClick={() => go("model")}>Get started</Button>
      </div>
    </div>
  );
}
