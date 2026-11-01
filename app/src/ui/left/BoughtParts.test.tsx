// Plan 10 E14 on the real core's BOM (core/tests/fixtures/ui/browser/bom.json).
import { fireEvent, render } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { bomSchema, type Bom } from "../../api/browser";
import { openPartId } from "../assembly/focus";
import { buildBom } from "./bomData";
import { BoughtParts } from "./BoughtParts";

const REAL = bomSchema.parse(JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/browser/bom.json"), "utf8"))) as Bom;

afterEach(() => { buildBom.value = null; openPartId.value = null; });

describe("Bought parts (E14)", () => {
  it("lists the bought rows with quantity and price; a click opens specs, price and the source", () => {
    buildBom.value = REAL;
    const { getAllByTestId, getByTestId, queryByTestId } = render(<BoughtParts />);
    const bought = REAL.items.filter((r) => r.kind === "bought");
    expect(getAllByTestId("bought-row")).toHaveLength(bought.length);
    const servo = getAllByTestId("bought-row").find((b) => b.textContent?.includes("Micro servo SG92R"))!;
    expect(servo.textContent).toContain("×6");
    fireEvent.click(servo);
    const info = getByTestId("bought-info");
    expect(info.textContent).toContain("$5.95 each");
    expect(info.textContent).toContain("Adafruit");
    expect(info.textContent).toContain("mass");
    expect(info.textContent).toContain("No 3D model in this build");
    expect(info.querySelector("a")?.textContent).toBe("adafruit.com");
    // the same row closes it again
    fireEvent.click(servo);
    expect(queryByTestId("bought-info")).toBeNull();
  });

  it("a row a build part draws opens that part's 3D model", () => {
    buildBom.value = { ...REAL, items: REAL.items.map((r) => (r.name === "Micro servo SG92R" ? { ...r, geometry_part_id: "servo_1", geometry: "builtin" } : r)) };
    const { getAllByTestId, getByTestId } = render(<BoughtParts />);
    fireEvent.click(getAllByTestId("bought-row").find((b) => b.textContent?.includes("Micro servo"))!);
    fireEvent.click(getByTestId("bought-model"));
    expect(openPartId.value).toBe("servo_1");
  });

  it("shows nothing when the build buys nothing", () => {
    buildBom.value = { ...REAL, items: REAL.items.filter((r) => r.kind === "made") };
    const { queryByTestId } = render(<BoughtParts />);
    expect(queryByTestId("bought-parts")).toBeNull();
  });
});
