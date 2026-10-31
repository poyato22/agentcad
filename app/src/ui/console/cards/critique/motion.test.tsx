// Plan 9 G-D: an agent's presented animation / section sweep (the real core's cards, core/tests/
// fixtures/ui/motion) parse into motion cards that open the build view full window.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { Step } from "../../../../api/types";
import { immersive, resetAssemblyState } from "../../../assembly/state";
import { parseViewSpec } from "../../../viewer/modes/viewSpec";
import { g4Announcement, type PresentedView } from "./announce";
import { motionMeta } from "./MotionCard";
import { ViewCard } from "./ViewCard";

const STEPS = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui/motion/motion_steps.json"), "utf8")) as Step[];
const VIEWS: PresentedView[] = STEPS.flatMap((s, i) => {
  const a = g4Announcement(s);
  return a?.kind === "view_presented" ? [{ ...a.view, key: `v${i}` }] : [];
});
const ANIM = VIEWS.find((v) => v.spec.kind === "animation") as PresentedView;
const SWEEP = VIEWS.find((v) => v.spec.kind === "section_sweep") as PresentedView;

afterEach(() => { cleanup(); resetAssemblyState(); });

describe("motion view specs", () => {
  it("parse_the_real_cards_and_refuse_a_motion_with_nothing_to_play", () => {
    expect(ANIM.spec.animation?.steps).toHaveLength(2);
    expect(SWEEP.spec.sweep?.axis).toBe("x");
    expect(parseViewSpec({ kind: "animation", target: "p", animation: { steps: [] } })).toBeNull();
    expect(parseViewSpec({ kind: "animation", target: "p" })).toBeNull();
    expect(parseViewSpec({ kind: "section_sweep", target: "p", sweep: { axis: "w" } })).toBeNull();
    const sw = parseViewSpec({ kind: "section_sweep", target: "p", sweep: { axis: "z", from: "1", duration_s: -3 } });
    expect(sw?.sweep).toEqual({ axis: "z", from: null, to: null, duration_s: 4 });
    // untrusted numbers: loops clamp, a step without a joint is dropped
    const a = parseViewSpec({ kind: "animation", target: "p", animation: { loops: 99, steps: [{ to: 3 }, { joint_id: "j", from: 0, to: 9, duration_s: 2 }] } });
    expect(a?.animation).toMatchObject({ loops: 10, duration_s: 2, steps: [{ joint_id: "j" }] });
  });

  it("say_what_they_show", () => {
    expect(motionMeta(ANIM)).toBe("Open and shut the lid · 2 moves · 3.5 s · plays 2×");
    expect(motionMeta(SWEEP)).toBe("Cuts across X from -20 to 60 mm");
  });
});

describe("motion cards", () => {
  it("an_animation_card_opens_the_build_view_full_window_playing_its_steps", () => {
    render(<ViewCard view={ANIM} group={VIEWS} />);
    const card = screen.getByTestId("motion-card");
    expect(card.dataset.kind).toBe("animation");
    expect(card.textContent).toContain("The lid swings 120° on its hinge and closes again");
    expect(screen.queryByTestId("live-view")).toBeNull();               // never a LiveView: the assembly plays it
    fireEvent.click(screen.getByRole("button", { name: "Play the animation full window" }));
    const imm = immersive.value;
    expect(imm?.mode).toBe("animation");
    if (imm?.mode !== "animation") return;
    expect(imm.steps).toEqual(ANIM.spec.animation?.steps);
    expect(imm.loop).toBe(true);
    expect(imm.title).toBe("Open and shut the lid");
  });

  it("a_sweep_card_opens_the_sweep", () => {
    render(<ViewCard view={SWEEP} group={VIEWS} />);
    fireEvent.click(screen.getByRole("button", { name: "Sweep the section full window" }));
    expect(immersive.value).toMatchObject({ mode: "sweep", sweep: { axis: "x", from: -20, to: 60 }, caption: SWEEP.spec.caption });
  });
});
