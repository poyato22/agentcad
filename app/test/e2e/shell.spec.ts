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
  // FW-layout (D20): the build view is full-bleed under the top bar; the Parts surface and the
  // agent dock float over it, 12px in from the window edges
  expect([viewer.x, viewer.y, viewer.width, viewer.height]).toEqual([0, 44, 1440, 900 - 44]);
  expect(left.width).toBe(240);
  expect([left.x, left.y]).toEqual([12, 44 + 12]);
  expect(consoleBox.width).toBe(440);   // the agent dock's default width (Plan 9 visual spec)
  expect(consoleBox.x + consoleBox.width).toBe(1440 - 12);
  expect(consoleBox.y).toBe(44 + 12);
  expect(consoleBox.height).toBe(900 - 44 - 24);
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
  expect(bg).toBe("rgb(20, 22, 26)");                       // Plan 9 Graphite
  const tokens = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    const names = ["--bg", "--paper", "--paper2", "--head", "--vp", "--line", "--line2", "--ink", "--dim", "--faint",
      "--blue", "--blue2", "--blue-tint", "--green", "--amber", "--red"];
    return Object.fromEntries(names.map((n) => [n, s.getPropertyValue(n).trim()]));
  });
  // Plan 9 Visual spec: Graphite / Panel / Raised / Hairline / Ink / Dim; semantic colors unchanged
  expect(tokens).toEqual({
    "--bg": "#14161a", "--paper": "#1b1e23", "--paper2": "#252930", "--head": "#1b1e23", "--vp": "#16181c",
    "--line": "#2f343b", "--line2": "#3b414a", "--ink": "#e9ebee", "--dim": "#9aa1aa", "--faint": "#6b727c",
    "--blue": "#4b93ff", "--blue2": "#8dbaff", "--blue-tint": "rgba(75,147,255,.10)",
    "--green": "#4fd18b", "--amber": "#f2b134", "--red": "#ff5a4a",
  });
  const size = await page.evaluate(() => getComputedStyle(document.body).fontSize);
  expect(size).toBe("12.5px");
});

test("ui_text_is_instrument_sans_and_numbers_iosevka", async ({ page }) => {
  // Plan 9 Visual spec: Instrument Sans for UI text (bundled, offline), Iosevka for numbers only
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const fam = await page.evaluate(() => ({
    body: getComputedStyle(document.body).fontFamily,
    tab: getComputedStyle(document.querySelector(".tab") as Element).fontFamily,
    loaded: document.fonts.check('12px "Instrument Sans"'),
  }));
  expect(fam.body).toMatch(/^"?Instrument Sans/);
  expect(fam.tab).toMatch(/^"?Instrument Sans/);
  expect(fam.body).not.toMatch(/ibm plex/i);
  expect(fam.loaded).toBe(true);
  const num = await page.evaluate(() => {
    const el = document.createElement("span"); el.className = "num"; el.textContent = "1.24 kg";
    document.body.append(el);
    const f = getComputedStyle(el); const out = [f.fontFamily, f.fontVariantNumeric]; el.remove(); return out;
  });
  expect(num[0]).toMatch(/^"?Iosevka/);
  expect(num[1]).toBe("tabular-nums");
});

test("fonts_are_local_instrument_sans_and_iosevka", async ({ page }) => {
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
    await document.fonts.load('12.5px "Instrument Sans"');
    await document.fonts.load('600 14px "Instrument Sans"');
    await document.fonts.ready;
    const faces = [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replace(/["']/g, ""));
    return [...new Set(faces)].sort();
  });
  // Plan 9 Visual spec: Instrument Sans (UI) + Iosevka (numbers), both bundled
  expect(loaded).toEqual(["Instrument Sans", "Iosevka"]);
  expect(fontFiles.length).toBeGreaterThan(0);
  for (const u of fontFiles) expect(new URL(u).origin).toBe(new URL(page.url()).origin);
  expect(remote).toEqual([]);
  expect(fontFiles.every((u) => !/ibm-plex/i.test(u))).toBe(true);
});

// ---- traffic lights: no interactive element or text may sit under the overlay window buttons.
// The reserved zone is the top-left 78px × 32px of the window (lights moved to y 15–29).
async function offenders(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const ZONE_X = 78, ZONE_Y = 32;
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

// ---- every surface that can sit under the traffic lights, in light, dark and system (OS both ways)
type Mode = "light" | "dark" | "system-light" | "system-dark";
const BG = { light: "rgb(242, 243, 245)", dark: "rgb(20, 22, 26)" };

async function useMode(page: Page, mode: Mode): Promise<void> {
  const [attr, os] = mode.startsWith("system") ? ["system", mode.slice(7) as "light" | "dark"] : [mode, mode === "light" ? "dark" : "light"];
  // forced themes run against the opposite OS preference, so they prove the override wins
  await page.emulateMedia({ colorScheme: os as "light" | "dark" });
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), attr);
  const want = mode === "light" || mode === "system-light" ? BG.light : BG.dark;
  await expect(page.locator("body")).toHaveCSS("background-color", want);
}

async function measure(page: Page, mode: Mode, what: string): Promise<void> {
  await useMode(page, mode); // re-apply: closing Profile re-reads the saved (system) theme
  expect(await offenders(page), `${what} / ${mode}`).toEqual([]);
}

const NOTICE_SCENARIO = async () => {
  const sc = JSON.parse(JSON.stringify((await import("../fake-core/scenarios/setup.json", { with: { type: "json" } })).default)) as
    Record<string, unknown> & { setup: { profile: Record<string, unknown> } };
  sc.setup.profile.setup_completed_at = 1790000000; // finished without a model → "Connect a model" notice
  return sc;
};

for (const size of [{ width: 1280, height: 800 }, { width: 1600, height: 1000 }]) {
  for (const mode of ["light", "dark", "system-light", "system-dark"] as const) {
    test(`traffic_lights_clear_on_every_surface_${size.width}x${size.height}_${mode}`, async ({ page, fake }) => {
      await page.setViewportSize(size);
      // Design page with an open approval card in the console
      await page.goto("/");
      await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
      await expect(page.locator("[data-region=console] [data-card]").first()).toBeVisible();
      await measure(page, mode, "design + approval card");
      // new-run dialog (composer)
      await page.keyboard.press("Meta+n");   // the new-run dialog (the console's "New" is an inline form)
      await expect(page.getByRole("dialog", { name: "New run" })).toBeVisible();
      await measure(page, mode, "new-run dialog");
      await page.getByRole("dialog", { name: "New run" }).getByRole("button", { name: "Close" }).click();
      await expect(page.getByRole("dialog", { name: "New run" })).toHaveCount(0);
      // Profile pane
      await page.getByRole("button", { name: "Profile" }).click();
      await expect(page.getByRole("dialog", { name: "Profile" })).toBeVisible();
      await expect(page.getByLabel("Theme")).toBeVisible();
      await measure(page, mode, "profile pane");
      await page.getByRole("dialog", { name: "Profile" }).getByRole("button", { name: "Close" }).click();
      await expect(page.getByRole("dialog", { name: "Profile" })).toHaveCount(0);
      // setup wizard (first run)
      await fake.reset("setup");
      await page.goto("/");
      await expect(page.getByRole("dialog", { name: "Set up Mises" })).toBeVisible();
      await measure(page, mode, "setup wizard");
      // "Connect a model" notice (setup finished, no model)
      await fake.reset(await NOTICE_SCENARIO());
      await page.goto("/");
      await expect(page.getByRole("status", { name: "Connect a model" })).toBeVisible();
      await measure(page, mode, "setup notice");
    });
  }
}

test("no_theme_flash_cached_theme_is_on_html_before_first_render", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    try { localStorage.setItem("mises.theme", "light"); } catch { /* ignore */ }
    const w = window as unknown as { __firstRenderTheme?: string | null };
    const obs = new MutationObserver(() => {
      const app = document.getElementById("app");
      if (app && app.firstElementChild && w.__firstRenderTheme === undefined) {
        w.__firstRenderTheme = document.documentElement.getAttribute("data-theme");
        obs.disconnect();
      }
    });
    obs.observe(document, { childList: true, subtree: true });
  });
  await page.goto("/");
  await expect.poll(() => page.evaluate(() => (window as unknown as { __firstRenderTheme?: string | null }).__firstRenderTheme)).toBe("light");
});

test("color_scheme_follows_the_theme", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("meta[name=color-scheme]")).toHaveAttribute("content", "light dark");
  const scheme = () => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme);
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  expect(await scheme()).toBe("light");
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  expect(await scheme()).toBe("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "system"));
  expect(await scheme()).toBe("light");
});

test("tab_arrow_keys_navigate", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Design" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Studies" })).toBeFocused();
  await expect(page.locator('[data-region="page"]')).toContainText("Studies");
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "BOM" })).toHaveAttribute("aria-selected", "true");
});

test("cmd_digits_do_nothing_while_a_modal_is_open", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Profile" }).click();
  await expect(page.getByRole("dialog", { name: "Profile" })).toBeVisible();
  await page.keyboard.press("Meta+2");
  await expect(page.getByRole("tab", { name: "Design" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("dialog", { name: "Profile" }).getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("dialog", { name: "Profile" })).toHaveCount(0);
  await page.keyboard.press("Meta+2");
  await expect(page.getByRole("tab", { name: "Studies" })).toHaveAttribute("aria-selected", "true");
});

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

test("cmd_1_to_4_navigate", async ({ page }) => {
  await page.goto("/");
  // Plan 9 D9/D21/D22: Design · Studies · Print · BOM; ⌘5–⌘7 do nothing any more
  await expect(page.getByRole("tab", { name: "Sketch" })).toHaveCount(0);
  await page.keyboard.press("Meta+3");
  await expect(page.getByRole("tab", { name: "Print" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Meta+4");
  await expect(page.getByRole("tab", { name: "BOM" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Meta+6");
  await expect(page.getByRole("tab", { name: "BOM" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Meta+1");
  await expect(page.locator("[data-region=viewer]")).toBeVisible();
});

test("console_collapses_to_a_rail_and_expands", async ({ page }) => {
  await page.goto("/");
  const consoleRegion = page.locator("[data-region=console]");
  expect((await consoleRegion.boundingBox())?.width).toBe(440);
  await page.getByRole("button", { name: "Collapse the agent console" }).click();
  // folded: a slim floating tab
  await expect.poll(async () => (await consoleRegion.boundingBox())?.width).toBe(34);
  await page.getByRole("button", { name: "Expand the agent console" }).click();
  await expect.poll(async () => (await consoleRegion.boundingBox())?.width).toBe(440);
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
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(242, 243, 245)");
});

test("rounded_corners_from_the_visual_spec", async ({ page }) => {
  // Plan 9: buttons 7px, rows/inputs 6px, cards and floating surfaces 10px
  await page.goto("/");
  const r = await page.evaluate(() => {
    const host = document.createElement("div");
    const b = document.createElement("button"); b.className = "btn";
    const i = document.createElement("input");
    const c = document.createElement("div"); c.setAttribute("data-card", "");
    const f = document.createElement("div"); f.className = "surface";
    host.append(b, i, c, f);
    document.body.append(host);
    const out = [b, i, c, f].map((el) => getComputedStyle(el).borderRadius);
    host.remove();
    return out;
  });
  expect(r).toEqual(["7px", "6px", "10px", "10px"]);
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
  // U2b: the light lives in the Status card; the button says so in its headline
  const status = page.getByRole("button", { name: /^Status/ });
  await expect(status).toContainText("Core down");
  await status.click();
  await core.click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __ipcCalls: string[] }).__ipcCalls)).toContain("core_restart");
});

test("connects_to_fake_core_without_errors", async ({ page, fake }) => {
  const errors: string[] = [];
  // Chromium's own GPU driver notes ("GL Driver Message ... GPU stall due to ReadPixels") are
  // about the headless GL backend, not the app
  page.on("console", (m) => { if ((m.type() === "error" || m.type() === "warning") && !m.text().includes("GL Driver Message")) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  // A6: /onshape/budget twice (the session figure, then the selected run's top-bar meter); A3: the
  // builds (GET /projects + the profile's tabs); A7: the console spec strip loads the active
  // build's spec and studies. (Plan 7 U2b: no Variables panel, so no GET /parts/{id}/source on boot.)
  await expect.poll(async () => (await fake.calls()).map((c) => c.path.replace(/^\/projects\/[^/]+\//, "/projects/{id}/")).filter((p) => p !== "/context").sort()).toEqual(
    // Plan 9: the build view loads the build's assembly (and its parts), the left pane the BOM
    // Plan 9 B3: the build's running background jobs (GET /jobs)
    ["/approvals", "/jobs", "/onshape/budget", "/onshape/budget", "/parts", "/parts", "/printers", "/profile", "/profile",
      // C-2/C-3: the running run's live activity and open questions are seeded on connect (GET
      // /runs/{id} carries `pending`, as the real core's does, so no GET /runs/{id}/pending)
      "/projects", "/projects/{id}/assembly", "/projects/{id}/bom", "/projects/{id}/bom", "/projects/{id}/spec", "/projects/{id}/studies", "/runs", "/runs/b7c79a57cbb4", "/runs/b7c79a57cbb4/steps", "/setup", "/status"]);
  // PUT /context goes out 150 ms after the session starts (state/context.ts debounce)
  await expect.poll(async () => (await fake.calls()).filter((c) => c.path === "/context").length).toBeGreaterThanOrEqual(1);
  const ctx = (await fake.calls()).filter((c) => c.path === "/context");
  expect(ctx.every((c) => c.method === "PUT")).toBe(true);
  expect(errors).toEqual([]);
});

// ---- title strips at the window top: text sits on the traffic lights' centre line.
// --chrome-center-y is the lights' vertical centre measured in a built Mises.app (22px: trafficLightPosition centres the lights in the 44px top bar).
async function chromeOffsets(page: Page, selectors: string[]): Promise<{ centre: number; off: [string, number][] }> {
  return page.evaluate((sels) => {
    const centre = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--chrome-center-y"));
    const off: [string, number][] = [];
    for (const sel of sels) {
      const els = [...document.querySelectorAll<HTMLElement>(sel)].filter((el) => el.getBoundingClientRect().height > 0);
      if (els.length === 0) off.push([`${sel} (missing)`, 999]);
      for (const el of els) {
        const r = el.getBoundingClientRect();
        off.push([sel, Math.abs(r.top + r.height / 2 - centre)]);
      }
    }
    return { centre, off };
  }, selectors);
}

for (const theme of ["dark", "light"] as const) {
  test(`header_text_is_centred_on_the_traffic_lights_${theme}`, async ({ page, fake }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
    const top = await chromeOffsets(page, [".build-name", ".tab", ".profile-btn", ".status-btn"]);
    expect(top.centre).toBe(22);
    for (const [sel, d] of top.off) expect(d, `top bar ${sel}`).toBeLessThanOrEqual(2);
    // the setup wizard header
    await fake.reset("setup");
    await page.goto("/");
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await expect(page.getByRole("dialog", { name: "Set up Mises" })).toBeVisible();
    const wiz = await chromeOffsets(page, [".setup-title", ".setup-step-name", ".setup-close"]);
    for (const [sel, d] of wiz.off) expect(d, `wizard ${sel}`).toBeLessThanOrEqual(2);
    // and the 78px inset still keeps text clear of the lights
    const left = await page.locator(".setup-title").evaluate((el) => el.getBoundingClientRect().left);
    expect(left).toBeGreaterThanOrEqual(78);
  });
}
