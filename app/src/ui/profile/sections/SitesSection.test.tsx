// Browsing section (R3) on the real core's default sites: list them (datasheet hosts with no
// search URL included), add one (the core's search-URL rules, checked before the round trip),
// remove one, restore the defaults, and edit "browser fetches per run". Saving never drops an
// entry the core sent.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SuggestedSite } from "../../../api/browser";
import type { CoreClient } from "../../../api/client";
import type { Profile } from "../../../api/types";
import { bindClient } from "../../../state/store";
import type { ProfileData } from "../common";
import { searchUrlProblem, SitesSection } from "./SitesSection";

const FX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/browser");
const DEFAULTS = JSON.parse(readFileSync(resolve(FX, "sites_defaults.json"), "utf8")) as SuggestedSite[];
const PROFILE = JSON.parse(readFileSync(resolve(FX, "profile_browser.json"), "utf8")) as Profile;

function renderSection(profile: Profile = PROFILE) {
  const update = vi.fn();
  const data = { profile, keys: [], claude: { installed: true, version: null, signed_in: true, auth_method: null }, status: null, models: null, agents: [] } as ProfileData;
  render(<SitesSection data={data} update={update} />);
  return { update };
}

describe("searchUrlProblem", () => {
  it("mirrors the core: https, one {q}, on the site's domain; empty is fine", () => {
    expect(searchUrlProblem("", "pololu.com")).toBeNull();
    expect(searchUrlProblem("https://www.pololu.com/search?query={q}", "pololu.com")).toBeNull();
    expect(searchUrlProblem("https://www.pololu.com/search", "pololu.com")).toMatch(/\{q\}/);
    expect(searchUrlProblem("http://www.pololu.com/?q={q}", "pololu.com")).toMatch(/https/);
    expect(searchUrlProblem("https://evil.example/?q={q}", "pololu.com")).toMatch(/on pololu\.com/);
  });
});

describe("SitesSection", () => {
  let client: { updateProfile: ReturnType<typeof vi.fn>; restoreDefaultSites: ReturnType<typeof vi.fn> };
  beforeEach(() => {
    client = {
      updateProfile: vi.fn(async (patch: Partial<Profile>) => ({ ...PROFILE, ...patch })),
      restoreDefaultSites: vi.fn(async () => DEFAULTS),
    };
    bindClient(client as unknown as CoreClient);
  });
  afterEach(() => { cleanup(); bindClient(null); });

  it("lists every default, including the no-search datasheet hosts", () => {
    renderSection();
    expect(document.querySelectorAll("[data-site]").length).toBe(DEFAULTS.length);
    expect(screen.getByText("Texas Instruments")).toBeTruthy();
    expect(screen.getAllByText("no search").length).toBe(DEFAULTS.filter((s) => s.search_url === null).length);
  });

  it("adding a site keeps every existing entry (null search_url ones too)", async () => {
    renderSection();
    fireEvent.input(screen.getByLabelText("Site name"), { target: { value: "Bot Shop" } });
    fireEvent.input(screen.getByLabelText("Site domain"), { target: { value: "https://BotShop.io/" } });
    fireEvent.input(screen.getByLabelText("Search URL"), { target: { value: "https://www.botshop.io/search?query={q}" } });
    fireEvent.input(screen.getByLabelText("Categories"), { target: { value: "motors, electronics" } });
    fireEvent.click(screen.getByRole("button", { name: "Add site" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledTimes(1));
    const sent = client.updateProfile.mock.calls[0][0].suggested_sites as SuggestedSite[];
    expect(sent).toHaveLength(DEFAULTS.length + 1);
    expect(sent.filter((s) => s.search_url === null)).toHaveLength(DEFAULTS.filter((s) => s.search_url === null).length);
    expect(sent.at(-1)).toEqual({ name: "Bot Shop", domain: "botshop.io", search_url: "https://www.botshop.io/search?query={q}", categories: ["motors", "electronics"], suggested: false });
  });

  it("a site with no search URL is allowed; a search URL off the domain is refused before saving", async () => {
    renderSection();
    fireEvent.input(screen.getByLabelText("Site domain"), { target: { value: "botshop.io" } });
    fireEvent.input(screen.getByLabelText("Search URL"), { target: { value: "https://evil.example/?q={q}" } });
    fireEvent.click(screen.getByRole("button", { name: "Add site" }));
    await screen.findByText("The search URL must be on botshop.io.");
    expect(client.updateProfile).not.toHaveBeenCalled();
    fireEvent.input(screen.getByLabelText("Search URL"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Add site" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalled());
    expect((client.updateProfile.mock.calls[0][0].suggested_sites as SuggestedSite[]).at(-1)?.search_url).toBeNull();
  });

  it("removes a site after confirming", async () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Remove McMaster-Carr" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalled());
    const sent = client.updateProfile.mock.calls[0][0].suggested_sites as SuggestedSite[];
    expect(sent).toHaveLength(DEFAULTS.length - 1);
    expect(sent.some((s) => s.domain === "mcmaster.com")).toBe(false);
  });

  it("Restore defaults asks first, then uses the core's restore route", async () => {
    const { update } = renderSection({ ...PROFILE, suggested_sites: [] });
    expect(screen.getByText(/No suggested sites/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore defaults" }));
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(client.restoreDefaultSites).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(update).toHaveBeenCalled());
  });

  it("edits browser fetches per run within 1-1000", async () => {
    renderSection();
    const input = screen.getByRole("textbox", { name: "Browser fetches per run" }) as HTMLInputElement;
    expect(input.value).toBe("60");
    fireEvent.input(input, { target: { value: "5000" } });
    expect(screen.getByText("A whole number from 1 to 1000.")).toBeTruthy();
    fireEvent.input(input, { target: { value: "120" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" }).at(-1) as HTMLElement);
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ browser_fetches_per_run: 120 }));
  });
});
