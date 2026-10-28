// @vitest-environment node
// scripts/smoke-real-core.sh must never point the core at a real MISES_HOME: it always makes a
// fresh temp dir (ignoring an inherited MISES_HOME), refuses a non-temp path and removes the dir
// on exit. MISES_SMOKE_DRY_RUN=1 stops right after the setup (no core, no Vite).
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SCRIPT = resolve(import.meta.dirname, "../scripts/smoke-real-core.sh");

function dryRun(env: Record<string, string>) {
  const r = spawnSync("bash", [SCRIPT], { env: { ...process.env, MISES_SMOKE_DRY_RUN: "1", ...env }, encoding: "utf8" });
  const home = /MISES_HOME=(\S+)/.exec(r.stdout)?.[1] ?? null;
  return { status: r.status, stdout: r.stdout, stderr: r.stderr, home };
}

describe("smoke-real-core.sh", () => {
  it("ignores_an_inherited_mises_home_and_removes_its_temp_home_on_exit", () => {
    const real = mkdtempSync(join(tmpdir(), "not-for-smoke-"));
    try {
      const r = dryRun({ MISES_HOME: real });
      expect(r.status).toBe(0);
      expect(r.home).not.toBeNull();
      expect(realpathSync(real)).not.toBe(r.home);
      expect(r.home).toMatch(/^\/(private\/)?(tmp|var\/folders)\//);
      expect(existsSync(r.home as string)).toBe(false);   // rm -rf on EXIT
      expect(existsSync(real)).toBe(true);                 // the inherited one is untouched
    } finally {
      rmSync(real, { recursive: true, force: true });
    }
  });

  it("a_tmpdir_that_does_not_exist_fails_without_removing_anything", () => {
    // a copy of the script in a throwaway "app" folder: the old script ran `cd ""` here and then
    // rm -rf'd the app folder (it did, when two test runs raced on one shared TMPDIR)
    const sandbox = mkdtempSync(join(tmpdir(), "smoke-sandbox-"));
    try {
      mkdirSync(join(sandbox, "app/scripts"), { recursive: true });
      copyFileSync(SCRIPT, join(sandbox, "app/scripts/smoke-real-core.sh"));
      writeFileSync(join(sandbox, "app/marker"), "keep me");
      const r = spawnSync("bash", [join(sandbox, "app/scripts/smoke-real-core.sh")], {
        env: { ...process.env, MISES_SMOKE_DRY_RUN: "1", TMPDIR: join(sandbox, "no-such-dir") }, encoding: "utf8",
      });
      expect(r.status).not.toBe(0);
      expect(r.stdout).not.toMatch(/MISES_HOME=/);
      expect(existsSync(join(sandbox, "app/marker"))).toBe(true);
    } finally {
      rmSync(sandbox, { recursive: true, force: true });
    }
  });

  it("refuses_a_home_outside_the_system_temp_dirs", () => {
    // unique per run: concurrent test runs must not remove each other's TMPDIR
    mkdirSync(resolve(import.meta.dirname, "../node_modules/.cache"), { recursive: true });
    const notTemp = mkdtempSync(resolve(import.meta.dirname, "../node_modules/.cache/smoke-not-temp-"));
    try {
      const r = dryRun({ TMPDIR: notTemp });
      expect(r.status).not.toBe(0);
      expect(r.stderr).toMatch(/refusing/);
      expect(r.home).toBeNull();
    } finally {
      rmSync(notTemp, { recursive: true, force: true });
    }
  });
});
