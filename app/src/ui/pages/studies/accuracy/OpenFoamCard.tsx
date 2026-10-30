// The OpenFOAM row of the system check, on a wind study: ready, a user-click download of the
// pinned image (POST /system/install/openfoam, its output streamed as `system_install`), or how
// to get a container runtime. OpenFOAM is optional: without it the built-in solver runs and the
// result says so. Nothing is downloaded until the button is pressed.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { OPENFOAM_ID_RE } from "../../../../api/accuracy";
import type { CoreClient } from "../../../../api/client";
import { onCoreEvent } from "../../../../api/events";
import type { StudySolvers } from "../../../../api/studies";
import { isInstallDep, type MisesEvent } from "../../../../api/types";
import { Button } from "../../../common/Button";
import { ExternalLink } from "../../../common/ExternalLink";
import { Led } from "../../../common/Led";
import { safeUrl } from "../../../system/SystemCheck";

type Row = NonNullable<StudySolvers["cfd"]>[number];
interface Fix { kind?: string; label?: string; dep?: string; url?: string }

export function openFoamRow(s: StudySolvers | null): Row | null {
  return s?.cfd?.find((r) => OPENFOAM_ID_RE.test(r.id)) ?? null;
}

export function OpenFoamCard({ solvers, chosen, client, onInstalled, subscribe = onCoreEvent }: {
  solvers: StudySolvers | null;
  /** The wind settings ask for OpenFOAM (else the card is a one-line status). */
  chosen: boolean;
  client: () => Pick<CoreClient, "systemInstall">;
  onInstalled: () => void;
  subscribe?: (cb: (e: MisesEvent) => void) => () => void;
}): JSX.Element | null {
  const row = openFoamRow(solvers);
  const [lines, setLines] = useState<string[]>([]);
  const [state, setState] = useState<"idle" | "running" | "failed">("idle");
  const fix = (row?.fix ?? null) as Fix | null;
  const dep = fix?.dep ?? "openfoam";
  // only a dependency the app knows is ever sent (the core's INSTALLERS allowlist is the other guard)
  const knownDep = isInstallDep(dep);
  useEffect(() => subscribe((ev) => {
    if (ev.type !== "system_install" || ev.data.dep !== dep) return;
    if (typeof ev.data.line === "string" && ev.data.line) setLines((l) => [...l.slice(-200), ev.data.line as string]);
    if (ev.data.done === true) {
      setState(ev.data.ok === true ? "idle" : "failed");
      onInstalled();
    }
  }), [dep]);
  if (!row) return null;
  const ready = row.status === "ok";
  if (ready && !chosen) return null;
  const install = async () => {
    setState("running"); setLines([]);
    try {
      if (!knownDep) return;
      const r = await client().systemInstall(dep);
      if (!r.ok) { setState("failed"); setLines([r.fix?.label ? `Can't install here: ${r.fix.label}` : "The install was refused."]); }
    } catch (e) { setState("failed"); setLines([e instanceof Error ? e.message : String(e)]); }
  };
  const url = fix?.kind === "open_url" ? safeUrl(fix.url) : null;
  return (
    <section class={`st-band acc-of${ready ? "" : " st-band-amber"}`} data-testid="openfoam-card" data-status={row.status} aria-label="OpenFOAM">
      <Led tone={ready ? "green" : "amber"} />
      <div>
        <p><span class="st-band-title">{ready ? "OpenFOAM is ready" : "OpenFOAM is not set up"}</span></p>
        <p class="dim text">{row.detail}</p>
        {!ready && chosen && <p class="text">Until it is, this study runs on the built-in solver and is labelled that way.</p>}
        {!ready && fix?.kind === "install" && knownDep && (
          <Button onClick={() => { void install(); }} disabled={state === "running"} data-testid="openfoam-install">
            {state === "running" ? "Downloading…" : fix.label ?? "Download OpenFOAM"}
          </Button>
        )}
        {!ready && url && <p class="text"><ExternalLink href={url}>{fix?.label ?? "Get a container runtime"}</ExternalLink>, then check again.</p>}
        {lines.length > 0 && <pre class="syscheck-log acc-of-log" aria-label="OpenFOAM install log">{lines.join("\n")}</pre>}
        {state === "failed" && <p class="tone-red text" role="alert">The download did not finish. The built-in solver still works.</p>}
      </div>
    </section>
  );
}
