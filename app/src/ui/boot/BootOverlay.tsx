// The boot gate that sits over the shell: loading → (first launch) intro → one-time system check →
// ready. In the real Tauri app it runs on every launch; on the web/dev it stays out of the way (the
// core is already up) unless the user asks to replay the intro from Profile. A crash is left to the
// top-bar core LED (reason + click-to-restart); Loading's own error is the "taking too long" case.
import { isTauri } from "@tauri-apps/api/core";
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { conn, shellStatus } from "../../state/store";
import { SystemCheck } from "../system/SystemCheck";
import {
  clearReplay, introSeen, markIntroSeen, markSystemCheckSeen, replayRequested, systemCheckSeen, type BootPhase,
} from "./boot";
import { Intro } from "./Intro";
import { Loading } from "./Loading";

export interface BootOverlayProps {
  /** Forces the gate on/off (tests); otherwise on inside Tauri, or when a replay was requested. */
  enabled?: boolean;
}

export function BootOverlay({ enabled }: BootOverlayProps): JSX.Element | null {
  const [phase, setPhase] = useState<BootPhase>("loading");
  const [replaying, setReplaying] = useState(false);
  const stream = conn.value;
  const shell = shellStatus.value;
  const replay = replayRequested.value;
  const on = enabled ?? (isTauri() || replay);

  // Leave "loading" once the stream is open.
  useEffect(() => {
    if (phase !== "loading" || stream !== "open") return;
    if (!introSeen() || replay) {
      if (replay) { setReplaying(true); clearReplay(); }
      setPhase("intro");
    } else {
      setPhase("ready");
    }
  }, [phase, stream, replay]);

  // Replay requested while already live.
  useEffect(() => {
    if (replay && phase === "ready") { setReplaying(true); clearReplay(); setPhase("intro"); }
  }, [replay, phase]);

  if (!on || phase === "ready") return null;

  if (phase === "loading") {
    // Defer a crash/stopped to the shell's own core LED so it stays reachable.
    if (shell.state === "crashed" || shell.state === "stopped") return null;
    return <Loading />;
  }

  if (phase === "intro") {
    return (
      <Intro onDone={() => {
        markIntroSeen();
        if (replaying) { setReplaying(false); setPhase("ready"); }
        else if (!systemCheckSeen()) setPhase("systemcheck");
        else setPhase("ready");
      }} />
    );
  }

  // systemcheck
  return <SystemCheck standalone onDone={() => { markSystemCheckSeen(); setPhase("ready"); }} />;
}
