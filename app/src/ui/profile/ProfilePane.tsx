// Profile panel: a right-hand sheet with seven sections (Profile, Models, Onshape, Agents & budgets,
// Data, Setup, About). App mounts <ProfilePane/> once; the top-bar Profile button opens it
// (ui/profile/open.ts), as do the "connect a model" notice and the new-run dialog.
// Every section reads and writes the core routes directly; secrets only ever go out (password
// inputs, cleared after a save) and are never rendered. Removing anything asks inside the panel.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { useFocusTrap } from "../common/focusTrap";
import { describeError, type ProfileData, type SectionProps } from "./common";
import { AgentsSection } from "./sections/AgentsSection";
import { AboutSection, DataSection, SetupSection } from "./sections/MiscSections";
import { ModelsSection } from "./sections/ModelsSection";
import { OnshapeSection } from "./sections/OnshapeSection";
import { ProfileSection } from "./sections/ProfileSection";
import "./profile.css";

export const SECTIONS = [
  { id: "profile", label: "Profile" },
  { id: "models", label: "Models" },
  { id: "onshape", label: "Onshape" },
  { id: "agents", label: "Agents & budgets" },
  { id: "data", label: "Data" },
  { id: "setup", label: "Setup" },
  { id: "about", label: "About" },
] as const;
export type SectionId = (typeof SECTIONS)[number]["id"];

async function load(): Promise<ProfileData> {
  const c = actions.client();
  // profile, keys and Claude Code status are required; usage, models and agents degrade quietly
  const [profile, keys, claude, status, models, agents] = await Promise.all([
    c.getProfile(), c.listKeys(), c.claudeCodeStatus(),
    c.status().catch(() => null), c.models().catch(() => null), c.agents().catch(() => []),
  ]);
  return { profile, keys, claude, status, models, agents };
}

export function ProfilePane({ open, onClose, section }: { open: boolean; onClose: () => void; section?: SectionId }): JSX.Element | null {
  if (!open) return null;
  return <ProfilePaneBody onClose={onClose} initial={section ?? "profile"} />;
}

function ProfilePaneBody({ onClose, initial }: { onClose: () => void; initial: SectionId }): JSX.Element {
  const [data, setData] = useState<ProfileData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [section, setSection] = useState<SectionId>(initial);
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root, () => root.current?.querySelector<HTMLElement>(".profile-nav-item[aria-current]") ?? null);

  useEffect(() => {
    let live = true;
    setError(null);
    Promise.resolve().then(load).then(
      (d) => { if (live) setData(d); },
      (e: unknown) => { if (live) setError(describeError(e)); },
    );
    return () => { live = false; };
  }, [attempt]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const update: SectionProps["update"] = (patch) => setData((d) => (d ? { ...d, ...(typeof patch === "function" ? patch(d) : patch) } : d));
  const props = data ? { data, update } : null;

  return (
    <div ref={root} class="profile" role="dialog" aria-modal="true" aria-label="Profile">
      <header class="profile-head">
        <span class="profile-title">Profile</span>
        <Button variant="ghost" class="profile-close" onClick={onClose}>Close</Button>
      </header>
      <div class="profile-main">
        <nav class="profile-nav" aria-label="Profile sections">
          {SECTIONS.map((s) => (
            <button key={s.id} type="button" class="profile-nav-item" aria-current={s.id === section ? "page" : undefined} onClick={() => setSection(s.id)}>
              {s.label}
            </button>
          ))}
        </nav>
        <div class="profile-body">
          {error && (
            <div class="profile-card">
              <div class="profile-error text" role="alert">{error}</div>
              <div class="profile-row"><Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button></div>
            </div>
          )}
          {!error && !props && <div class="faint">loading…</div>}
          {props && section === "profile" && <ProfileSection {...props} />}
          {props && section === "models" && <ModelsSection {...props} />}
          {props && section === "onshape" && <OnshapeSection {...props} />}
          {props && section === "agents" && <AgentsSection {...props} />}
          {props && section === "data" && <DataSection {...props} />}
          {props && section === "setup" && <SetupSection onClose={onClose} />}
          {props && section === "about" && <AboutSection {...props} />}
        </div>
      </div>
    </div>
  );
}
