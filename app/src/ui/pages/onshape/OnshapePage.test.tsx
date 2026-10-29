import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { OnshapeDoc } from "../../../api/directory";
import type { CoreStatusInfo } from "../../../api/types";
import { bindClient, conn, coreStatus, resetStore } from "../../../state/store";
import { profileOpen, profileSection } from "../../profile/open";
import { chosenBuild } from "../directory/BuildPicker";
import { OnshapePage } from "./index";

const FX = resolve(import.meta.dirname, "../../../../test/fixtures/core-ui");
const read = (n: string) => JSON.parse(readFileSync(resolve(FX, n), "utf8")) as unknown;
const DOCS = read("onshape_documents.json") as OnshapeDoc[];
const ELEMENTS = read("onshape_elements.json");
const IMPORTED = read("onshape_import.json");

/** 23 documents, like the core test's paging case: a full page of 20, then 3. */
const many: OnshapeDoc[] = Array.from({ length: 23 }, (_, i) => ({
  ...DOCS[i % DOCS.length], did: (i + 1).toString(16).padStart(24, "0"), name: `Doc ${i}`,
}));
const status = (onshape: CoreStatusInfo["onshape"]): CoreStatusInfo => ({
  core_version: "0", onshape, onshape_calls_year: 5, onshape_cap_year: 2500, runs_running: 0, approvals_pending: 0,
});

const client = { onshapeDocuments: vi.fn(), onshapeElements: vi.fn(), onshapeThumbnail: vi.fn(), onshapeImport: vi.fn(), buildChoices: vi.fn() };

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:http://localhost/thumb");
  client.onshapeDocuments.mockReset().mockImplementation(async ({ offset = 0, limit = 20, q = "" }: { offset?: number; limit?: number; q?: string }) =>
    many.filter((d) => !q || d.name.toLowerCase().includes(q.toLowerCase())).slice(offset, offset + limit));
  client.onshapeElements.mockReset().mockResolvedValue(ELEMENTS);
  client.onshapeThumbnail.mockReset().mockResolvedValue(new Blob(["png"], { type: "image/png" }));
  client.onshapeImport.mockReset().mockResolvedValue(IMPORTED);
  client.buildChoices.mockReset().mockResolvedValue({ builds: [{ id: "b_quad", name: "Quad", trashed_at: null }], active: null });
  bindClient(client as unknown as CoreClient);
  conn.value = "open";
});
afterEach(() => { cleanup(); resetStore(); chosenBuild.value = null; profileOpen.value = false; });

describe("OnshapePage", () => {
  it("not connected: a connect card that opens Profile → Onshape, and no Onshape call", async () => {
    coreStatus.value = status("not_configured");
    render(<OnshapePage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Onshape keys" }));
    expect(profileOpen.value).toBe(true);
    expect(profileSection.value).toBe("onshape");
    expect(client.onshapeDocuments).not.toHaveBeenCalled();
  });

  it("the core saying 'not connected' also shows the card", async () => {
    coreStatus.value = status("connected");
    client.onshapeDocuments.mockRejectedValue(new CoreError("denied", "Onshape is not connected", { status: 403 }));
    render(<OnshapePage />);
    expect(await screen.findByRole("button", { name: "Add Onshape keys" })).toBeTruthy();
  });

  it("grid of documents with thumbnails; Load more pages on by offset", async () => {
    coreStatus.value = status("connected");
    render(<OnshapePage />);
    const grid = await screen.findByRole("list", { name: "20 documents" });
    expect(client.onshapeDocuments).toHaveBeenCalledWith({ q: "", offset: 0, limit: 20 });
    await waitFor(() => expect(grid.querySelectorAll('[data-thumb="loaded"]').length).toBe(20));
    expect(client.onshapeThumbnail).toHaveBeenCalledWith(DOCS[0].thumbnail_url);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByRole("list", { name: "23 documents" });
    expect(client.onshapeDocuments).toHaveBeenLastCalledWith({ q: "", offset: 20, limit: 20 });
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();   // a short page is the last
  });

  it("search runs on submit (each search is an Onshape call), not per keystroke", async () => {
    coreStatus.value = status("connected");
    render(<OnshapePage />);
    await screen.findByRole("list", { name: "20 documents" });
    const box = screen.getByRole("searchbox", { name: "Search your Onshape documents" });
    fireEvent.input(box, { target: { value: "Doc 2" } });
    expect(client.onshapeDocuments).toHaveBeenCalledTimes(1);
    fireEvent.submit(box.closest("form") as HTMLFormElement);
    await waitFor(() => expect(client.onshapeDocuments).toHaveBeenLastCalledWith({ q: "Doc 2", offset: 0, limit: 20 }));
  });

  it("Open in Onshape links to cad.onshape.com in the system browser", async () => {
    coreStatus.value = status("connected");
    render(<OnshapePage />);
    await screen.findByRole("list", { name: "20 documents" });
    const link = screen.getAllByRole("link", { name: /Open in Onshape \(cad\.onshape\.com\)/ })[0];
    expect(link.getAttribute("href")).toBe(`https://cad.onshape.com/documents/${many[0].did}/w/${many[0].default_wid}`);
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("opening a document lists its elements; Import to build mirrors one into the build", async () => {
    coreStatus.value = status("connected");
    render(<OnshapePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Open Doc 0" }));
    const list = await screen.findByRole("list", { name: "Part Studios and Assemblies" });
    expect(client.onshapeElements).toHaveBeenCalledWith(many[0].did, many[0].default_wid);
    expect(list.textContent).toContain("Part Studio 1");
    expect(list.textContent).toContain("Assembly 1");
    const row = list.querySelector('[data-element="000000000000000000000003"]') as HTMLElement;
    await waitFor(() => expect((within(row).getByRole("button", { name: "Import to build" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(row).getByRole("button", { name: "Import to build" }));
    await waitFor(() => expect(client.onshapeImport).toHaveBeenCalledWith({
      did: many[0].did, wid: many[0].default_wid, eid: "000000000000000000000003", project_id: "b_quad" }));
    const done = await within(row).findByRole("status");
    expect(done.textContent).toContain("Imported into Quad as part_studio");
    expect(done.textContent).toContain("estimate");
    fireEvent.click(screen.getByRole("button", { name: "Back to documents" }));
    expect(await screen.findByRole("list", { name: "20 documents" })).toBeTruthy();
  });
});
