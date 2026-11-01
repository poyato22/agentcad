// Drives the app at http://localhost:1420 (started by scripts/smoke-real-core.sh) through one
// run against the REAL core, headless, and prints what it saw. Not part of npm test / e2e.
//
//   npx tsx test/smoke/drive-smoke.ts [--model <vendor/model>] [--task <text>] [--approve] [--timeout <s>]
//
// Checks: the core light goes green, a run starts from the first chat message, tool rows appear, the part
// shows in the part list (with mass) and in the viewer (bbox), and, with --approve, the approval
// card is approved by a trusted key press while focused and the run finishes.
import { chromium } from "@playwright/test";

const arg = (name: string): string | undefined => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : undefined;
};
const TASK = arg("task") ?? "make a 40×20×5 mm plate called mount_plate with two 4.2 mm holes and measure it";
const MODEL = arg("model");
const APPROVE = process.argv.includes("--approve");
const TIMEOUT = Number(arg("timeout") ?? 180) * 1000;

const log = (...a: unknown[]) => console.log(`[smoke ${new Date().toISOString().slice(11, 19)}]`, ...a);

async function main(): Promise<void> {
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  // the roster (Full log) names the run the message started
  await page.addInitScript(() => { try { localStorage.setItem("mises.console.view", "details"); } catch { /* none */ } });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  const glb: { status: number; inm: string | null }[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/model.glb")) glb.push({ status: r.status(), inm: r.request().headers()["if-none-match"] ?? null });
    if (/\/approvals\/[^/]+\/decide/.test(r.url())) void r.text().then((t) => log("decide", r.status(), r.request().postData(), "→", t.slice(0, 160)));
  });
  try {
    await page.goto("http://localhost:1420/");
    await page.locator("[data-region=app][data-conn=open]").waitFor({ timeout: 30_000 });
    log("connected; core light:", await page.locator('[data-light="core"] .led').getAttribute("data-tone"));
    log("status lights:", (await page.locator(".lights").innerText()).replace(/\s+/g, " "));

    // P10 E24: one chat per build — the first message starts the build's lead on the default
    // model (set the profile's default model for --model)
    if (MODEL) log("--model is the profile's default model now; set it in Profile before the smoke");
    const cmd = page.getByLabel("Command line");
    await cmd.fill(TASK);
    log("messaging the build's lead ·", JSON.stringify(TASK));
    const before = new Set(await page.locator("[data-roster] [data-run]").evaluateAll((els) => els.map((e) => e.getAttribute("data-run"))));
    await cmd.press("Enter");
    const err = page.locator("[data-command-line] [role=alert]");
    await Promise.race([page.waitForFunction(() => (document.querySelector("[aria-label='Command line']") as HTMLTextAreaElement | null)?.value === ""), err.waitFor()]);
    if (await err.isVisible()) throw new Error(`send error: ${await err.innerText()}`);

    await page.waitForFunction((old) => { const id = document.querySelector("[data-roster] [data-run][aria-selected=true]")?.getAttribute("data-run"); return !!id && !old.includes(id); }, [...before]);
    const runId = await page.locator("[data-roster] [data-run][aria-selected=true]").getAttribute("data-run");
    const row = page.locator(`[data-roster] [data-run="${runId}"]`);
    log("roster row:", (await row.innerText()).replace(/\s+/g, " "));

    // tool rows, then the part in the list and the viewer
    await page.locator('[data-row][data-kind="call"], [data-row][data-kind="fold"]').first().waitFor({ timeout: TIMEOUT });
    await page.locator("[data-region=left] button", { has: page.locator("[data-label]", { hasText: "Part · mount plate" }) }).waitFor({ timeout: TIMEOUT });
    log("part row:", (await page.locator("[data-region=left] button", { has: page.locator("[data-label]", { hasText: "Part · mount plate" }) }).innerText()).replace(/\s+/g, " "));
    await page.locator("[data-testid=viewer-canvas][data-bbox-size]").waitFor({ timeout: 30_000 });
    log("viewer bbox:", await page.getByTestId("viewer-canvas").getAttribute("data-bbox-size"));
    log("title block:", (await page.locator(".tb, [data-testid=title-block]").first().innerText().catch(() => "?")).replace(/\s+/g, " "));

    if (APPROVE) {
      const card = page.locator("[data-card][data-status=pending]").first();
      await card.waitFor({ timeout: TIMEOUT });
      log("approval card:", (await card.innerText()).replace(/\s+/g, " ").slice(0, 240));
      await card.focus();
      log("focused:", await page.evaluate(() => document.activeElement?.getAttribute("data-approval") ?? document.activeElement?.tagName));
      await page.waitForTimeout(450);
      await page.keyboard.press("m");
      await page.locator("[data-card][data-status=approved]").first().waitFor({ timeout: 15_000 });
      log("approved from the focused card (key m)");
    }

    await row.locator('.led[data-tone="green"], .led[data-tone="red"], .led[data-tone="faint"]').waitFor({ timeout: TIMEOUT });
    log("run finished:", (await row.innerText()).replace(/\s+/g, " "));
    await page.waitForTimeout(1500); // let the last steps land
    const calls = await page.locator('[data-row][data-kind="call"] [data-call-head]').allInnerTexts();
    log("tool rows:", calls.map((c) => c.replace(/\s+/g, " ")).join(" | "));
    const folds = await page.locator('[data-row][data-kind="fold"]').allInnerTexts();
    if (folds.length) log("folds:", folds.join(" | "));
    log("GLB requests:", JSON.stringify(glb));
    log("page errors:", errors.length ? errors : "none");
  } catch (e) {
    log("FAILED:", e instanceof Error ? e.message : e);
    await page.screenshot({ path: "test-results/smoke-failure.png", fullPage: true }).catch(() => {});
    log("page text:", (await page.locator("[data-region=console]").innerText().catch(() => "")).slice(0, 2000));
    process.exitCode = 1;
  } finally {
    await page.screenshot({ path: "test-results/smoke-final.png" }).catch(() => {});
    await browser.close();
  }
}

void main();
