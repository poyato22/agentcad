// The core loop end to end against the fake core (scenario core-loop.json): start a run, watch
// it work, review the change, approve it from the card, see it finish. Plus the stream-gap,
// token-rotation and stop-all paths, and the visual rules (§5.1) over the whole running app.
import type { Page } from "@playwright/test";
import { expect, test, type FakeControl } from "./fixtures";
import { CORE_TOKEN, CORE_URL } from "./ports";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const ARM_WAIT_MS = 450;
const TASK = "make the plate 6 mm thick";
const LABEL = "designer · make plate 6 mm";
const HASH = "5c0e1a7d9b3f2e4c6a8d0b1f3e5c7a9d2b4f6e8a0c1d3e5f7a9b1c3d5e7f9a0b";

const app = (page: Page) => page.locator("[data-region=app]");
const rosterRow = (page: Page, label = LABEL) => page.locator("[data-roster] [data-run]", { hasText: label });
const rows = (page: Page, kind?: string) => page.locator(kind ? `[data-row][data-kind="${kind}"]` : "[data-row]");

async function open(page: Page): Promise<void> {
  await page.goto("/");
  await expect(app(page)).toHaveAttribute("data-conn", "open");
}

/** ⌘N → designer + task → Start. Returns the new run id (from the fake core's call log). */
async function startRun(page: Page, fake: FakeControl, task = TASK): Promise<string> {
  await page.keyboard.press("Meta+n");
  const dialog = page.getByRole("dialog", { name: "New run" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Agent").selectOption("designer");
  await dialog.getByLabel("Task").fill(task);
  await dialog.getByRole("button", { name: "Start" }).click();
  await expect(dialog).toBeHidden();
  const runs = await (await fetch(`${CORE_URL}/runs`, { headers: { Authorization: `Bearer ${fake.token}` } })).json() as { id: string; task: string }[];
  const run = runs.find((r) => r.task === task);
  if (!run) throw new Error("run not created");
  return run.id;
}

test.beforeEach(async ({ page, fake }) => {
  await fake.reset("core-loop");
  await page.setViewportSize({ width: 1440, height: 900 });
});

// back to the shared token (token_rotation_reconnects changes it) for the next spec
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

test("create_run_to_merge", async ({ page, fake }) => {
  await open(page);
  const v = page.getByTestId("var-thickness");
  await expect(v).toHaveText("5.00 mm");
  await expect(v).not.toHaveAttribute("data-changed", "true");
  // the viewer shows the 5 mm plate first
  await expect(page.getByTestId("viewer-canvas")).toHaveAttribute("data-bbox-size", "40.00,20.00,5.00", { timeout: 15_000 });
  const firstGlb = (await fake.calls()).filter((c) => c.path === "/parts/mount_plate/model.glb");
  expect(firstGlb).toHaveLength(1);

  const runId = await startRun(page, fake);
  const row = rosterRow(page);
  await expect(row).toHaveCount(1);
  await expect(row).toHaveAttribute("aria-selected", "true");
  await expect(row.locator(".led")).toHaveClass(/led-blue/);           // running
  await expect(row).toContainText(/\d+s/);                             // elapsed while running

  // five successful calls in a row (turns with no text in between): the first shows, four fold;
  // the change's diff never folds
  await expect(page.getByRole("button", { name: "▸ 4 more tool calls · mirror_measure · mirror_part_set_vars" })).toBeVisible();
  await expect(page.locator('[data-row][data-key="c:c1"]')).toContainText("mirror_part_list");
  await expect(rows(page, "diff")).toHaveCount(1);
  await expect(rows(page, "diff")).toContainText("Part · mount plate");

  // Variables: thickness turns blue 5.00 → 6.00
  await expect(v).toHaveText("6.00 mm");
  await expect(v).toHaveAttribute("data-changed", "true");
  await expect(v).toHaveCSS("color", "rgb(141, 186, 255)");

  // the viewer reloads the new GLB, revalidating with the ETag it had
  await expect(page.getByTestId("viewer-canvas")).toHaveAttribute("data-bbox-size", "40.00,20.00,6.00", { timeout: 15_000 });
  const glb = (await fake.calls()).filter((c) => c.path === "/parts/mount_plate/model.glb");
  expect(glb.length).toBeGreaterThanOrEqual(2);
  expect(glb[0].ifNoneMatch).toBeUndefined();
  expect(glb[1].ifNoneMatch).toMatch(/^"\d+-\d+"$/);

  // the approval card: hash-bound, with evidence links into the log
  const card = page.locator(`[data-card][data-approval="ap_${runId}"]`);
  await expect(card).toBeVisible();
  await expect(card).toContainText(`hash ${HASH.slice(0, 8)}`);
  await expect(card).toContainText("measure · Part · mount plate");
  await expect(card).toContainText(LABEL);
  await expect(page.locator("[data-roster-head]")).toContainText("1 needs you");

  // focus the card and press m (after the arm delay): one decide with the card's hash
  await card.focus();
  await page.waitForTimeout(ARM_WAIT_MS);
  await page.keyboard.press("m");
  await expect(card).toHaveAttribute("data-status", "approved");
  const decides = (await fake.calls()).filter((c) => c.path.endsWith("/decide"));
  expect(decides.map((c) => c.body)).toEqual([{ payload_hash: HASH, approve: true }]);

  // the core merges and finishes: green LED, final text, nothing needs you
  await expect(row.locator(".led")).toHaveClass(/led-green/);
  await expect(row).toContainText("done");
  await expect(rows(page, "message").filter({ hasText: "Merged: the plate is 6 mm thick." })).toHaveCount(1);
  await expect(page.locator("[data-roster-head]")).toContainText("0 needs you");
  expect((await fake.calls()).filter((c) => c.path.endsWith("/decide"))).toHaveLength(1);
  expect(runId).toMatch(/^[0-9a-f]{12}$/);
});

/** Every row key once (folds opened so every call shows). */
async function rowKeys(page: Page): Promise<string[]> {
  for (;;) {
    const fold = page.getByRole("button", { name: /^▸ \d+ more tool calls/ }).first();
    if ((await fold.count()) === 0) break;
    // folds are not approvals: a DOM click is fine, and it avoids racing the next frame's re-render
    const text = (await fold.textContent()) ?? "";
    await fold.evaluate((b) => (b as HTMLButtonElement).click());
    await expect(page.getByRole("button", { name: text, exact: true })).toHaveCount(0);
  }
  return page.locator("[data-row]").evaluateAll((els) => els.map((e) => e.getAttribute("data-key") ?? ""));
}

test("ws_drop_resyncs_without_duplicates", async ({ page, fake }) => {
  await open(page);
  const runId = await startRun(page, fake);
  await expect(page.locator(`[data-card][data-approval="ap_${runId}"]`)).toBeVisible();
  // a burst of 40 calls; the socket dies in the middle and events 15..29 are lost
  const N = 40;
  let callsAtDrop = 0;
  for (let i = 0; i < N; i++) {
    if (i === 15) { callsAtDrop = (await fake.calls()).length; await fake.dropSockets("terminate"); }
    const cid = `burst${i}`;
    await fake.emit({ type: "tool_call", run_id: runId, data: { call_id: cid, name: "mirror_measure", input: { part_id: "mount_plate" } } }, {
      steps: [{ run_id: runId, kind: "tool_call", payload: { call_id: cid, name: "mirror_measure", input: { part_id: "mount_plate" } } }],
    });
    await fake.emit({ type: "tool_result", run_id: runId, data: { call_id: cid, name: "mirror_measure", ok: true, summary: "ok" } }, {
      steps: [{ run_id: runId, kind: "measure", payload: { call_id: cid, name: "mirror_measure", output: { ok: true, summary: "ok", data: { part_id: "mount_plate" }, handles: [], warnings: [], step_kind: "measure" } } }],
    });
  }
  // reconnected (the open resync fetched /runs again) and caught up from /steps?after_id=
  await expect(app(page)).toHaveAttribute("data-conn", "open");
  await expect.poll(async () => {
    return (await fake.calls()).slice(callsAtDrop).filter((c) => c.path === "/runs").length;
  }).toBeGreaterThan(0);
  // every burst call landed: burst0 shows, burst1..burst39 fold into one row
  await expect(page.getByRole("button", { name: `▸ ${N - 1} more tool calls · mirror_measure` })).toBeVisible();
  const keys = await rowKeys(page);
  const burst = keys.filter((k) => k.startsWith("c:burst"));
  expect(burst).toEqual(Array.from({ length: N }, (_, i) => `c:burst${i}`));      // each once, in order
  expect(new Set(keys).size).toBe(keys.length);                                     // no duplicate row anywhere
});

test("token_rotation_reconnects", async ({ page, fake }) => {
  // run "inside the shell": core_info comes from invoke(), like the Tauri app (mocked IPC)
  await page.addInitScript(({ url, token }) => {
    const w = window as unknown as Record<string, unknown>;
    w.__coreToken = token;
    let next = 1;
    w.isTauri = true;
    w.__TAURI_INTERNALS__ = {
      transformCallback: (cb: unknown) => { const id = next++; w[`_${id}`] = cb; return id; },
      invoke: async (cmd: string) => {
        if (cmd === "core_info") return { baseUrl: url, wsUrl: url.replace("http", "ws") + "/events", token: w.__coreToken, owned: true };
        if (cmd === "plugin:event|listen") return next++;
        return null;
      },
      metadata: { currentWindow: { label: "main" }, currentWebview: { windowLabel: "main", label: "main" } },
    };
  }, { url: CORE_URL, token: CORE_TOKEN });
  await open(page);
  // the core restarts with a new token: the socket is closed, the reconnect gets 4401
  await fake.rotateToken("rotated-token");
  await expect(app(page)).toHaveAttribute("data-conn", "unauthorized");
  // the shell now reports the new token: the app re-resolves core info and reconnects
  await page.evaluate(() => { (window as unknown as Record<string, unknown>).__coreToken = "rotated-token"; });
  await expect(app(page)).toHaveAttribute("data-conn", "open", { timeout: 15_000 });
  // and the resync went through with the new token (the fake only logs authorized calls)
  await expect.poll(async () => (await fake.calls()).map((c) => c.path)).toContain("/runs");
  await expect(page.locator('[data-light="core"] .led')).toHaveClass(/led-green/);
});

test("stop_all_mid_run", async ({ page, fake }) => {
  await open(page);
  await startRun(page, fake);
  const row = rosterRow(page);
  await expect(page.getByRole("button", { name: /^▸ \d+ more tool calls/ }).first()).toBeVisible();
  await expect(row.locator(".led")).toHaveClass(/led-blue/);
  // ⌘⇧. from inside the command line
  await page.getByLabel("Command line").focus();
  await page.keyboard.press("Meta+Shift+Period");
  await expect(page.getByRole("status")).toHaveText("stopped 1");
  expect((await fake.calls()).filter((c) => c.path === "/runs/cancel_all")).toHaveLength(1);
  await expect(row).toContainText("cancelled");
  await expect(row.locator(".led")).not.toHaveClass(/led-blue/);
  await expect(page.locator('[data-light="agents"]')).toContainText("0 agents");
});

// ---- visual rules over the whole app, mid-run (cards, changing vars, folds, selected rows)

async function midRun(page: Page, fake: FakeControl): Promise<void> {
  await open(page);
  const first = await startRun(page, fake);
  await expect(page.locator(`[data-card][data-approval="ap_${first}"]`)).toBeVisible();
  // a second designer run whose label would read the same: it must differ by what differs
  // (here the start time), never by a counter
  const second = await startRun(page, fake, "make the plate 6 mm wide");
  await expect(page.locator(`[data-card][data-approval="ap_${second}"]`)).toBeVisible();
  await expect(page.getByTestId("var-thickness")).toHaveAttribute("data-changed", "true");
  await expect(page.getByRole("button", { name: /^▸ \d+ more tool calls/ }).first()).toBeVisible();
  await page.mouse.move(0, 0);
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); });
}

test("no_auto_numbered_anywhere", async ({ page, fake }) => {
  await midRun(page, fake);
  const found = await page.evaluate(() => {
    const TRAILING_NUMBER = /\d+\s*$/;
    const CODE_NAME = /^[A-Z]+-\d+$/;
    const auto = (s: string) => TRAILING_NUMBER.test(s) || CODE_NAME.test(s.trim());
    // allowed tiebreakers in labels (design §5.1): model ids, HH:MM(:SS) start times
    const models = new Set([...document.querySelectorAll(".con-ag-who")].flatMap((e) => (e.textContent ?? "").split(" · ")));
    const tiebreak = (s: string) => models.has(s.trim()) || /^[a-z][a-z0-9.]*(-[a-z0-9.]+)*-\d+(\.\d+)?$/.test(s.trim()) || /^\d\d:\d\d(:\d\d)?$/.test(s.trim());
    const bad: string[] = [];
    // 1) every "Type · purpose" text node
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = (n.textContent ?? "").trim();
      if (!t.includes(" · ")) continue;
      const [type, ...rest] = t.split(" · ");
      if (!/^[A-Za-z][\w-]*$/.test(type)) continue;               // a value line ("2 running · …"), not a name
      for (const seg of rest) if (auto(seg) && !tiebreak(seg)) bad.push(t);
    }
    // 2) every label cell, whole
    for (const el of document.querySelectorAll("[data-label], [data-run-label], [data-crumb], .tb-name, .acard-run, .pane-title")) {
      const t = (el.textContent ?? "").trim();
      const last = t.split(" · ").at(-1) ?? t;
      if (auto(t) && !tiebreak(last)) bad.push(t);
    }
    return { bad, labels: [...document.querySelectorAll("[data-run-label], [data-label]")].map((e) => e.textContent) };
  });
  expect(found.labels.length).toBeGreaterThanOrEqual(3);
  expect(found.bad).toEqual([]);
});

test("blue_only_where_allowed", async ({ page, fake }) => {
  await midRun(page, fake);
  const offenders = await page.evaluate(() => {
    const BLUE = new Set(["rgb(61, 139, 255)", "rgb(141, 186, 255)"]);
    const ALLOWED = "[data-selected], [data-changing], [data-card], .active-tab, .cmd-prompt, .needs-you";
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("body *")) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      const blue = [cs.color, cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor, cs.backgroundColor]
        .filter((c, i) => BLUE.has(c) && (i === 0 ? (el.textContent ?? "").trim() !== "" : i === 5 || parseFloat([cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth][i - 1]) > 0));
      if (blue.length && !el.closest(ALLOWED)) out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} "${(el.textContent ?? "").trim().slice(0, 40)}"`);
    }
    return out;
  });
  expect(offenders).toEqual([]);
});
