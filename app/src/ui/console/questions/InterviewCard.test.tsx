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
  it("renders every sub-question as one card with an intro", () => {
    const { container } = mount();
    const card = container.querySelector("[data-interview]") as HTMLElement;
    expect(card.getAttribute("role")).toBe("group");
    expect(card.textContent).toContain("A few questions about your drone:");
    expect(container.querySelectorAll("[data-q]")).toHaveLength(3);
    expect(screen.getByText("Prop size?")).toBeTruthy();
    // multi / rank hints are shown
    expect(container.querySelector('[data-q="mats"]')?.textContent).toContain("pick any");
    expect(container.querySelector('[data-q="prio"]')?.textContent).toContain("order of importance");
  });

  it("requires every question answered before Send is enabled", () => {
    mount();
    const send = screen.getByRole("button", { name: "Send answers" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "5 in" }));
    fireEvent.click(screen.getByRole("button", { name: "PETG" }));
    expect(send.disabled).toBe(true);               // prio still unanswered
    fireEvent.click(screen.getByRole("button", { name: "impact" }));
    expect(send.disabled).toBe(false);
  });

  it("posts the whole batch as a JSON {id: value} object", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "5 in" }));
    fireEvent.click(screen.getByRole("button", { name: "PLA" }));
    fireEvent.click(screen.getByRole("button", { name: "PETG" }));
    fireEvent.click(screen.getByRole("button", { name: "impact" }));
    fireEvent.click(screen.getByRole("button", { name: "weight" }));
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(answerCalls()[0].path).toBe(`/questions/${QID}/answer`);
    const body = answerCalls()[0].body as { text: string };
    expect(JSON.parse(body.text)).toEqual({ size: "5 in", mats: ["PLA", "PETG"], prio: ["impact", "weight"] });
    await waitFor(() => expect(questions.value.has(QID)).toBe(false));
  });

  it("single-select replaces the earlier pick", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "3 in" }));
    fireEvent.click(screen.getByRole("button", { name: "5 in" }));
    expect((screen.getByRole("button", { name: "3 in" })).getAttribute("aria-pressed")).toBe("false");
    expect((screen.getByRole("button", { name: "5 in" })).getAttribute("aria-pressed")).toBe("true");
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
