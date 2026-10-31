import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../../api/client";
import type { PartSummary } from "../../../../api/types";
import { bindClient, parts, resetStore } from "../../../../state/store";
import { liveContexts } from "../../../viewer/modes/LiveView";
import type { RendererLike } from "../../../viewer/scene";
import { parseViewSpec } from "../../../viewer/modes/viewSpec";
import type { CritiqueRound, PresentedView } from "./announce";
import { CritiqueCard } from "./CritiqueCard";
import { resetImageCache } from "./images";
import { ModelWarning } from "./ModelWarning";
import { ViewCard } from "./ViewCard";
import { ViewImmersiveRoot } from "./ViewImmersive";
import { liveCard, openViewImmersive, viewImmersive } from "./viewState";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;

/** A renderer double that remembers the last scene it drew and whether its context was given back. */
function renderers() {
  const made: { scene: THREE.Scene | null; lost: boolean; disposed: boolean }[] = [];
  const factory = (): RendererLike & { forceContextLoss(): void } => {
    const r = { scene: null as THREE.Scene | null, lost: false, disposed: false };
    made.push(r);
    return {
      info: { memory: { geometries: 0, textures: 0 } },
      render: (scene: THREE.Scene) => { r.scene = scene; },
      setSize: vi.fn(), setPixelRatio: vi.fn(), setClearColor: vi.fn(),
      dispose: () => { r.disposed = true; },
      forceContextLoss: () => { r.lost = true; },
    };
  };
  return { made, factory, live: () => made.filter((m) => !m.lost) };
}

const part = (id: string): PartSummary => ({ part_id: id, project_id: "b1", vars: {}, metrics: {} as never, mtime: 1 });

function view(key: string, spec: Record<string, unknown>): PresentedView {
  return { key, runId: "r1", title: "", detail: "", spec: parseViewSpec({ target: { project_id: "b1" }, caption: `${key} caption`, ...spec })!, thumbnail: null };
}

function sceneParts(s: THREE.Scene): THREE.Object3D[] {
  return s.children.filter((o) => o instanceof THREE.Group && o.name !== "section-caps");
}
function faceMaterials(o: THREE.Object3D): THREE.Material[] {
  const out: THREE.Material[] = [];
  o.traverse((x) => { if (x instanceof THREE.Mesh) out.push(x.material as THREE.Material); });
  return out;
}

beforeEach(() => {
  resetStore();
  resetImageCache();
  bindClient({ partGlb: vi.fn(async () => ({ bytes: fixtureBytes(), etag: '"x"' })), renderImage: vi.fn(async () => new Blob(["png"], { type: "image/png" })) } as unknown as CoreClient);
  parts.value = [part("pi_case"), part("pi_board")];
  liveCard.value = null;
  viewImmersive.value = null;
});
afterEach(() => { cleanup(); resetStore(); vi.unstubAllGlobals(); });

describe("CritiqueCard", () => {
  const round: CritiqueRound = {
    runId: "r1", title: "fixed the trailing edge", detail: "", round: 2, maxRounds: 3, score: 0.86, threshold: 0.8, stopReason: "passed",
    before: { id: "r1b" }, after: { dataUrl: "data:image/png;base64,iVBORw0KGgo=" },
    fixed: [{ view: "top", issue: "straight trailing edge", severity: "high", fix: "sawtooth" }], open: [{ view: "side", issue: "nose too blunt", severity: "low", fix: null }],
    iou: [{ view: "top", value: 0.91 }], parts: ["b2"], done: true,
  };

  it("is compact (thumbnails, round, score) and expands on click to the issues", async () => {
    render(<CritiqueCard round={round} />);
    const card = screen.getByTestId("critique-card");
    expect(card.textContent).toContain("Round 2 of 3");
    expect(screen.getByTestId("critique-score").textContent).toBe("score 0.86 / 0.80");
    expect(card.textContent).toContain("1 fixed · 1 open");
    expect(screen.queryByTestId("critique-details")).toBeNull();
    await waitFor(() => expect(card.querySelectorAll("img")).toHaveLength(2));
    fireEvent.click(card.querySelector("button")!);
    const more = screen.getByTestId("critique-details");
    expect(more.textContent).toContain("straight trailing edge");
    expect(more.textContent).toContain("fixed: sawtooth");
    expect(more.textContent).toContain("nose too blunt");
    expect(more.textContent).toContain("0.91");
    expect(more.textContent).toContain("stopped: the score passed");
  });
});

describe("ViewCard", () => {
  async function renderOne(spec: Record<string, unknown>) {
    const r = renderers();
    const v = view("v1", spec);
    render(<ViewCard view={v} group={[v]} deps={{ createRenderer: r.factory }} />);
    await waitFor(() => expect(screen.getByTestId("live-view").getAttribute("data-state")).toBe("ready"));
    await waitFor(() => expect(r.made[0]?.scene).not.toBeNull());
    return { r, scene: () => r.made[0].scene! };
  }

  it("a view of parts the build no longer has says so instead of a failing live stage (FW-app)", () => {
    parts.value = [part("other")];
    const v = view("gone", { kind: "orbit", target: { part_id: "deleted_part" }, parts: ["deleted_part"] });
    render(<ViewCard view={v} group={[v]} deps={{ createRenderer: renderers().factory }} />);
    expect(screen.queryByTestId("live-view")).toBeNull();
    expect(screen.getByTestId("view-card-still").textContent).toBe("Its parts are no longer in this build.");
  });

  it("orbit: renders live with its caption, orbiting", async () => {
    await renderOne({ kind: "orbit" });
    expect(screen.getByTestId("view-card").getAttribute("data-live")).toBe("true");
    expect(screen.getByTestId("view-card").textContent).toContain("v1 caption");
    expect(screen.getByTestId("live-view").getAttribute("data-orbit")).toBe("playing");
    fireEvent.click(screen.getByTestId("view-card-play"));
    expect(screen.getByTestId("live-view").getAttribute("data-orbit")).toBe("paused");
  });

  it("orbit is paused under reduced motion", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} }));
    await renderOne({ kind: "orbit" });
    expect(screen.getByTestId("live-view").getAttribute("data-orbit")).toBe("paused");
  });

  it("a polished part keeps its finish in the view card (I5: PBR metal, not the matte look)", async () => {
    parts.value = [{ ...part("pi_case"), appearance: { preset: "polished_metal", color: "#b0b4ba", metalness: 1, roughness: 0.15, opacity: 1 } }, part("pi_board")];
    const { scene } = await renderOne({ kind: "orbit" });
    await waitFor(() => {
      const [a, b] = sceneParts(scene());
      expect(faceMaterials(a).every((m) => m instanceof THREE.MeshStandardMaterial && m.metalness === 1)).toBe(true);
      expect(faceMaterials(b).every((m) => m instanceof THREE.MeshLambertMaterial)).toBe(true);
    });
  });

  it("internal: the named parts turn see-through", async () => {
    const { scene } = await renderOne({ kind: "internal", xray_parts: ["pi_case"] });
    await waitFor(() => {
      const [a, b] = sceneParts(scene());
      expect(faceMaterials(a).every((m) => m.transparent && m.opacity < 1)).toBe(true);
      expect(faceMaterials(b).every((m) => !m.transparent)).toBe(true);
    });
  });

  it("section: the cut is capped", async () => {
    const { scene } = await renderOne({ kind: "section", section: { origin: [0, 0, 0], normal: [0, 1, 0] } });
    await waitFor(() => {
      const caps = scene().getObjectByName("section-caps")!;
      expect(caps.visible).toBe(true);
      expect(caps.children.filter((c) => c.name.startsWith("cap:"))).toHaveLength(2);
    });
  });

  it("exploded: the parts move apart", async () => {
    const { scene } = await renderOne({ kind: "exploded" });
    await waitFor(() => {
      const [a, b] = sceneParts(scene());
      expect(Math.abs(a.position.z - b.position.z)).toBeGreaterThan(1);
    });
  });

  it("subset: the named parts stay solid, the rest are ghosts", async () => {
    const { scene } = await renderOne({ kind: "subset", parts: ["pi_board"] });
    await waitFor(() => {
      const [b, a] = sceneParts(scene());   // the shown part first, then its ghosted context
      expect(faceMaterials(a).every((m) => m.opacity < 0.2)).toBe(true);
      expect(faceMaterials(b).every((m) => m.opacity === 1)).toBe(true);
    });
  });

  it("only one card holds a live WebGL context; hover moves it and the others keep a still", async () => {
    const r = renderers();
    const vs = [view("a", { kind: "orbit" }), view("b", { kind: "section" }), view("c", { kind: "exploded" })];
    render(<>{vs.map((v) => <ViewCard key={v.key} view={v} group={vs} deps={{ createRenderer: r.factory }} />)}</>);
    const cards = () => screen.getAllByTestId("view-card");
    await waitFor(() => expect(cards().filter((c) => c.getAttribute("data-live") === "true")).toHaveLength(1));
    expect(cards()[2].getAttribute("data-live")).toBe("true");   // the newest
    expect(liveContexts.value).toBe(1);
    expect(r.live()).toHaveLength(1);
    fireEvent.pointerEnter(cards()[0]);
    await waitFor(() => expect(cards()[0].getAttribute("data-live")).toBe("true"));
    expect(cards().filter((c) => c.getAttribute("data-live") === "true")).toHaveLength(1);
    // the claim lands after the hover settles (a timer), so its effects run a tick later; never two
    await waitFor(() => expect(liveContexts.value).toBe(1));
    await waitFor(() => expect(r.live()).toHaveLength(1));
    expect(r.made.length).toBe(2);
    expect(cards()[2].querySelector("[data-testid=view-card-still]")).not.toBeNull();
  });
});

describe("live context hygiene", () => {
  it("sweeping the pointer across a card does not move the live context", async () => {
    const r = renderers();
    const vs = [view("a", { kind: "orbit" }), view("b", { kind: "section" })];
    render(<>{vs.map((v) => <ViewCard key={v.key} view={v} group={vs} deps={{ createRenderer: r.factory }} />)}</>);
    const cards = () => screen.getAllByTestId("view-card");
    await waitFor(() => expect(cards()[1].getAttribute("data-live")).toBe("true"));
    const made = r.made.length;
    fireEvent.pointerEnter(cards()[0]);
    fireEvent.pointerLeave(cards()[0]);
    await new Promise((res) => setTimeout(res, 300));
    expect(cards()[1].getAttribute("data-live")).toBe("true");
    expect(r.made.length).toBe(made);
  });

  it("a viewer that fails after its renderer was made gives the context back", () => {
    const lost = vi.fn();
    const v = view("a", { kind: "orbit" });
    render(<ViewCard view={v} group={[v]} deps={{ createRenderer: () => ({
      info: { memory: { geometries: 0, textures: 0 } }, render: () => {}, setSize: vi.fn(), setPixelRatio: vi.fn(),
      setClearColor: () => { throw new Error("context lost"); }, dispose: vi.fn(), forceContextLoss: lost,
    }) }} />);
    expect(screen.getByTestId("live-view").getAttribute("data-state")).toBe("no-webgl");
    expect(lost).toHaveBeenCalledTimes(1);
    expect(liveContexts.value).toBe(0);
  });
});

describe("immersive view", () => {
  it("opens from a card in that view, steps with ⌘[ / ⌘], closes with Esc; no card stays live meanwhile", async () => {
    const r = renderers();
    const vs = [view("a", { kind: "orbit" }), view("b", { kind: "section", section: { origin: [0, 0, 0], normal: [1, 0, 0] } }), view("c", { kind: "subset", parts: ["pi_board"] })];
    render(<><ViewCard view={vs[1]} group={vs} deps={{ createRenderer: r.factory }} /><ViewImmersiveRoot deps={{ createRenderer: r.factory }} /></>);
    fireEvent.click(screen.getByTestId("view-card-open"));
    const imm = await screen.findByTestId("view-immersive");
    expect(imm.getAttribute("data-kind")).toBe("section");
    expect(screen.getByTestId("vimm-pos").textContent).toBe("2 / 3");
    expect(screen.getByTestId("view-card").getAttribute("data-live")).toBe("false");
    await waitFor(() => expect(liveContexts.value).toBe(1));
    // every view mode is there, already set to the agent's view
    await waitFor(() => expect(screen.getByRole("button", { name: "section" }).getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByRole("button", { name: "x-ray" })).toBeTruthy();
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", metaKey: true })); });
    expect(screen.getByTestId("view-immersive").getAttribute("data-kind")).toBe("subset");
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", metaKey: true })); });
    expect(screen.getByTestId("view-immersive").getAttribute("data-kind")).toBe("orbit");
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true })); });
    expect(screen.getByTestId("view-immersive").getAttribute("data-kind")).toBe("subset");
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(screen.queryByTestId("view-immersive")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("view-card").getAttribute("data-live")).toBe("true"));
  });

  it("the close button closes it too", async () => {
    const vs = [view("a", { kind: "orbit" })];
    render(<ViewImmersiveRoot deps={{ createRenderer: renderers().factory }} />);
    act(() => openViewImmersive(vs, 0));
    fireEvent.click(await screen.findByTestId("vimm-close"));
    expect(screen.queryByTestId("view-immersive")).toBeNull();
  });
});

describe("ModelWarning", () => {
  it("shows for shape-heavy work on a small local model, not otherwise", () => {
    const { rerender } = render(<ModelWarning run={{ provider: "ollama", model: "qwen3:8b", shape_complexity: { level: "high", keywords: ["aircraft"] } }} />);
    const chip = screen.getByTestId("model-warning");
    expect(chip.textContent).toContain("small model for shape work");
    expect(chip.getAttribute("title")).toContain("aircraft");
    rerender(<ModelWarning run={{ provider: "claude-code", model: "opus", shape_complexity: { level: "high" } }} />);
    expect(screen.queryByTestId("model-warning")).toBeNull();
  });
});
