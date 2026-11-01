import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Build } from "../api/builds";
import type { CoreClient } from "../api/client";
import type { AgentRun } from "../api/types";
import { openPartId } from "../ui/assembly/focus";
import { immersive as playView } from "../ui/assembly/state";
import { activePage, resetDockMemory, setPage } from "../ui/pages/router";
import { immersive as studyView } from "../ui/pages/studies/immersive/state";
import { activeBuildId, builds, resetBuilds } from "./builds";
import { atHome, homeView, resetHome } from "./home";
import { canGoBack, canGoForward, goBack, goForward, installNav, navState, NAV_LIMIT, resetNav } from "./nav";
import { bindClient, resetStore, runs, selectedRunId } from "./store";

const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const b = (id: string): Build => ({ id, name: id, created_at: 1, updated_at: 1, trashed_at: null, part_count: 0, run_count: 0, onshape: null });
const run = (id: string, role: "lead" | "worker", project = "a"): AgentRun =>
  ({ id, role, project_id: project, created_at: id === "lead" ? 1 : 2, archived: false, status: "running", agent: "x", task: "t" } as unknown as AgentRun);

let off: () => void = () => {};
const trusted = { isTrusted: () => true, isBlocked: () => false };

beforeEach(async () => {
  resetStore(); resetBuilds(); resetHome(); resetDockMemory(); resetNav();
  openPartId.value = null; studyView.value = null; playView.value = null;
  bindClient({ setActiveBuild: vi.fn(async () => ({})), parts: vi.fn(async () => []) } as unknown as CoreClient);
  builds.value = [b("a"), b("b")];
  activeBuildId.value = "a";
  activePage.value = "design";
  runs.value = new Map([["lead", run("lead", "lead")], ["w1", run("w1", "worker")]]);
  off = installNav(trusted);
  await tick();
});
afterEach(() => { off(); });

describe("navigation history (E11)", () => {
  it("records each place once, and back / forward restore them", async () => {
    expect(navState().entries).toHaveLength(1);           // Home, at launch
    expect(canGoBack.value).toBe(false);
    atHome.value = false; await tick();                    // into the build (Design)
    openPartId.value = "arm"; await tick();                // a part alone
    setPage("studies"); await tick();
    expect(navState().entries).toHaveLength(4);
    expect(goBack()).toBe(true); await tick();
    expect(activePage.value).toBe("design");
    expect(openPartId.value).toBe("arm");
    goBack(); await tick();
    expect(openPartId.value).toBeNull();
    expect(atHome.value).toBe(false);
    goBack(); await tick();
    expect(atHome.value).toBe(true);
    expect(canGoBack.value).toBe(false);
    expect(canGoForward.value).toBe(true);
    goForward(); goForward(); goForward(); await tick();
    expect(activePage.value).toBe("studies");
    expect(canGoForward.value).toBe(false);
    // restoring never added steps
    expect(navState().entries).toHaveLength(4);
  });

  it("a new place after going back drops the forward steps", async () => {
    homeView.value = "library"; await tick();
    homeView.value = "onshape"; await tick();
    goBack(); await tick();
    expect(homeView.value).toBe("library");
    homeView.value = "trash"; await tick();
    expect(canGoForward.value).toBe(false);
    expect(navState().entries.map((e) => e.homeView)).toEqual(["builds", "library", "trash"]);
  });

  it("a sub-agent's thread is a step; back returns to the lead", async () => {
    atHome.value = false; selectedRunId.value = "lead"; await tick();
    selectedRunId.value = "w1"; await tick();
    goBack(); await tick();
    expect(selectedRunId.value).toBe("lead");
    goForward(); await tick();
    expect(selectedRunId.value).toBe("w1");
  });

  it("the dock's first pick of a run (from none) is not a step", async () => {
    atHome.value = false; await tick();
    const n = navState().entries.length;
    selectedRunId.value = "w1"; await tick();
    expect(navState().entries).toHaveLength(n);
  });

  it("a full-window view is a step: back closes it; stepping its studies is not", async () => {
    atHome.value = false; await tick();
    studyView.value = { simId: "s1", ids: ["s1", "s2"] }; await tick();
    studyView.value = { simId: "s2", ids: ["s1", "s2"] }; await tick();
    expect(navState().entries).toHaveLength(3);
    goBack(); await tick();
    expect(studyView.value).toBeNull();
    playView.value = { mode: "tour" }; await tick();
    goBack(); await tick();
    expect(playView.value).toBeNull();
  });

  it("back to another build switches to it", async () => {
    atHome.value = false; await tick();
    activeBuildId.value = "b"; await tick();
    goBack(); await tick(); await tick();
    expect(activeBuildId.value).toBe("a");
  });

  it("keeps at most NAV_LIMIT steps", async () => {
    for (let i = 0; i < NAV_LIMIT + 10; i++) { openPartId.value = `p${i}`; atHome.value = false; await tick(); }
    expect(navState().entries).toHaveLength(NAV_LIMIT);
  });

  it("⌘← / ⌘→ step back and forward, but not while typing in a field", async () => {
    atHome.value = false; await tick();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", metaKey: true }));
    await tick();
    expect(atHome.value).toBe(true);
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", metaKey: true, bubbles: true }));
    await tick();
    expect(atHome.value).toBe(true);                       // the caret moved, not the app
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", metaKey: true }));
    await tick();
    expect(atHome.value).toBe(false);
    input.remove();
  });

  it("the mouse's back / forward buttons step too", async () => {
    atHome.value = false; await tick();
    window.dispatchEvent(new MouseEvent("mouseup", { button: 3 }));
    await tick();
    expect(atHome.value).toBe(true);
    window.dispatchEvent(new MouseEvent("mouseup", { button: 4 }));
    await tick();
    expect(atHome.value).toBe(false);
  });

  it("is blocked under a dialog it doesn't own", async () => {
    off();
    off = installNav({ isTrusted: () => true, isBlocked: () => true });
    atHome.value = false; await tick();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", metaKey: true }));
    await tick();
    expect(atHome.value).toBe(false);
  });
});
