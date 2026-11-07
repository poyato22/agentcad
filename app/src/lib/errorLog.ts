// The one hook that sends every error the user can see to the core's error log
// (<MISES_HOME>/logs/errors.jsonl). The app's convention for red text is role="alert",
// [data-error] (error cards, failed toasts) or .cv-fail (failed tool steps): one DOM observer
// reports each such element once when it appears and again only if its text changes, so no
// component calls a logger by hand. Elements may refine the report with data attributes:
// data-error-source, data-error-code, data-run-id, data-error-tool, and child
// [data-error-message] / [data-error-hint]. Uncaught exceptions and unhandled rejections are
// reported with their stack. The core scrubs secrets and dedupes repeats; anything inside
// [data-no-error-log] (the Error log view itself) is skipped.
import type { ErrorReport, ErrorSource } from "../api/errorLog";
import { activeBuildId } from "../state/builds";
import { actions } from "../state/store";

export const ERROR_SELECTOR = '[role="alert"], [data-error], .cv-fail';
const MAX_TEXT = 4000;
const MAX_QUEUE = 50;

type Sink = (e: ErrorReport) => Promise<void>;
let sink: Sink = (e) => actions.client().reportError(e);
let queue: ErrorReport[] = [];
const reported = new WeakMap<Element, string>();

/** Tests: replace where reports go (default: POST /logs/errors). */
export function setErrorSink(s: Sink | null): void {
  sink = s ?? ((e) => actions.client().reportError(e));
  queue = [];
}

function clip(s: string | null | undefined, n = MAX_TEXT): string | null {
  if (!s) return null;
  const t = s.trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
}

/** Report one error. Never throws; reports made while the core is unreachable wait in a small
 *  queue and go out with the next one that gets through. */
export function reportError(e: ErrorReport): void {
  const r: ErrorReport = {
    ...e, message: clip(e.message) ?? "", hint: clip(e.hint), stack: clip(e.stack, 20000),
    build_id: e.build_id ?? activeBuildId.peek() ?? null,
  };
  if (!r.message) return;
  const send = (x: ErrorReport): Promise<boolean> => {
    try {
      return sink(x).then(() => true, () => false);
    } catch {
      return Promise.resolve(false);
    }
  };
  void send(r).then((ok) => {
    if (!ok) {
      queue.push(r);
      if (queue.length > MAX_QUEUE) queue.shift();
      return;
    }
    const pending = queue;
    queue = [];
    for (const p of pending) void send(p);
  });
}

function sourceOf(el: Element): ErrorSource {
  const s = el.getAttribute("data-error-source");
  if (s === "app" || s === "core" || s === "agent" || s === "tool" || s === "study") return s;
  if (el.closest("[data-activity]")) return "tool";
  if (el.closest('[data-kind="error"]')) return "agent";
  if (el.closest(".study-card, [data-study], .studies-page")) return "study";
  return "app";
}

function describe(el: Element): string {
  const cls = (el.getAttribute("class") || "").split(/\s+/).filter(Boolean).slice(0, 2).join(".");
  return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ""}`;
}

/** The report for an error element, or null when it is not one to log. */
export function errorFromElement(el: Element): ErrorReport | null {
  if (el.closest('[aria-hidden="true"], [data-no-error-log]')) return null;
  if (el.parentElement?.closest(ERROR_SELECTOR)) return null;   // the outermost error element speaks
  const message = clip(el.querySelector("[data-error-message]")?.textContent ?? el.textContent);
  if (!message) return null;
  const hintEl = el.querySelector("[data-error-hint]");
  const label = el.getAttribute("aria-label");
  return {
    source: sourceOf(el),
    message: label && !message.includes(label) ? `${label}: ${message}` : message,
    code: el.getAttribute("data-error-code") || el.querySelector(".cv-err-code")?.textContent || null,
    hint: clip(hintEl?.textContent),
    run_id: el.getAttribute("data-run-id"),
    tool: el.getAttribute("data-error-tool"),
    where: describe(el),
  };
}

function scan(root: Element): void {
  const els = root.matches(ERROR_SELECTOR) ? [root] : [];
  els.push(...Array.from(root.querySelectorAll(ERROR_SELECTOR)));
  for (const el of els) {
    const r = errorFromElement(el);
    if (!r) continue;
    const key = `${r.code ?? ""}␟${r.message}`;
    if (reported.get(el) === key) continue;
    reported.set(el, key);
    reportError(r);
  }
}

/** Starts the observer (and the uncaught-exception listeners); returns the stop function. */
export function watchErrors(root: Element = document.body): () => void {
  scan(root);
  const mo = new MutationObserver((muts) => {
    const roots = new Set<Element>();
    for (const m of muts) {
      const t = m.target instanceof Element ? m.target : m.target.parentElement;
      if (!t) continue;
      if (m.type === "childList") {
        m.addedNodes.forEach((n) => { if (n instanceof Element) roots.add(n); });
        const host = t.closest(ERROR_SELECTOR);
        if (host) roots.add(host);
      } else {
        roots.add(t.closest(ERROR_SELECTOR) ?? t);
      }
    }
    for (const r of roots) if (r.isConnected) scan(r);
  });
  mo.observe(root, { childList: true, subtree: true, characterData: true, attributes: true,
                     attributeFilter: ["role", "data-error", "class"] });
  const onError = (ev: ErrorEvent): void => {
    const err = ev.error as { message?: string; stack?: string } | undefined;
    reportError({ source: "app", code: "uncaught", message: err?.message || ev.message || "uncaught error",
                  stack: err?.stack ?? (ev.filename ? `${ev.filename}:${ev.lineno}:${ev.colno}` : null) });
  };
  const onRejection = (ev: PromiseRejectionEvent): void => {
    const r = ev.reason as { message?: string; stack?: string; code?: string } | string | undefined;
    const message = typeof r === "string" ? r : r?.message || String(r);
    reportError({ source: "app", code: typeof r === "object" && r?.code ? String(r.code) : "unhandled_rejection",
                  message, stack: typeof r === "object" ? r?.stack ?? null : null });
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    mo.disconnect();
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
