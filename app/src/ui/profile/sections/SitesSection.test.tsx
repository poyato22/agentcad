// Browsing section (R3): list the suggested sites, add one (validating the search URL template),
// and remove one. Saving writes the whole list back with PUT /profile.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import type { Profile } from "../../../api/types";
import { bindClient } from "../../../state/store";
import type { ProfileData } from "../common";
import { SitesSection } from "./SitesSection";

const profile = {
  display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: "auto", local_endpoints: {},
  theme: "system", setup_completed_at: 1,
  suggested_sites: [{ name: "McMaster-Carr", domain: "mcmaster.com", search_url: "https://www.mcmaster.com/{q}", categories: ["fasteners"], suggested: true }],
} as Profile;

function renderSection() {
  const update = vi.fn();
  const data = { profile, keys: [], claude: { installed: true, version: null, signed_in: true, auth_method: null }, status: null, models: null, agents: [] } as ProfileData;
  render(<SitesSection data={data} update={update} />);
  return { update };
}

describe("SitesSection", () => {
  let client: { updateProfile: ReturnType<typeof vi.fn> };
  beforeEach(() => {
    client = { updateProfile: vi.fn(async (patch: Partial<Profile>) => ({ ...profile, ...patch })) };
    bindClient(client as unknown as CoreClient);
  });
  afterEach(() => { cleanup(); bindClient(null); });

  it("lists sites and tags the suggested defaults", () => {
    renderSection();
    expect(screen.getByText("McMaster-Carr")).toBeTruthy();
    expect(screen.getByText("mcmaster.com")).toBeTruthy();
    expect(screen.getByText("suggested")).toBeTruthy();
  });

  it("validates the search URL template before saving", async () => {
    renderSection();
    fireEvent.input(screen.getByLabelText("Site domain"), { target: { value: "pololu.com" } });
    fireEvent.input(screen.getByLabelText("Search URL"), { target: { value: "https://www.pololu.com/search" } });
    fireEvent.click(screen.getByRole("button", { name: "Add site" }));
    await screen.findByText(/Put \{q\} in the search URL/);
    expect(client.updateProfile).not.toHaveBeenCalled();
  });

  it("adds a valid site via PUT /profile", async () => {
    const { update } = renderSection();
    fireEvent.input(screen.getByLabelText("Site name"), { target: { value: "Pololu" } });
    fireEvent.input(screen.getByLabelText("Site domain"), { target: { value: "https://Pololu.com/" } });
    fireEvent.input(screen.getByLabelText("Search URL"), { target: { value: "https://www.pololu.com/search?query={q}" } });
    fireEvent.input(screen.getByLabelText("Categories"), { target: { value: "motors, electronics" } });
    fireEvent.click(screen.getByRole("button", { name: "Add site" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledTimes(1));
    const sent = client.updateProfile.mock.calls[0][0].suggested_sites;
    expect(sent).toHaveLength(2);
    expect(sent[1]).toMatchObject({ name: "Pololu", domain: "pololu.com", categories: ["motors", "electronics"], suggested: false });
    expect(update).toHaveBeenCalled();
  });

  it("removes a site after confirming", async () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Remove McMaster-Carr" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ suggested_sites: [] }));
  });
});
