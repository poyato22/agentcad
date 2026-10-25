import { expect, test } from "./fixtures";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("layout_regions_exist", async ({ page }) => {
  await page.goto("/");
  const box = async (region: string) => {
    const b = await page.locator(`[data-region="${region}"]`).boundingBox();
    if (!b) throw new Error(`region ${region} not rendered`);
    return b;
  };
  const top = await box("topbar");
  const left = await box("left");
  const viewer = await box("viewer");
  const consoleBox = await box("console");
  expect(top.height).toBe(44);
  expect(top.width).toBe(1440);
  expect(left.width).toBe(240);
  expect(consoleBox.width).toBe(420);
  expect(viewer.width).toBe(1440 - 240 - 420);
  // console spans the full height below the top bar
  expect(consoleBox.y).toBe(44);
  expect(consoleBox.height).toBe(900 - 44);
  expect(left.y).toBe(44);
  expect(viewer.y).toBe(44);
});

test("min_window_size_is_1280_by_800", async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 600 });
  await page.goto("/");
  const b = await page.locator("[data-region=app]").boundingBox();
  expect(b?.width).toBe(1280);
  expect(b?.height).toBe(800);
});

test("tokens_exact", async ({ page }) => {
  await page.goto("/");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).toBe("rgb(10, 10, 10)");
  const tokens = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    const names = ["--bg", "--paper", "--paper2", "--head", "--vp", "--line", "--line2", "--ink", "--dim", "--faint",
      "--blue", "--blue2", "--blue-tint", "--green", "--amber", "--red"];
    return Object.fromEntries(names.map((n) => [n, s.getPropertyValue(n).trim()]));
  });
  expect(tokens).toEqual({
    "--bg": "#0a0a0a", "--paper": "#111111", "--paper2": "#161616", "--head": "#141414", "--vp": "#0d0d0d",
    "--line": "#242424", "--line2": "#333333", "--ink": "#ececec", "--dim": "#9a9a9a", "--faint": "#5e5e5e",
    "--blue": "#3d8bff", "--blue2": "#8dbaff", "--blue-tint": "rgba(61,139,255,.10)",
    "--green": "#4fd18b", "--amber": "#f2b134", "--red": "#ff5a4a",
  });
  const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  expect(font).toContain("IBM Plex Mono");
  const size = await page.evaluate(() => getComputedStyle(document.body).fontSize);
  expect(size).toBe("12px");
});

test("square_corners", async ({ page }) => {
  await page.goto("/");
  // make sure there is at least one of each kind on the page
  await page.evaluate(() => {
    const host = document.createElement("div");
    host.id = "corner-probe";
    const b = document.createElement("button"); b.textContent = "probe"; b.style.borderRadius = "8px";
    const i = document.createElement("input"); i.style.borderRadius = "8px";
    const c = document.createElement("div"); c.setAttribute("data-card", ""); c.style.borderRadius = "8px";
    host.append(b, i, c);
    document.body.append(host);
  });
  const radii = await page.evaluate(() =>
    [...document.querySelectorAll("button, input, textarea, select, [data-card]")].map((el) => getComputedStyle(el).borderRadius));
  expect(radii.length).toBeGreaterThanOrEqual(3);
  for (const r of radii) expect(r).toBe("0px");
});

test("fonts_are_local", async ({ page }) => {
  const remote: string[] = [];
  const fontFiles: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (/fonts\.(googleapis|gstatic)\.com/.test(u)) remote.push(u);
    if (/\.woff2?(\?|$)/.test(u)) fontFiles.push(u);
  });
  await page.goto("/");
  const loaded = await page.evaluate(async () => {
    await document.fonts.load('12px "IBM Plex Mono"');
    await document.fonts.load('600 14px "IBM Plex Sans Condensed"');
    await document.fonts.ready;
    const faces = [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replace(/["']/g, ""));
    return [...new Set(faces)].sort();
  });
  expect(loaded).toEqual(["IBM Plex Mono", "IBM Plex Sans Condensed"]);
  expect(fontFiles.length).toBeGreaterThan(0);
  for (const u of fontFiles) expect(new URL(u).origin).toBe(new URL(page.url()).origin);
  expect(remote).toEqual([]);
});

test("connects_to_fake_core_without_errors", async ({ page, fake }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  // the resync on open went through CORS + bearer auth; the viewer then loads the first part's GLB;
  // the setup gate and the Profile button each read once; the selection goes to PUT /context
  // (once at start, again when the roster picks a run: the count depends on timing)
  await expect.poll(async () => (await fake.calls()).map((c) => c.path).filter((p) => p !== "/context").sort()).toEqual(
    ["/approvals", "/parts", "/parts/mount_plate/model.glb", "/profile", "/runs", "/runs/b7c79a57cbb4/steps", "/setup", "/status"]);
  const ctx = (await fake.calls()).filter((c) => c.path === "/context");
  expect(ctx.length).toBeGreaterThanOrEqual(1);
  expect(ctx.every((c) => c.method === "PUT")).toBe(true);
  expect(errors).toEqual([]);
});

test("tauri_startup_crash_before_listen_shows_red_core_light_with_reason", async ({ page }) => {
  // Mocked Tauri IPC: the shell already crashed at setup ("uv" not found) and emitted that before
  // the webview listened, so no core-status event ever arrives; core_info rejects with crashed:.
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    const calls: string[] = [];
    w.__ipcCalls = calls;
    w.isTauri = true;
    let cb = 0;
    w.__TAURI_INTERNALS__ = {
      transformCallback: () => ++cb,
      unregisterCallback: () => {},
      invoke: (cmd: string) => {
        calls.push(cmd);
        const reason = "could not find `uv`; install it with `brew install uv`";
        if (cmd === "core_status") return Promise.resolve({ state: "crashed", code: null, stderrTail: reason, restarts: 0 });
        if (cmd === "core_info") return Promise.reject(`crashed: ${reason}`);
        if (cmd === "core_restart") return Promise.resolve(null);
        if (cmd.startsWith("plugin:event|")) return Promise.resolve(1);
        return Promise.reject(`unexpected command ${cmd}`);
      },
    };
  });
  await page.goto("/");
  const core = page.locator("[data-light=core]");
  await expect(core.locator(".led")).toHaveClass(/led-red/);
  await expect(core).toHaveAttribute("title", /could not find `uv`/);
  await expect(core).toHaveAttribute("role", "button");
  await core.click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __ipcCalls: string[] }).__ipcCalls)).toContain("core_restart");
});
