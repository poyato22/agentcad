// Top-bar Profile button: the display name's initials (or "Profile" before one is set). Opens the
// Profile panel. The name is re-read after each (re)connect, when Profile closes and when setup
// saves a profile (profileVersion).
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { actions, conn } from "../../state/store";
import { Button } from "../common/Button";
import { openProfile, profileOpen, profileVersion } from "../profile/open";

/** "Hartigan Deely" → "HD", "hartigan" → "H", "" → "". At most two letters. */
export function initials(name: string): string {
  return name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => [...w][0]?.toUpperCase() ?? "").join("");
}

export function ProfileButton(): JSX.Element {
  const [name, setName] = useState("");
  const connected = conn.value === "open";
  const open = profileOpen.value;
  const version = profileVersion.value;
  const [reconnects, setReconnects] = useState(0);
  const seenOpen = useRef(false);
  const failed = useRef(false);

  // read again on a reconnect; on the first connect only if the boot read failed (core not ready)
  useEffect(() => {
    if (!connected) return;
    const first = !seenOpen.current;
    seenOpen.current = true;
    if (!first || failed.current) setReconnects((n) => n + 1);
  }, [connected]);

  useEffect(() => {
    if (open) return;
    let live = true;
    failed.current = false;
    Promise.resolve().then(() => actions.client().getProfile()).then(
      (p) => { if (live) setName(p.display_name); },
      () => { failed.current = true; /* core not ready: the next connect reads it */ },
    );
    return () => { live = false; };
  }, [open, version, reconnects]);

  const short = initials(name);
  return (
    <Button class="profile-btn" aria-label="Profile" title={name ? `Profile · ${name}` : "Profile"} aria-haspopup="dialog"
      aria-expanded={open} onClick={() => openProfile()}>{short || "Profile"}</Button>
  );
}
