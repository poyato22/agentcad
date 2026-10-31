import { describe, expect, it } from "vitest";
import type { Build } from "../../../api/builds";
import { buildStatus, DEFAULT_VIEW, fmtLastEdited, fmtMass, partsLabel, viewBuilds } from "./model";

function build(p: Partial<Build>): Build {
  return {
    id: "b", name: "Build", created_at: 0, updated_at: 0, trashed_at: null,
    part_count: 0, run_count: 0, onshape: null, ...p,
  };
}

const B = [
  build({ id: "arm", name: "Robot arm", created_at: 10, updated_at: 300 }),
  build({ id: "gear", name: "Gearbox", created_at: 50, updated_at: 100 }),
  build({ id: "old", name: "Old mount", created_at: 5, updated_at: 5, trashed_at: 999 }),
];

describe("viewBuilds", () => {
  it("shows only live builds by default", () => {
    const out = viewBuilds(B, DEFAULT_VIEW);
    expect(out.map((b) => b.id)).toEqual(["arm", "gear"]);     // edited desc
  });

  it("shows only trashed builds behind the Trash filter", () => {
    const out = viewBuilds(B, { ...DEFAULT_VIEW, trash: true });
    expect(out.map((b) => b.id)).toEqual(["old"]);
  });

  it("filters by name, case-insensitively", () => {
    expect(viewBuilds(B, { ...DEFAULT_VIEW, query: "GEAR" }).map((b) => b.id)).toEqual(["gear"]);
    expect(viewBuilds(B, { ...DEFAULT_VIEW, query: "zzz" })).toEqual([]);
  });

  it("sorts 'last edited' by real activity (last_edited_at), not the row's updated_at", () => {
    const bs = [
      build({ id: "renamed", name: "Renamed", updated_at: 900 }),                         // only renamed
      build({ id: "busy", name: "Busy", updated_at: 100, last_edited_at: 5000 }),         // rebuilt today
    ];
    expect(viewBuilds(bs, DEFAULT_VIEW).map((b) => b.id)).toEqual(["busy", "renamed"]);
  });

  it("sorts by name and by creation date", () => {
    expect(viewBuilds(B, { ...DEFAULT_VIEW, sort: "name" }).map((b) => b.id)).toEqual(["gear", "arm"]);
    expect(viewBuilds(B, { ...DEFAULT_VIEW, sort: "created" }).map((b) => b.id)).toEqual(["gear", "arm"]);
  });
});

describe("buildStatus", () => {
  it("the build's lead agent (U3c) speaks for it first", () => {
    const runs = [{ project_id: "arm", status: "running", role: "sub" }, { project_id: "arm", status: "queued", role: "lead" }];
    expect(buildStatus("arm", runs)).toEqual({ tone: "amber", label: "The lead agent is queued" });
    expect(buildStatus("arm", [{ project_id: "arm", status: "running", role: "lead" }]).label).toBe("The lead agent is working");
    expect(buildStatus("arm", [{ project_id: "arm", status: "done", role: "lead" }, { project_id: "arm", status: "running", role: "sub" }]).label).toBe("An agent is working");
  });

  const runs = [
    { project_id: "arm", status: "running" },
    { project_id: "gear", status: "queued" },
    { project_id: "gear", status: "done" },
    { project_id: "idle", status: "done" },
  ];
  it("is green while an agent runs, amber while queued, faint when idle", () => {
    expect(buildStatus("arm", runs).tone).toBe("green");
    expect(buildStatus("gear", runs).tone).toBe("amber");
    expect(buildStatus("idle", runs).tone).toBe("faint");
    expect(buildStatus("none", runs).tone).toBe("faint");
  });
});

describe("fmtLastEdited", () => {
  const now = 1_000_000;
  it("reads as a short relative time", () => {
    expect(fmtLastEdited(now, now)).toBe("just now");
    expect(fmtLastEdited(now - 120, now)).toBe("2 min ago");
    expect(fmtLastEdited(now - 3 * 3600, now)).toBe("3 h ago");
    expect(fmtLastEdited(now - 2 * 86400, now)).toBe("2 days ago");
    expect(fmtLastEdited(now - 1 * 86400, now)).toBe("1 day ago");
    expect(fmtLastEdited(now - 40 * 86400, now)).toMatch(/\d{4}/);   // falls back to a date
  });
});

describe("fmtMass (D24 tile)", () => {
  it("grams under a kilo, kilos above; null when unknown", () => {
    expect(fmtMass(180)).toBe("180 g");
    expect(fmtMass(4.25)).toBe("4.3 g");
    expect(fmtMass(1240)).toBe("1.24 kg");
    expect(fmtMass(31000)).toBe("31.0 kg");
    expect(fmtMass(null)).toBeNull();
    expect(fmtMass(undefined)).toBeNull();
    expect(fmtMass(Number.NaN)).toBeNull();
  });
});

describe("partsLabel", () => {
  it("pluralizes", () => {
    expect(partsLabel(1)).toBe("1 part");
    expect(partsLabel(0)).toBe("0 parts");
    expect(partsLabel(3)).toBe("3 parts");
  });
});
