// @vitest-environment node
// The settings form's schema logic against the real core's /sims/types schemas.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { StudyTypeInfo } from "../../../api/studies";
import { defaultFor, deref, discriminated, getAt, labelFor, setAt, validate, withDefaults } from "./formSchema";
import { STARTERS, TYPE_ORDER } from "./model";

const TYPES = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/study_types.json"), "utf8")) as StudyTypeInfo[];
const schema = (id: string) => TYPES.find((t) => t.id === id)!.schema;

describe("schema form", () => {
  it.each(TYPE_ORDER)("the %s starter merged onto the schema defaults is valid", (t) => {
    const s = withDefaults(schema(t), STARTERS[t]);
    expect(validate(s, schema(t), schema(t))).toEqual([]);
  });

  it("defaults come from the schema (wind)", () => {
    const s = withDefaults(schema("wind"), {});
    expect(s).toMatchObject({ speed_m_s: 10, direction: "+x", refinement: 2, turbulence: "smagorinsky", yaw_deg: [] });
  });

  it("bad ranges are rejected at their field, like the core", () => {
    const s = withDefaults(schema("modal"), { ...STARTERS.modal, n_modes: 500 });
    expect(validate(s, schema("modal"), schema("modal"))).toEqual([{ path: "n_modes", message: "at most 30" }]);
    const w = withDefaults(schema("wind"), { speed_m_s: 0, refinement: 9, yaw_deg: [1, "x"] });
    const errs = validate(w, schema("wind"), schema("wind")).map((e) => e.path);
    expect(errs).toEqual(expect.arrayContaining(["speed_m_s", "refinement", "yaw_deg.1"]));
  });

  it("loads are a tagged union: the kind picks the fields", () => {
    const root = schema("static");
    const lc = deref({ $ref: "#/$defs/LoadCase" }, root);
    const d = discriminated(deref((lc.properties as Record<string, Record<string, unknown>>).loads.items as never, root), root);
    expect(d?.options.map((o) => o.value).sort()).toEqual(["bearing", "force", "gravity", "moment", "pressure", "remote_force"]);
    const bad = setAt(withDefaults(root, STARTERS.static), ["load_cases", 0, "loads", 0], { kind: "pressure", face: "top", mpa: 0.5 });
    expect(validate(bad, root, root)).toEqual([]);
    const missing = setAt(bad, ["load_cases", 0, "loads", 0], { kind: "warp", face: "top" });
    expect(validate(missing, root, root)[0]?.path).toBe("load_cases.0.loads.0.kind");
  });

  it("faces and directions: presets, face ids and vectors", () => {
    const root = schema("static");
    const base = withDefaults(root, STARTERS.static);
    expect(validate(setAt(base, ["load_cases", 0, "loads", 0, "face"], "f12"), root, root)).toEqual([]);
    expect(validate(setAt(base, ["load_cases", 0, "loads", 0, "face"], "side"), root, root)[0]?.path).toBe("load_cases.0.loads.0.face");
    expect(validate(setAt(base, ["load_cases", 0, "loads", 0, "direction"], [0, 0.5, -1]), root, root)).toEqual([]);
    expect(validate(setAt(base, ["load_cases", 0, "loads", 0, "direction"], "down"), root, root)[0]?.path).toBe("load_cases.0.loads.0.direction");
  });

  it("nullable fields stay null (auto) until set", () => {
    const root = schema("static");
    const n = deref({ $ref: "#/$defs/MeshSettings" }, root);
    expect(defaultFor(n, root)).toMatchObject({ size_mm: null, second_order: true });
    expect(validate(setAt(withDefaults(root, STARTERS.static), ["mesh", "size_mm"], -1), root, root)[0]).toEqual({ path: "mesh.size_mm", message: "more than 0" });
  });

  it("unknown settings are refused (the core forbids extra fields)", () => {
    const root = schema("drop");
    expect(validate({ ...withDefaults(root, STARTERS.drop), speed: 3 }, root, root)).toEqual([{ path: "speed", message: "not a setting of this study" }]);
  });

  it("setAt copies, getAt reads; labels drop unit suffixes", () => {
    const a = { x: [{ y: 1 }] };
    const b = setAt(a, ["x", 0, "y"], 2);
    expect(getAt(b, ["x", 0, "y"])).toBe(2);
    expect(a.x[0].y).toBe(1);
    expect(labelFor("speed_m_s", {})).toBe("Wind speed");
    expect(labelFor("drop_m", {})).toBe("Drop height");
    expect(labelFor("cells_across", {})).toBe("Cells across");
  });
});
