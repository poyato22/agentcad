// Missing solver: exactly what to install and the command, plus "Check again". Never a crash.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { SimSolvers, SolverInfo } from "../../../api/sims";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";

const WHY: Record<keyof SimSolvers, string> = {
  ccx: "Studies solve with CalculiX (ccx), a separate program Mises runs on its own. It is not installed.",
  gmsh: "Studies mesh the part with gmsh, which ships with the Mises core. It is missing.",
};

/** The command to copy: the `quoted` part of a hint like "run `uv sync` in core/", else all of it. */
export function installCommand(install: string): string {
  const m = /`([^`]+)`/.exec(install);
  return (m ? m[1] : install).trim();
}

function Missing({ k, info }: { k: keyof SimSolvers; info: SolverInfo }): JSX.Element {
  const [copied, setCopied] = useState(false);
  return (
    <div class="st-install-item">
      <p><span class="st-band-title">{info.name} is not installed</span></p>
      <p class="dim">{WHY[k]} Install it with:</p>
      <div class="st-cmd">
        <code class="text">{info.install}</code>
        <Button variant="ghost" aria-label={`Copy the install command for ${info.name}`} onClick={() => {
          void navigator.clipboard?.writeText(installCommand(info.install)).then(() => setCopied(true), () => setCopied(false));
        }}>{copied ? "copied" : "copy"}</Button>
      </div>
    </div>
  );
}

export function InstallCard({ solvers, onRecheck, checking }: { solvers: SimSolvers; onRecheck: () => void; checking: boolean }): JSX.Element | null {
  const missing = (Object.keys(solvers) as (keyof SimSolvers)[]).filter((k) => !solvers[k].found);
  if (missing.length === 0) return null;
  return (
    <section class="st-install st-band st-band-red" data-testid="install-card" aria-label="Solver missing" role="alert">
      <Led tone="red" />
      <div>
        {missing.map((k) => <Missing key={k} k={k} info={solvers[k]} />)}
        <Button onClick={onRecheck} disabled={checking}>{checking ? "checking…" : "Check again"}</Button>
      </div>
    </section>
  );
}
