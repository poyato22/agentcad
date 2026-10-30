// @vitest-environment node
// The untrusted-text ESLint ban (eslint.config.js) catches every HTML sink, including the
// computed and indirect spellings.
import { ESLint } from "eslint";
import { beforeAll, describe, expect, it } from "vitest";

const eslint = new ESLint({ cwd: new URL("..", import.meta.url).pathname });

async function banned(code: string, file = "src/x.tsx"): Promise<boolean> {
  const [res] = await eslint.lintText(code, { filePath: file });
  return res.messages.some((m) => m.ruleId === "no-restricted-syntax");
}

describe("no-html-injection lint rules", () => {
  // m14: ESLint starts cold (config, parser) on the first lint: warm it once with room to spare,
  // so the first case doesn't time out at 5 s on a busy machine
  beforeAll(async () => { await banned("export {};"); }, 60_000);

  it.each([
    ["el.innerHTML = s;"],
    ["el.outerHTML = s;"],
    ['el["innerHTML"] = s;'],
    ["el[`outerHTML`] = s;"],
    ["el.insertAdjacentHTML('beforeend', s);"],
    ["document.write(s);"],
    ["iframe.srcdoc = s;"],
    ['iframe["srcdoc"] = s;'],
    ["new DOMParser().parseFromString(s, 'text/html');"],
    ["document.createRange().createContextualFragment(s);"],
    ["el.setHTMLUnsafe(s);"],
    ["Document.parseHTMLUnsafe(s);"],
    ["const x = <iframe srcdoc={s} />;"],
    ["const x = <div dangerouslySetInnerHTML={{ __html: s }} />;"],
  ])("bans %s", async (code) => {
    expect(await banned(`declare const el: HTMLElement; declare const iframe: HTMLIFrameElement; declare const s: string;\n${code}\nexport {};`)).toBe(true);
  }, 20_000);

  it("allows textContent", async () => {
    expect(await banned("declare const el: HTMLElement; el.textContent = 'x'; export {};")).toBe(false);
  });
});
