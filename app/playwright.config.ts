import { defineConfig, devices } from "@playwright/test";
import { APP_ORIGIN, CORE_PORT, CORE_TOKEN, CORE_URL, VITE_PORT } from "./test/e2e/ports";

// One fake core and one Vite server are shared by all specs, so tests run serially.
export default defineConfig({
  testDir: "test/e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: [["list"]],
  use: {
    baseURL: APP_ORIGIN,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `npx tsx test/fake-core/cli.ts --port ${CORE_PORT} --token ${CORE_TOKEN} --scenario base --origin ${APP_ORIGIN}`,
      url: `${CORE_URL}/health`, // 401 without a token still counts as "up"
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 30_000,
    },
    {
      command: `npx vite --mode test --port ${VITE_PORT} --strictPort`,
      url: APP_ORIGIN,
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      timeout: 60_000,
      env: { VITE_MISES_URL: CORE_URL, VITE_MISES_TOKEN: CORE_TOKEN },
    },
  ],
});
