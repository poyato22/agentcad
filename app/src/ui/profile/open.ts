// Open/close state of the Profile panel. The top-bar Profile button, the setup gate's
// "connect a model" notice and the new-run dialog's "Sign in in Profile" open it.
import { signal, type Signal } from "@preact/signals";

export type ProfileSectionId = "profile" | "models" | "claude" | "onshape" | "agents" | "data" | "system" | "setup" | "developer" | "about";

export const profileOpen: Signal<boolean> = signal<boolean>(false);
/** The section the panel opens at. */
export const profileSection: Signal<ProfileSectionId> = signal<ProfileSectionId>("profile");

export function openProfile(section: ProfileSectionId = "profile"): void {
  profileSection.value = section;
  profileOpen.value = true;
}

export function closeProfile(): void { profileOpen.value = false; }

/** Bumped when the profile was saved outside the Profile panel (the setup wizard), so the top-bar
 *  button re-reads the display name. */
export const profileVersion: Signal<number> = signal<number>(0);
export function noteProfileChanged(): void { profileVersion.value++; }
