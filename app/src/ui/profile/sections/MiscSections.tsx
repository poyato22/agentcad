// Data, Setup and About: small read-mostly sections.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { isTauri } from "@tauri-apps/api/core";
import { Button } from "../../common/Button";
import { replayIntro } from "../../boot/boot";
import { closeProfile } from "../open";
import { openSetupWizard } from "../../setup/gate";
import { SystemCheck } from "../../system/SystemCheck";
import { Card, SectionBox, type SectionProps } from "../common";

/** Fallback when /status has no data_dir: the core's default data home in `~` form (the webview can't read $HOME). */
export const DATA_DIR = "~/Library/Application Support/Mises";

export function DataSection({ data }: SectionProps): JSX.Element {
  const dir = data.status?.data_dir || DATA_DIR;
  const [note, setNote] = useState<string | null>(null);
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(dir);
      setNote("copied");
    } catch {
      setNote("copy failed: select the path and press ⌘C");
    }
  }
  // No Tauri opener command exists (and no new capabilities are added), so "Show in Finder" is "Copy path".
  return (
    <SectionBox title="Data">
      <Card title="Data folder">
        <p class="dim">Runs, parts, the CAD mirror and the profile.</p>
        <code class="profile-code text" data-testid="data-path">{dir}</code>
        <div class="profile-row">
          <Button onClick={() => { void copy(); }}>Copy path</Button>
          {note && <span class="dim">{note}</span>}
        </div>
      </Card>
    </SectionBox>
  );
}

export function SystemSection(): JSX.Element {
  return (
    <SectionBox title="System">
      <Card title="Welcome intro">
        <p class="dim">Play the cube-assembling welcome again now. Click or press any key to skip it.</p>
        <div class="profile-row">
          <Button onClick={() => { closeProfile(); replayIntro(); }}>Replay intro</Button>
        </div>
      </Card>
      <SystemCheck />
    </SectionBox>
  );
}

export function SetupSection({ onClose }: { onClose: () => void }): JSX.Element {
  return (
    <SectionBox title="Setup">
      <Card title="First-run setup">
        <p class="dim">Profile, model and Onshape. Saved settings are kept.</p>
        <div class="profile-row">
          <Button onClick={() => { openSetupWizard(); onClose(); }}>Run setup again</Button>
        </div>
      </Card>
    </SectionBox>
  );
}

async function appVersion(): Promise<string> {
  try {
    if (!isTauri()) return "dev";
    const { getVersion } = await import("@tauri-apps/api/app");
    return await getVersion();
  } catch {
    return "unknown";
  }
}

export function AboutSection({ data }: SectionProps): JSX.Element {
  const [app, setApp] = useState("…");
  useEffect(() => {
    let live = true;
    void appVersion().then((v) => { if (live) setApp(v); });
    return () => { live = false; };
  }, []);
  const claude = data.claude.installed ? (data.claude.version ?? "unknown") : "not installed";
  return (
    <SectionBox title="About">
      <Card title="Versions">
        <dl class="profile-dl">
          <dt class="label">Mises app</dt><dd>{app}</dd>
          <dt class="label">Core</dt><dd>{data.status?.core_version ?? "unknown"}</dd>
          <dt class="label">Your Claude Code</dt><dd>{claude}</dd>
        </dl>
      </Card>
    </SectionBox>
  );
}
