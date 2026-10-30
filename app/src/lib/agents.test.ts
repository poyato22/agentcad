import { describe, expect, it } from "vitest";
import { startableAgents } from "./agents";

const list = [{ name: "designer", hidden: true }, { name: "engineer", hidden: false }, { name: "sim" }];

describe("startableAgents (C-4)", () => {
  it("drops hidden agents", () => {
    expect(startableAgents(list).map((a) => a.name)).toEqual(["engineer", "sim"]);
  });
  it("keeps the one a form already holds", () => {
    expect(startableAgents(list, "designer").map((a) => a.name)).toEqual(["designer", "engineer", "sim"]);
  });
});
