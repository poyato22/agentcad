// A walk study names its joints: the per-joint table shows the part each joint moves, its actuator,
// the peak and the use of stall as a bar against the 60% rule (the knee at 85% reads as a fail).
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import { assemblyStudyRecordSchema } from "../../../../api/assemblyStudies";
import { JointTable } from "./AssemblyStudies";

afterEach(cleanup);

describe("JointTable", () => {
  it("lists named joints sorted by name with their share of stall", () => {
    const rec = assemblyStudyRecordSchema.parse({
      study_id: "as_1", project_id: "b1", kind: "walk", name: "Walk", status: "done", verdict: "fail", created_at: 1,
      joints: [
        { joint_id: "f4f7ee91fa27", label: "rear left shank", actuator: "servo_st3215_hs@1.24", peak_nm: 2.07, use_of_stall: 0.853, status: "fail" },
        { joint_id: "34fa117e3ca5", label: "front left hip bracket", actuator: "servo_st3215_hs", peak_nm: 0.31, use_of_stall: 0.158, status: "pass" },
        { joint_id: "abc", label: null, actuator: null, peak_nm: 0.1, use_of_stall: null, status: "not_checked" },
        { bad: true },
      ],
    });
    expect(rec.joints).toHaveLength(3);
    render(<JointTable rows={rec.joints} />);
    const rows = screen.getAllByTestId("asy-joint");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("abc"),
      expect.stringContaining("front left hip bracket"),
      expect.stringContaining("rear left shank"),
    ]);
    expect(rows[2].getAttribute("data-status")).toBe("fail");
    expect(rows[2].textContent).toContain("85 %");
    expect(rows[2].textContent).toContain("2.07 N m");
    expect((rows[2].querySelector(".asy-bar i") as HTMLElement).style.width).toBe("85.3%");
    expect(rows[0].textContent).toContain("not checked");
  });

  it("a record without joints still parses (older cores)", () => {
    const rec = assemblyStudyRecordSchema.parse({ study_id: "as_2", project_id: "b1", kind: "walk", name: "Walk", status: "done", verdict: "pass", created_at: 1 });
    expect(rec.joints).toEqual([]);
  });
});
