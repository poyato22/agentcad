import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakeCore, type FakeCore } from "../../../../test/fake-core/server";
import { CoreClient } from "../../../api/client";
import type { Question } from "../../../api/types";
import { bindClient, questions, resetStore } from "../../../state/store";
import { InterviewCard } from "./InterviewCard";

// jsdom can't make trusted clicks: follow a flag (the real-input path is covered by e2e).
let trusted = true;
vi.mock("../approvals/approvalKeys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../approvals/approvalKeys")>()),
  isTrustedActivation: () => trusted,
}));

const QID = "q_interview";
let core: FakeCore;
let clock = 0;

const answerCalls = () => core.state.calls.filter((c) => c.path.endsWith("/answer"));

const BATCH: Question = {
  question_id: QID, run_id: "r1", question: "A few questions about your drone:", options: [],
  items: [
    { id: "size", prompt: "Prop size?", options: ["3 in", "5 in", "7 in"], multi: false, rank: false, allow_text: true },
    { id: "mats", prompt: "Materials to allow?", options: ["PLA", "PETG"], multi: true, rank: false, allow_text: false },
    { id: "prio", prompt: "Rank priorities", options: ["weight", "impact"], multi: false, rank: true, allow_text: false },
  ],
};

function mount(q: Question = BATCH, arm = true) {
  core.state.questions.set(q.question_id, q);
  questions.value = new Map([[q.question_id, q]]);
  const r = render(<InterviewCard question={q} runLabel="engineer · design a 5-inch drone" />);
  if (arm) clock += 500;
  return r;
}

beforeEach(async () => {
  trusted = true;
  clock = 100_000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  core = await startFakeCore({ port: 0, token: "t", scenario: "base" });
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
});

afterEach(async () => {
  cleanup();
  resetStore();
  await core.stop();
});

describe("InterviewCard", () => {
  it("renders every sub-question as one card with an intro and a counter", () => {
    const { container } = mount();
    const card = container.querySelector("[data-interview]") as HTMLElement;
    expect(card.getAttribute("role")).toBe("group");
    expect(card.textContent).toContain("A few questions about your drone:");
    expect(container.querySelectorAll("[data-q]")).toHaveLength(3);
    expect(screen.getByText("Prop size?")).toBeTruthy();
    expect(container.querySelector('[data-q="mats"]')?.textContent).toContain("pick any");
    // the rank question is pre-filled in the given order, so it counts as answered
    const count = container.querySelector("[data-count]") as HTMLElement;
    expect(count.getAttribute("aria-live")).toBe("polite");
    expect(count.textContent).toBe("1/3 answered");
    expect(container.querySelector("[data-open]")?.textContent).toBe("Answer 1, 2 to send.");
  });

  it("requires every question answered before Send is enabled, and says which are open", () => {
    const { container } = mount();
    const send = screen.getByRole("button", { name: "Send answers" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "5 in" }));
    expect(container.querySelector("[data-count]")?.textContent).toBe("2/3 answered");
    expect(container.querySelector("[data-open]")?.textContent).toBe("Answer 2 to send.");
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "PETG" }));
    expect(send.disabled).toBe(false);
    expect(container.querySelector("[data-open]")).toBeNull();
  });

  it("posts the whole batch as a JSON {id: value} object, with the full ranking", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "5 in" }));
    fireEvent.click(screen.getByRole("button", { name: "PLA" }));
    fireEvent.click(screen.getByRole("button", { name: "PETG" }));
    fireEvent.click(screen.getByRole("button", { name: "Move impact up" }));
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(answerCalls()[0].path).toBe(`/questions/${QID}/answer`);
    const body = answerCalls()[0].body as { text: string };
    expect(JSON.parse(body.text)).toEqual({ size: "5 in", mats: ["PLA", "PETG"], prio: ["impact", "weight"] });
    await waitFor(() => expect(questions.value.has(QID)).toBe(false));
  });

  it("ranks by Alt+↑/↓ on a row and the move buttons disable at the ends", () => {
    const q: Question = { ...BATCH, items: [{ id: "prio", prompt: "Rank", options: ["weight", "impact", "stiffness"], multi: false, rank: true, allow_text: false }] };
    const { container } = mount(q);
    const rows = () => [...container.querySelectorAll<HTMLElement>("[data-rank]")].map((r) => r.dataset.rank);
    expect(rows()).toEqual(["weight", "impact", "stiffness"]);
    expect((screen.getByRole("button", { name: "Move weight up" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Move stiffness down" }) as HTMLButtonElement).disabled).toBe(true);
    const row = container.querySelector<HTMLElement>('[data-rank="stiffness"]')!;
    fireEvent.keyDown(row, { key: "ArrowUp", altKey: true });
    expect(rows()).toEqual(["weight", "stiffness", "impact"]);
    fireEvent.keyDown(container.querySelector<HTMLElement>('[data-rank="weight"]')!, { key: "ArrowDown", altKey: true });
    expect(rows()).toEqual(["stiffness", "weight", "impact"]);
    fireEvent.keyDown(container.querySelector<HTMLElement>('[data-rank="impact"]')!, { key: "ArrowUp" }); // no Alt: nothing
    expect(rows()).toEqual(["stiffness", "weight", "impact"]);
  });

  it("multi + typed text sends both", async () => {
    const q: Question = { ...BATCH, items: [{ id: "extras", prompt: "Extras?", options: ["GPS", "camera"], multi: true, rank: false, allow_text: true }] };
    mount(q);
    fireEvent.click(screen.getByRole("button", { name: "GPS" }));
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.placeholder).toBe("and/or add your own");
    fireEvent.input(box, { target: { value: "lidar" } });
    expect(screen.getByRole("button", { name: "GPS" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(JSON.parse((answerCalls()[0].body as { text: string }).text)).toEqual({ extras: ["GPS", "lidar"] });
  });

  it("single choice: the last of chip or text wins visibly and is what is sent", async () => {
    const q: Question = { ...BATCH, items: [{ id: "use", prompt: "Use?", options: ["racing", "mapping"], multi: false, rank: false, allow_text: true }] };
    mount(q);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.input(box, { target: { value: "survey" } });
    fireEvent.click(screen.getByRole("button", { name: "racing" }));
    expect(box.value).toBe("");                                       // the chip cleared the text
    expect(screen.getByRole("button", { name: "racing" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.input(box, { target: { value: "mapping drone" } });
    expect(screen.getByRole("button", { name: "racing" }).getAttribute("aria-pressed")).toBe("false"); // and back
    fireEvent.click(screen.getByRole("button", { name: "racing" }));
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(JSON.parse((answerCalls()[0].body as { text: string }).text)).toEqual({ use: "racing" });
  });

  it("shows Sending… while the answer is posting, through the send seam", async () => {
    const q: Question = { ...BATCH, items: [BATCH.items![0]] };
    let release!: () => void;
    const send = vi.fn(() => new Promise<void>((r) => { release = r; }));
    core.state.questions.set(q.question_id, q);
    questions.value = new Map([[q.question_id, q]]);
    render(<InterviewCard question={q} runLabel="engineer" send={send} />);
    clock += 500;
    fireEvent.click(screen.getByRole("button", { name: "3 in" }));
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    const busy = await screen.findByRole("button", { name: "Sending…" }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    expect(send).toHaveBeenCalledWith(QID, JSON.stringify({ size: "3 in" }));
    release();
    await waitFor(() => expect(questions.value.has(QID)).toBe(false));
    expect(answerCalls()).toHaveLength(0);                             // the seam, not the client
  });

  it("free text satisfies a question with options", async () => {
    const q: Question = { ...BATCH, items: [BATCH.items![0]] };
    mount(q);
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "6 in custom" } });
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(JSON.parse((answerCalls()[0].body as { text: string }).text)).toEqual({ size: "6 in custom" });
  });
});
