import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakeCore, type FakeCore } from "../../../../test/fake-core/server";
import { CoreClient } from "../../../api/client";
import type { Question } from "../../../api/types";
import { bindClient, questions, resetStore } from "../../../state/store";
import { QuestionCard } from "./QuestionCard";

// jsdom can't make trusted clicks: the trust check follows `trusted` (real-input path: e2e).
let trusted = true;
vi.mock("./approvalKeys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./approvalKeys")>()),
  isTrustedActivation: () => trusted,
}));

const QID = "q_thick_01";
let core: FakeCore;
let clock = 0;

const answerCalls = () => core.state.calls.filter((c) => c.path.endsWith("/answer"));

/** Renders the card, then moves the clock past the arm delay. */
function mount(q: Question = core.state.questions.get(QID) as Question, arm = true) {
  questions.value = new Map([[q.question_id, q]]);
  const r = render(<QuestionCard question={q} runLabel="designer · stiffen upright bracket" />);
  if (arm) clock += 500;
  return r;
}

beforeEach(async () => {
  trusted = true;
  clock = 100_000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  core = await startFakeCore({ port: 0, token: "t", scenario: "approvals" });
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
});

afterEach(async () => {
  cleanup();
  resetStore();
  await core.stop();
});

describe("QuestionCard", () => {
  it("renders_question_options_and_input", () => {
    const { container } = mount();
    const card = container.querySelector("[data-card]") as HTMLElement;
    expect(card.getAttribute("role")).toBe("group");
    expect(card.getAttribute("aria-label")).toBe("Question from designer · stiffen upright bracket");
    expect(card.textContent).toContain("Which rib thickness?");
    expect(screen.getByRole("button", { name: "2 mm" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "3 mm" })).toBeTruthy();
    expect(screen.getByRole("textbox")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("question_option_click_answers", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "3 mm" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(answerCalls()[0].path).toBe(`/questions/${QID}/answer`);
    expect(answerCalls()[0].body).toEqual({ text: "3 mm" });
    await waitFor(() => expect(questions.value.has(QID)).toBe(false));
  });

  it("question_free_text_requires_send_click", async () => {
    mount();
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "2.5 mm please" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await new Promise((r) => setTimeout(r, 30));
    expect(answerCalls()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    expect(answerCalls()[0].body).toEqual({ text: "2.5 mm please" });
  });

  it("one_answer_in_flight", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "2 mm" }));
    fireEvent.click(screen.getByRole("button", { name: "3 mm" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 30));
    expect(answerCalls()).toHaveLength(1);
  });

  it("shows_error_when_the_question_is_gone", async () => {
    core.state.questions.delete(QID);
    mount({ question_id: QID, run_id: "5e1f0c2a9d3b", question: "Which rib thickness?", options: ["2 mm"] });
    fireEvent.click(screen.getByRole("button", { name: "2 mm" }));
    await waitFor(() => expect(document.querySelector("[data-error]")?.textContent).toContain("no open question"));
    expect((screen.getByRole("button", { name: "2 mm" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("option_click_within_400ms_is_ignored", async () => {
    mount(undefined, false);
    clock += 399;
    fireEvent.click(screen.getByRole("button", { name: "2 mm" }));
    await new Promise((r) => setTimeout(r, 30));
    expect(answerCalls()).toEqual([]);
    clock += 1;
    fireEvent.click(screen.getByRole("button", { name: "2 mm" }));
    await waitFor(() => expect(answerCalls()).toHaveLength(1));
  });

  it("untrusted_or_double_clicks_never_answer", async () => {
    mount();
    trusted = false;
    fireEvent.click(screen.getByRole("button", { name: "2 mm" }));
    fireEvent.input(screen.getByRole("textbox"), { target: { value: "yes merge" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    trusted = true;
    fireEvent.click(screen.getByRole("button", { name: "3 mm" }), { detail: 2 });
    await new Promise((r) => setTimeout(r, 30));
    expect(answerCalls()).toEqual([]);
  });

  it("duplicate_options_render_with_distinct_keys", () => {
    mount({ question_id: "q3", run_id: "r", question: "again?", options: ["yes", "yes"] });
    expect(screen.getAllByRole("button", { name: "yes" })).toHaveLength(2);
  });

  it("untrusted_text_renders_as_text", () => {
    mount({ question_id: "q2", run_id: "r", question: "<b>bold?</b>", options: ["<i>x</i>"] });
    const card = document.querySelector("[data-card]") as HTMLElement;
    expect(card.querySelector("b, i")).toBeNull();
    expect(card.textContent).toContain("<b>bold?</b>");
  });
});
