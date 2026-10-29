// Kinematics port vs the real core (fixtures dumped by core/scripts/dump_ui_fixtures.py --only
// assembly), gizmo snapping math, and face picking.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Quaternion, Vector3 } from "three";
import { describe, expect, it } from "vitest";
import { assemblySchema, driveResultSchema, type Assembly } from "../../api/assembly";
import { partMetricsSchema } from "../../api/schemas";
import { jointMotion, mobility, rotAxis, solve, frameMatrix, inverseRigid, matmul, samePose } from "./kinematics";
import { draftProblem, emptyDraft, faceOptions, jointBody, pickOption } from "./pick";
import { angleAboutZ, snapJointValue, snapMove, snapTo, snapTurn, turnBetween } from "./snap";

const FX = resolve(import.meta.dirname, "../../../test/fixtures/core-ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));

const asm = assemblySchema.parse(read("assembly.json"));
const driven = driveResultSchema.parse(read("assembly_drive.json"));
const clamped = driveResultSchema.parse(read("assembly_drive_clamped.json"));
const metrics = partMetricsSchema.parse((read("assembly_parts.json") as { metrics: unknown }[])[0].metrics);

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

describe("gizmo snapping", () => {
  it("snaps_moved_axes_to_whole_mm_and_leaves_the_others", () => {
    expect(snapMove([0.3, 2, 5], [7.62, 2, 5])).toEqual([8, 2, 5]);
    expect(snapMove([0, 0, 0], [-0.4, 0, 2.5001])).toEqual([0, 0, 3]);
    expect(snapTo(0.1 + 0.2, 0.1)).toBe(0.3);
    expect(Object.is(snapTo(-0.2, 1), 0)).toBe(true);
  });

  it("snaps_a_turn_to_15_degree_steps_about_the_same_axis", () => {
    const start = new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), 0.3);
    const cur = new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), (37 * Math.PI) / 180).multiply(start);
    const snapped = snapTurn(start, cur);
    const t = turnBetween(start, snapped);
    expect(t.deg).toBeCloseTo(30, 6);
    expect(t.axis.z).toBeCloseTo(1, 6);
    const back = new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), (-52 * Math.PI) / 180).multiply(start);
    expect(turnBetween(start, snapTurn(start, back)).deg).toBeCloseTo(45, 6);
  });

  it("reads_a_z_angle_without_jumping_at_180", () => {
    const q = (deg: number) => new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), (deg * Math.PI) / 180);
    expect(angleAboutZ(q(30))).toBeCloseTo(30);
    expect(angleAboutZ(q(190), 175)).toBeCloseTo(190);
    expect(angleAboutZ(q(-170), 185)).toBeCloseTo(190);
  });

  it("joint_values_snap_then_clamp", () => {
    expect(snapJointValue(37, "angle", true)).toBe(30);
    expect(snapJointValue(37.04, "angle", false)).toBe(37);
    expect(snapJointValue(2.6, "distance", true, [0, 2])).toBe(2);
    expect(snapJointValue(-50, "angle", true, [0, 180])).toBe(0);
  });
});

describe("face picking", () => {
  const opts = faceOptions(metrics);

  it("offers_flat_faces_and_both_hole_ends", () => {
    expect(opts.find((o) => o.key === "f2")).toMatchObject({ origin: [0, 0, 3], axis: [0, 0, 1] });
    expect(opts.find((o) => o.key === "h0+")).toMatchObject({ origin: [-10, 0, 3], axis: [0, 0, 1] });
    expect(opts.find((o) => o.key === "h0-")).toMatchObject({ origin: [-10, 0, -3], axis: [0, 0, -1] });
    expect(opts.every((o) => !/undefined|NaN/.test(o.label))).toBe(true);
  });

  it("a_click_on_a_face_snaps_to_that_face", () => {
    expect(pickOption(opts, metrics, [5, 3, 3], [0, 0, 1], 45).key).toBe("f2");
    expect(pickOption(opts, metrics, [20, 4, 1], [1, 0, 0], 45).origin).toEqual([20, 0, 0]);
  });

  it("a_click_on_a_hole_wall_picks_the_nearer_hole_end", () => {
    expect(pickOption(opts, metrics, [10 + 2.1, 0, 1.5], [-1, 0, 0], 45).key).toBe("h1+");
    expect(pickOption(opts, metrics, [-10, 2.1, -2], [0, -1, 0], 45).key).toBe("h0-");
  });

  it("anything_else_is_a_raw_pick_at_the_click", () => {
    const o = pickOption(opts, metrics, [1, 1, 1], [0.6, 0.8, 0], 45);
    expect(o.key).toBe("pick");
    expect(o.axis).toEqual([0.6, 0.8, 0]);
  });

  it("joint_body_mates_the_faces_unless_flipped", () => {
    const d = { ...emptyDraft("revolute"), a: { instanceId: "i1", option: opts[0] }, b: { instanceId: "i2", option: opts[0] } };
    expect(draftProblem(d)).toBeNull();
    const body = jointBody(d);
    expect(body.b.frame.axis).toEqual(opts[0].axis.map((v) => -v + 0));
    expect(body.a.frame.axis).toEqual(opts[0].axis);
    expect(body.limits).toEqual([-90, 90]);
    expect(jointBody({ ...d, flip: true }).b.frame.axis).toEqual(opts[0].axis);
    expect(draftProblem({ ...d, b: { instanceId: "i1", option: opts[1] } })).toMatch(/different part/);
    expect(draftProblem({ ...d, lo: 5, hi: 1 })).toMatch(/lower limit/);
    expect(jointBody({ ...emptyDraft("cylindrical"), a: d.a, b: d.b }).value).toEqual([0, 0]);
  });
});
