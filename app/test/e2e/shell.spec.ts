import type { Page } from "@playwright/test";
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

test("tokens_exact_dark_is_the_default", async ({ page }) => {
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
  const size = await page.evaluate(() => getComputedStyle(document.body).fontSize);
  expect(size).toBe("12px");
});

test("font_is_iosevka_everywhere_and_no_ibm_plex", async ({ page }) => {
  await page.goto("/");
  // every element's resolved font-family begins with Iosevka and names no IBM Plex
  const bad = await page.evaluate(() => {
    const out: string[] = [];
    const skip = new Set(["SCRIPT", "STYLE", "CANVAS"]);
    for (const el of [document.body, ...Array.from(document.body.querySelectorAll("*"))]) {
      if (skip.has(el.tagName)) continue;
      const ff = getComputedStyle(el).fontFamily;
      if (!ff) continue; // SVG and the like can report no family
      if (/ibm plex/i.test(ff)) out.push(`ibm-plex: ${el.tagName} ${ff}`);
      if (!/^["']?iosevka/i.test(ff.trim())) out.push(`not-iosevka: ${el.tagName} ${ff}`);
    }
    return out.slice(0, 5);
  });
  expect(bad).toEqual([]);
});

test("fonts_are_local_iosevka", async ({ page }) => {
  const remote: string[] = [];
  const fontFiles: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (/fonts\.(googleapis|gstatic)\.com/.test(u)) remote.push(u);
    if (/\.woff2?(\?|$)/.test(u)) fontFiles.push(u);
  });
  await page.goto("/");
  const loaded = await page.evaluate(async () => {
    await document.fonts.load('12px "Iosevka"');
    await document.fonts.load('700 14px "Iosevka"');
    await document.fonts.ready;
    const faces = [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replace(/["']/g, ""));
    return [...new Set(faces)].sort();
  });
  expect(loaded).toEqual(["Iosevka"]);
  expect(fontFiles.length).toBeGreaterThan(0);
  for (const u of fontFiles) expect(new URL(u).origin).toBe(new URL(page.url()).origin);
  expect(remote).toEqual([]);
  expect(fontFiles.every((u) => !/ibm-plex/i.test(u))).toBe(true);
});

// ---- traffic lights: no interactive element or text may sit under the overlay window buttons.
// The reserved zone is the top-left 78px × 28px of the window.
async function offenders(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const ZONE_X = 78, ZONE_Y = 28;
    const interactive = "a,button,input,select,textarea,[role=tab],[role=button],[tabindex]";
    const seen = new Set<Element>();
    const bad: string[] = [];
    const visible = (el: Element): boolean => {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const hasOwnText = (el: Element): boolean =>
      Array.from(el.childNodes).some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim().length > 0);
    const check = (el: Element, why: string) => {
      if (seen.has(el) || !visible(el)) return;
      seen.add(el);
      const r = el.getBoundingClientRect();
      if (r.left < ZONE_X && r.top < ZONE_Y && r.right > 0 && r.bottom > 0) {
        bad.push(`${why}: ${el.tagName}.${(el as HTMLElement).className} @ ${Math.round(r.left)},${Math.round(r.top)}`);
      }
    };
    for (const el of Array.from(document.querySelectorAll(interactive))) check(el, "interactive");
    for (const el of Array.from(document.querySelectorAll("body *"))) if (hasOwnText(el)) check(el, "text");
    return bad;
  });
}

for (const size of [{ width: 1280, height: 800 }, { width: 1600, height: 1000 }]) {
  for (const theme of ["dark", "light"] as const) {
    test(`traffic_lights_clear_at_${size.width}x${size.height}_${theme}`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.goto("/");
      await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
      await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
      // check the Design page, then each other page, and with the console collapsed
      for (const key of ["1", "2", "3", "4", "5"]) {
        await page.keyboard.press(`Meta+${key}`);
        expect(await offenders(page), `page ${key} / ${theme}`).toEqual([]);
      }
      await page.keyboard.press("Meta+1");
      await page.getByRole("button", { name: "Collapse the agent console" }).click();
      expect(await offenders(page), `console collapsed / ${theme}`).toEqual([]);
    });
  }
}

test("tabs_navigate_between_pages", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=viewer]")).toBeVisible();
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.locator('[data-region="page"]')).toContainText("Studies");
  await expect(page.locator("[data-region=viewer]")).toHaveCount(0);
  await expect(page.locator("[data-region=console]")).toBeVisible(); // console stays on every page
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.locator("[data-region=viewer]")).toBeVisible();
});

test("cmd_1_to_5_navigate", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Meta+3");
  await expect(page.locator('[data-region="page"]')).toContainText("Sketch");
  await expect(page.getByRole("tab", { name: "Sketch" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Meta+5");
  await expect(page.locator('[data-region="page"]')).toContainText("Print");
  await page.keyboard.press("Meta+1");
  await expect(page.locator("[data-region=viewer]")).toBeVisible();
});

test("console_collapses_to_a_rail_and_expands", async ({ page }) => {
  await page.goto("/");
  const consoleRegion = page.locator("[data-region=console]");
  expect((await consoleRegion.boundingBox())?.width).toBe(420);
  await page.getByRole("button", { name: "Collapse the agent console" }).click();
  await expect.poll(async () => (await consoleRegion.boundingBox())?.width).toBe(36);
  await page.getByRole("button", { name: "Expand the agent console" }).click();
  await expect.poll(async () => (await consoleRegion.boundingBox())?.width).toBe(420);
});

test("theme_switch_persists_via_put_profile_and_sets_data_theme", async ({ page, fake }) => {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("button", { name: "Profile" }).click();
  await expect(page.getByRole("dialog", { name: "Profile" })).toBeVisible();
  await page.getByLabel("Theme").selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light"); // previews immediately
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("saved")).toBeVisible();
  const puts = (await fake.calls()).filter((c) => c.method === "PUT" && c.path === "/profile");
  expect(puts.length).toBeGreaterThanOrEqual(1);
  expect((puts.at(-1)?.body as { theme?: string }).theme).toBe("light");
  // light palette is live
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(244, 244, 242)");
});

test("square_corners", async ({ page }) => {
  await page.goto("/");
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

test("tauri_startup_crash_before_listen_shows_red_core_light_with_reason", async ({ page }) => {
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

test("connects_to_fake_core_without_errors", async ({ page, fake }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect.poll(async () => (await fake.calls()).map((c) => c.path).filter((p) => p !== "/context").sort()).toEqual(
    ["/approvals", "/parts", "/parts/mount_plate/model.glb", "/profile", "/runs", "/runs/b7c79a57cbb4/steps", "/setup", "/status"]);
  const ctx = (await fake.calls()).filter((c) => c.path === "/context");
  expect(ctx.length).toBeGreaterThanOrEqual(1);
  expect(ctx.every((c) => c.method === "PUT")).toBe(true);
  expect(errors).toEqual([]);
});
