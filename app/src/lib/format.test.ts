import { describe, expect, it } from "vitest";
import { fmtClock, fmtElapsed, fmtGrams, fmtHM, fmtMm, fmtSize, fmtTokens, fmtUsd, fmtSigned, shortHash } from "./format";

describe("format", () => {
  const t = new Date(2026, 9, 24, 14, 3, 9).getTime() / 1000;
  it("clock times", () => {
    expect(fmtClock(t)).toBe("14:03:09");
    expect(fmtHM(t)).toBe("14:03");
  });
  it("elapsed", () => {
    expect(fmtElapsed(0)).toBe("0s");
    expect(fmtElapsed(42.7)).toBe("42s");
    expect(fmtElapsed(134)).toBe("2m 14s");
    expect(fmtElapsed(3720)).toBe("1h 02m");
  });
  it("units", () => {
    expect(fmtMm(6)).toBe("6.00 mm");
    expect(fmtGrams(12.4)).toBe("12.40 g");
    expect(fmtSize([40, 20, 6])).toBe("40.00 × 20.00 × 6.00");
    expect(fmtUsd(0.4123)).toBe("$0.41");
    expect(fmtTokens(950)).toBe("950 tok");
    expect(fmtTokens(38_400)).toBe("38k tok");
    expect(fmtTokens(1_250_000)).toBe("1.3M tok");
    expect(fmtSigned(0.8, 2, " g")).toBe("+0.80 g");
    expect(fmtSigned(-772.3, 0, " mm³")).toBe("−772 mm³");
  });
  it("short hash is 8 chars", () => {
    expect(shortHash("f340f4e1fa8ae34092bf3a8c")).toBe("f340f4e1");
  });
});
