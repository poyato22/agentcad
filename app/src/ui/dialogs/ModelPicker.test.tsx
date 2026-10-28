import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { modelsInfoSchema } from "../../api/schemas";
import type { LocalStatus, MisesEvent, ModelsInfo } from "../../api/types";
import { bindClient, resetStore } from "../../state/store";
import { pullText, resetCatalog } from "./catalog";
import { ModelPicker } from "./ModelPicker";

const MODELS: ModelsInfo = modelsInfoSchema.parse({
  default: "anthropic/claude-sonnet-5-5",
  vendors: [
    { vendor: "anthropic", key: "none" },
    { vendor: "openrouter", key: "none", free: true, models: ["openrouter/qwen/qwen3-coder:free"] },
    { vendor: "ollama", key: "not_needed", models: [] },
    { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"], recommended: "best", note: "uses your Claude plan" },
  ],
});
const STOPPED: LocalStatus = {
  installed: true, running: false, version: "0.12.3", models: [{ name: "gpt-oss:20b", size_gb: 13.8 }],
  recommended: [{ name: "qwen3-coder:30b", size_gb: 19, fits: true, note: "best local coder" }],
};

// The core's events reach UI taps through EventStream; here a captured tap stands in for it.
const taps: ((e: MisesEvent) => void)[] = [];
vi.mock("../../api/events", () => ({ onCoreEvent: (cb: (e: MisesEvent) => void) => { taps.push(cb); return () => { taps.splice(taps.indexOf(cb), 1); }; } }));
const emit = (type: string, data: Record<string, unknown>) => act(() => { for (const t of [...taps]) t({ type, run_id: null, data, ts: 1 }); });

function client(local: LocalStatus | Error = STOPPED, over: { models?: ReturnType<typeof vi.fn> } = {}) {
  let st = local;
  const c = {
    models: vi.fn().mockResolvedValue(MODELS),
    localStatus: vi.fn(async () => { if (st instanceof Error) throw st; return st; }),
    getProfile: vi.fn().mockResolvedValue({ default_model: null }),
    localStart: vi.fn(async () => { if (!(st instanceof Error)) st = { ...st, running: true }; return { ok: true, running: true }; }),
    localPull: vi.fn().mockResolvedValue({ ok: true }),
    localPullCancel: vi.fn().mockResolvedValue(true),
    ...over,
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

afterEach(() => { cleanup(); resetStore(); resetCatalog(); taps.length = 0; });

async function openPicker(onChange = vi.fn(), value: string | null = null) {
  render(<ModelPicker value={value} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /^Model/ }));
  return { panel: await screen.findByRole("region", { name: "Choose a model" }), onChange };
}

describe("ModelPicker", () => {
  it("shows_the_groups_in_order_with_badges_and_notes", async () => {
    client();
    const { panel } = await openPicker();
    await waitFor(() => expect(within(panel).getByRole("group", { name: "Recommended" })).toBeTruthy());
    const names = within(panel).getAllByRole("group").map((g) => g.getAttribute("aria-label"));
    expect(names).toEqual(["Recommended", "Your Claude Code", "On this Mac (Ollama)", "Free (OpenRouter)", "API keys"]);
    const rec = within(panel).getByRole("group", { name: "Recommended" });
    expect(rec.textContent).toContain("Claude Code · opus");
    expect(rec.querySelector("[data-badge=best]")?.textContent).toBe("best");
    expect(rec.textContent).toContain("uses your Claude plan");
    const free = within(panel).getByRole("group", { name: "Free (OpenRouter)" });
    expect(free.querySelector("[data-badge=free]")?.textContent).toBe("free");
    expect(free.textContent).toContain("needs a free OpenRouter key — add it in Profile");
    expect(within(free).getByRole("button", { name: /qwen\/qwen3-coder:free/ }).hasAttribute("disabled")).toBe(true);
  });

  it("picking_a_usable_model_calls_onChange_and_closes", async () => {
    client();
    const { panel, onChange } = await openPicker();
    const claude = await within(panel).findByRole("group", { name: "Your Claude Code" });
    fireEvent.click(within(claude).getByRole("button", { name: /sonnet/ }));
    expect(onChange).toHaveBeenCalledWith("claude-code/sonnet");
    expect(screen.queryByRole("region", { name: "Choose a model" })).toBeNull();
  });

  it("ollama_not_running_offers_start_ollama_which_posts_local_start", async () => {
    const c = client();
    const { panel } = await openPicker();
    const local = await within(panel).findByRole("group", { name: "On this Mac (Ollama)" });
    expect(local.textContent).toContain("Ollama isn't running");
    fireEvent.click(within(local).getByRole("button", { name: "Start Ollama" }));
    await waitFor(() => expect(c.localStart).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(within(local).queryByRole("button", { name: "Start Ollama" })).toBeNull());
    expect(within(local).getByRole("button", { name: /gpt-oss:20b · 13.8 GB/ }).hasAttribute("disabled")).toBe(false);
  });

  it("recommended_local_model_not_downloaded_offers_download_with_progress", async () => {
    const c = client({ ...STOPPED, running: true });
    const { panel } = await openPicker();
    const local = await within(panel).findByRole("group", { name: "On this Mac (Ollama)" });
    const dl = within(local).getByRole("button", { name: "Download qwen3-coder:30b (19 GB)" });
    expect(dl.textContent).toBe("Download (19 GB)");
    expect(local.textContent).toContain("best local coder");
    fireEvent.click(dl);
    await waitFor(() => expect(c.localPull).toHaveBeenCalledWith("qwen3-coder:30b"));
    emit("local_pull", { model: "qwen3-coder:30b", completed: 7.2e9, total: 19e9, status: "pulling 2f3a" });
    const bar = local.querySelector("[data-pull='qwen3-coder:30b']") as HTMLElement;
    expect(bar.textContent).toContain("37% · 7.2 of 19.0 GB");
    expect(bar.querySelector("progress")?.getAttribute("value")).toBe("37");
    // done: the catalog reloads and the model is now downloaded (a choice, not a download)
    c.localStatus.mockResolvedValue({ ...STOPPED, running: true, models: [...STOPPED.models, { name: "qwen3-coder:30b", size_gb: 19 }] });
    emit("local_pull", { model: "qwen3-coder:30b", completed: 19e9, total: 19e9, status: "success" });
    await waitFor(() => expect(within(local).getByRole("button", { name: /qwen3-coder:30b · 19 GB/ })).toBeTruthy());
    expect(within(local).queryByRole("button", { name: /^Download/ })).toBeNull();
  });

  it("a_download_can_be_cancelled_and_errors_and_refusals_show_the_core's_reason", async () => {
    const c = client({ ...STOPPED, running: true });
    const { panel } = await openPicker();
    const local = await within(panel).findByRole("group", { name: "On this Mac (Ollama)" });
    fireEvent.click(within(local).getByRole("button", { name: "Download qwen3-coder:30b (19 GB)" }));
    emit("local_pull", { model: "qwen3-coder:30b", completed: 1e9, total: 19e9, status: "pulling 2f3a" });
    fireEvent.click(within(local).getByRole("button", { name: "Cancel download of qwen3-coder:30b" }));
    await waitFor(() => expect(c.localPullCancel).toHaveBeenCalledWith("qwen3-coder:30b"));
    // "cancelled" is terminal: the row goes back to its Download button
    emit("local_pull", { model: "qwen3-coder:30b", completed: 0, total: 0, status: "cancelled" });
    await waitFor(() => expect(local.querySelector("[data-pull='qwen3-coder:30b']")).toBeNull());
    within(local).getByRole("button", { name: "Download qwen3-coder:30b (19 GB)" });
    // a failed download: the core's `error`, not the status word
    fireEvent.click(within(local).getByRole("button", { name: "Download qwen3-coder:30b (19 GB)" }));
    emit("local_pull", { model: "qwen3-coder:30b", completed: 0, total: 0, status: "error", error: "Ollama refused the download (HTTP 500)" });
    await waitFor(() => expect(local.querySelector("[data-pull='qwen3-coder:30b']")?.textContent).toContain("download failed — Ollama refused the download (HTTP 500)"));
    // a refusal (200 {ok:false}) shows its reason
    c.localPull.mockResolvedValue({ ok: false, error: "too_many_pulls", hint: "at most 2 downloads at once" });
    fireEvent.click(within(local).getByRole("button", { name: "Download qwen3-coder:30b (19 GB)" }));
    await waitFor(() => expect(local.querySelector("[data-pull='qwen3-coder:30b']")?.textContent).toContain("too many downloads at once — at most 2 downloads at once"));
  });

  it("an_older_core_without_local_status_still_lists_models", async () => {
    client(new Error("404"));
    const { panel } = await openPicker();
    await within(panel).findByRole("group", { name: "Your Claude Code" });
    expect(within(panel).queryByRole("button", { name: "Start Ollama" })).toBeNull();
  });

  it("untrusted_model_names_render_as_text", async () => {
    const evil = { ...MODELS, vendors: [{ vendor: "claude-code", key: "signed_in" as const, models: ["<img src=x onerror=alert(1)>"] }] };
    client(STOPPED, { models: vi.fn().mockResolvedValue(evil) });
    const { panel } = await openPicker();
    await within(panel).findByRole("group", { name: "Your Claude Code" });
    expect(panel.querySelector("img")).toBeNull();
    expect(panel.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("a_typed_model_ref_is_accepted", async () => {
    client();
    const { panel, onChange } = await openPicker();
    await within(panel).findByRole("group", { name: "Your Claude Code" });
    fireEvent.input(within(panel).getByLabelText("Other model"), { target: { value: "gemini/gemini-3-pro" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Use" }));
    expect(onChange).toHaveBeenCalledWith("gemini/gemini-3-pro");
  });

  it("enter_in_other_model_never_submits_a_surrounding_form", async () => {
    client();
    const submit = vi.fn((e: Event) => e.preventDefault());
    const onChange = vi.fn();
    render(<form onSubmit={submit}><ModelPicker value={null} onChange={onChange} /></form>);
    fireEvent.click(screen.getByRole("button", { name: /^Model/ }));
    const panel = await screen.findByRole("region", { name: "Choose a model" });
    const other = within(panel).getByLabelText("Other model");
    fireEvent.input(other, { target: { value: "gemini" } });            // partial: not a ref yet
    const formKeys = vi.fn();
    panel.closest("form")?.addEventListener("keydown", formKeys);
    expect(fireEvent.keyDown(other, { key: "Enter" })).toBe(false);    // default (implicit submit) prevented
    expect(formKeys).not.toHaveBeenCalled();                            // never reaches the form
    expect(submit).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("chip_names_the_current_model_and_escape_closes", async () => {
    client();
    render(<ModelPicker value="claude-code/opus" onChange={vi.fn()} />);
    const chip = screen.getByRole("button", { name: /^Model/ });
    expect(chip.textContent).toContain("Claude Code · opus");
    fireEvent.click(chip);
    await screen.findByRole("region", { name: "Choose a model" });
    fireEvent.keyDown(screen.getByRole("region", { name: "Choose a model" }), { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Choose a model" })).toBeNull();
  });
});

describe("pullText", () => {
  it("formats_progress", () => {
    expect(pullText({ model: "m", completed: 0, total: 0, status: "pulling manifest", error: null })).toBe("pulling manifest");
    expect(pullText({ model: "m", completed: 5e9, total: 10e9, status: "x", error: null })).toBe("50% · 5.0 of 10.0 GB");
    expect(pullText({ model: "m", completed: 0, total: 0, status: "error", error: "disk full" })).toBe("download failed — disk full");
  });
});
