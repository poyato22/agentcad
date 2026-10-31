// Kinematics port vs the real core (fixtures dumped by core/scripts/dump_ui_fixtures.py --only
// assembly).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { assemblySchema, driveResultSchema, type Assembly } from "../../api/assembly";
import { jointMotion, mobility, rotAxis, solve, frameMatrix, inverseRigid, matmul, samePose } from "./kinematics";

const FX = resolve(import.meta.dirname, "../../../test/fixtures/core-ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));

const asm = assemblySchema.parse(read("assembly.json"));
const driven = driveResultSchema.parse(read("assembly_drive.json"));
const clamped = driveResultSchema.parse(read("assembly_drive_clamped.json"));

function close(a: number[][], b: number[][], tol = 1e-9): void {
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) expect(Math.abs(a[i][j] - b[i][j])).toBeLessThan(tol);
}

function withValue(a: Assembly, value: number): Assembly {
  return { ...a, joints: a.joints.map((j) => ({ ...j, value })) };
}

describe("solve (port of core assembly/solve.py)", () => {
  it("hinge_at_90_matches_the_real_core_drive", () => {
    const r = solve(withValue(asm, 90));
    for (const [id, m] of Object.entries(driven.transforms)) close(r.transforms[id], m);
    expect(r.applied[asm.joints[0].id]).toBe(90);
  });

  it("limits_clamp_like_the_core (270 → 180)", () => {
    const r = solve(withValue(asm, 270));
    expect(r.applied[asm.joints[0].id]).toBe(clamped.applied);
    for (const [id, m] of Object.entries(clamped.transforms)) close(r.transforms[id], m);
  });

  it("knows_which_joint_places_each_instance", () => {
    const r = solve(asm);
    const [base, lid] = asm.instances;
    expect(mobility(base, r.placedBy)).toEqual({ kind: "grounded" });
    expect(mobility(lid, r.placedBy)).toEqual({ kind: "joint", joint: asm.joints[0].id, from: "a" });
    expect(mobility({ ...base, fixed: false }, {})).toEqual({ kind: "free" });
  });

  it("a_loop_or_an_unknown_face_is_skipped_not_thrown", () => {
    const loop = { ...asm.joints[0], id: "loop" };
    const r = solve({ ...asm, joints: [...asm.joints, loop] });
    expect(r.unresolved).toEqual(["loop"]);
    const faceOnly = { ...asm.joints[0], a: { ...asm.joints[0].a, frame: { face_id: "f9" } } };
    expect(solve({ ...asm, joints: [faceOnly] }).unresolved).toEqual([faceOnly.id]);
    const resolved = solve({ ...asm, joints: [faceOnly] }, () => [[20, 0, 0], [0, 1, 0]]);
    expect(resolved.unresolved).toEqual([]);
    // the part that joint should place is locked (never offered a free gizmo the core would undo)
    const lost = solve({ ...asm, joints: [faceOnly] });
    expect(mobility(asm.instances[1], lost.placedBy, lost.blocked)).toEqual({ kind: "locked", joint: faceOnly.id });
  });

  it("frames_have_local_Z_on_the_axis_and_invert", () => {
    const f = frameMatrix([1, 2, 3], [0, 1, 0]);
    expect([f[0][2], f[1][2], f[2][2]]).toEqual([0, 1, 0]);
    close(matmul(f, inverseRigid(f)), [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]);
  });

  it("joint_motion_by_kind", () => {
    close(jointMotion("revolute", 90), rotAxis([0, 0, 1], 90));
    expect(jointMotion("slider", 5)[2][3]).toBe(5);
    const cyl = jointMotion("cylindrical", [90, 4]);
    expect(cyl[2][3]).toBe(4);
    expect(cyl[1][0]).toBeCloseTo(1);
    expect(samePose(jointMotion("fixed", 12), jointMotion("ball", [0, 0, 0]))).toBe(true);
  });
});
