// The build view's floating pill (Plan 9 D12 / D17), bottom-centre over the assembly:
//   ▶ Play     full-window view of the assembly (the agent's latest animation, else an exploded tour)
//   ◧ Section  one click cuts the assembly; the slider sweeps the plane through its box, X / Y / Z
//              pick the plane, flip looks from the other side (the drag handle on the model too)
//   X-ray · Explode · Laid out (Plan 10 E36: every part flat in a grid, named)
//   Measure · Comment (Plan 10 E16 / E35: click tools on the assembly; pressed again, Select)
// and, only when the agents' check found something, a small status chip (interference, a joint that
// can't apply). The modes are the part viewer's (ui/viewer/modes) on the instances' poses.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import * as THREE from "three";
import { viewInsets } from "../shell/insets";
import type { ModeHost } from "../viewer/scene";
import { restBox, type ModesController } from "../viewer/modes/controller";
import { sectionAt, sectionRange, unit } from "../viewer/modes/section";
import { SectionHandle } from "../viewer/modes/SectionHandle";
import { MODES_OFF, XRAY_OPACITY, type ModesState, type Vec3 } from "../viewer/modes/state";
import { fmtVolume } from "./format";

const AXES: { key: string; normal: Vec3 }[] = [{ key: "X", normal: [1, 0, 0] }, { key: "Y", normal: [0, 1, 0] }, { key: "Z", normal: [0, 0, 1] }];

function axisOf(n: Vec3): string | null {
  const u = unit(n);
  for (const a of AXES) if (Math.abs(Math.abs(u.dot(new THREE.Vector3(...a.normal))) - 1) < 1e-6) return a.key;
  return null;
}

/** The plane's normal, signed so the kept side faces the camera: you look straight at the cut
 *  (review I1). The clip keeps n·p <= at, and the cap is seen from +n. */
export function towardCamera(host: ModeHost | null, normal: Vec3): Vec3 {
  if (!host) return normal;
  const look = host.camera.position.clone().sub(host.controls.target);
  return unit(normal).dot(look) >= 0 ? normal : [-normal[0], -normal[1], -normal[2]];
}

export interface CheckIssue { tone: "fail" | "warn"; text: string }

/** What the check chip says: clashes first (fail), then joints that can't apply (warn). */
export function checkIssues(items: readonly { a: string; b: string; volume_mm3: number; intended?: string | null }[], overconstrained: readonly (readonly string[])[]): CheckIssue[] {
  return [
    // G2: a declared contact (press fit, bearing seat) is the design, not an issue
    ...items.filter((x) => !x.intended).map((x) => ({ tone: "fail" as const, text: `${x.a} and ${x.b} overlap by ${fmtVolume(x.volume_mm3)}` })),
    ...overconstrained.map((g) => ({ tone: "warn" as const, text: `${g.join(" and ")} are grounded and joined: one joint can't apply` })),
  ];
}

/** P10 FWA: when the pill is wider than the free area (the build view less the floating surfaces),
 *  these move into its "More" menu, first to last; Play, Section, Explode, the clear button and
 *  the check chip always stay. */
export const FOLD_ORDER = ["comment", "measure", "layout", "xray"] as const;
export type FoldKey = (typeof FOLD_ORDER)[number];
/** The pill's flex gap; the More button's width before it was ever shown. */
const PILL_GAP = 2;
const MORE_W = 64;

/** How many of `items` (present ones, in FOLD_ORDER, with their widths) go into More so the pill
 *  fits `avail` px: `fixed` is what always shows (padding and border included), `sepW` the
 *  separator before Measure / Comment (shown while either is), `moreW` the More button. All of
 *  them when nothing fits (the pill then wraps to a second row). Pure. */
export function pillFold(avail: number, fixed: number, items: readonly { key: FoldKey; w: number }[], sepW: number, moreW = MORE_W): number {
  for (let k = 0; k <= items.length; k++) {
    const shown = items.slice(k);
    const tools = shown.some((i) => i.key === "measure" || i.key === "comment");
    const need = fixed + shown.reduce((t, i) => t + i.w + PILL_GAP, 0) + (k > 0 ? moreW + PILL_GAP : 0) + (tools ? sepW + PILL_GAP : 0);
    if (need <= avail) return k;
  }
  return items.length;
}

/** An element's width with its side margins. */
function outerW(el: Element): number {
  const cs = getComputedStyle(el);
  return el.getBoundingClientRect().width + (parseFloat(cs.marginLeft) || 0) + (parseFloat(cs.marginRight) || 0);
}

export interface ViewerPillProps {
  host: ModeHost | null;
  /** The build view's modes controller (it outlives the pill: Play's view keeps the explode). */
  modesCtl: ModesController | null;
  modes: ModesState;
  onModes(next: ModesState): void;
  /** Set true before a change that should glide (the explode). */
  animate: { current: boolean };
  issues: readonly CheckIssue[];
  onPlay(): void;
  canPlay: boolean;
  /** The click tool on (Select when absent). */
  tool?: "select" | "measure" | "comment";
  onTool?(t: "select" | "measure" | "comment"): void;
  /** Plan 10 E5: the build's saved animations (oldest first); more than one: a menu beside Play. */
  animations?: readonly string[];
  onPlayAnimation?(i: number): void;
}

export function ViewerPill({ host, modesCtl: ctl, modes, onModes, animate, issues, onPlay, canPlay, tool = "select", onTool, animations = [], onPlayAnimation }: ViewerPillProps): JSX.Element {
  const [animMenu, setAnimMenu] = useState(false);
  const menuRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!animMenu) return;
    menuRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    const away = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setAnimMenu(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [animMenu]);
  const set = (patch: Partial<ModesState>, glide = false) => { animate.current = glide; onModes({ ...modes, ...patch }); };
  const sec = modes.section;
  const xrayOn = modes.xray.opacity < 0.999;
  const exploding = modes.explode > 0;
  const laid = (modes.layout ?? 0) > 0;
  const canExplode = (ctl?.exploder.count ?? 0) >= 2;
  const box = host ? restBox(host.parts()) : null;
  const n = unit(sec.normal);
  const range = box ? sectionRange(box, n) : [0, 0];
  const at = sectionAt(sec, box);
  const step = Math.max((range[1] - range[0]) / 200, 0.01);
  const fails = issues.filter((i) => i.tone === "fail").length;
  const chipRef = useRef<HTMLSpanElement>(null);

  // P10 FWA: the buttons that fold into More when the free area is narrower than the pill
  const foldable: { key: FoldKey; label: string; pressed: boolean; disabled: boolean; title: string; testid: string; run(): void }[] = [
    { key: "xray", label: "X-ray", pressed: xrayOn, disabled: false, title: "See through the parts to what is inside", testid: "pill-xray",
      run: () => set({ xray: { ...modes.xray, opacity: xrayOn ? 1 : XRAY_OPACITY } }) },
    { key: "layout", label: "Laid out", pressed: laid, disabled: !canExplode && !laid, testid: "pill-layout",
      title: canExplode || laid ? "Lay every part out flat in a grid, largest first, each named" : "Laid out needs two or more parts",
      run: () => set({ layout: laid ? 0 : 1, explode: 0 }, true) },
    ...(onTool ? [
      { key: "measure" as const, label: "Measure", pressed: tool === "measure", disabled: false, title: "Measure between two spots on the parts", testid: "pill-measure",
        run: () => onTool(tool === "measure" ? "select" : "measure") },
      { key: "comment" as const, label: "Comment", pressed: tool === "comment", disabled: false, title: "Pin a comment on a part for the agent", testid: "pill-comment",
        run: () => onTool(tool === "comment" ? "select" : "comment") },
    ] : []),
  ];
  const present = FOLD_ORDER.filter((k) => foldable.some((f) => f.key === k));
  const [fold, setFold] = useState(0);
  const folded = new Set<FoldKey>(present.slice(0, fold));
  const item = (k: FoldKey) => foldable.find((f) => f.key === k);
  const inline = (k: FoldKey) => { const f = item(k); return f && !folded.has(k) ? f : null; };
  const pillRef = useRef<HTMLDivElement>(null);
  const widths = useRef(new Map<string, number>());
  const [, setTick] = useState(0);
  void viewInsets.value;                                   // the surfaces moved: measure again
  useLayoutEffect(() => {
    const el = pillRef.current;
    const host = el?.parentElement;
    if (!el || !host || host.clientWidth === 0) return;
    const cs = getComputedStyle(host);
    const avail = host.clientWidth - (parseFloat(cs.getPropertyValue("--inset-l")) || 0) - (parseFloat(cs.getPropertyValue("--inset-r")) || 0) - 32;
    const pcs = getComputedStyle(el);
    let fixed = (parseFloat(pcs.paddingLeft) || 0) + (parseFloat(pcs.paddingRight) || 0) + (parseFloat(pcs.borderLeftWidth) || 0) + (parseFloat(pcs.borderRightWidth) || 0);
    let n = 0;
    for (const c of [...el.children]) {
      const h = c as HTMLElement;
      const w = outerW(h);
      if (h.dataset.ov) widths.current.set(h.dataset.ov, w);
      else if (h.dataset.ovSep !== undefined) widths.current.set("sep", w);
      else if (h.dataset.ovMore !== undefined) widths.current.set("more", w);
      else { fixed += w + (n > 0 ? PILL_GAP : 0); n++; }
    }
    const next = pillFold(avail, fixed, present.map((key) => ({ key, w: widths.current.get(key) ?? 80 })), widths.current.get("sep") ?? 9, widths.current.get("more"));
    if (next !== fold) setFold(next);
  });
  useEffect(() => {
    const host = pillRef.current?.parentElement;
    if (!host || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setTick((t) => t + 1));
    ro.observe(host);
    return () => ro.disconnect();
  }, []);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!moreOpen) return;
    moreRef.current?.querySelector<HTMLElement>("[role=menuitemcheckbox]:not([disabled])")?.focus();
    // the More button sits inside moreRef: its own click closes the menu (E7)
    const away = (e: MouseEvent) => { if (moreRef.current && !moreRef.current.contains(e.target as Node)) setMoreOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [moreOpen]);
  useEffect(() => { if (fold === 0) setMoreOpen(false); }, [fold]);
  const moreItems = present.filter((k) => folded.has(k)).map(item).filter((f): f is NonNullable<typeof f> => !!f);
  const btn = (f: NonNullable<ReturnType<typeof item>>) => (
    <button key={f.key} type="button" class="pill-btn" aria-pressed={f.pressed} data-testid={f.testid} data-ov={f.key} disabled={f.disabled}
      title={f.title} onClick={f.run}>{f.label}</button>
  );
  const xray = inline("xray");
  const layout = inline("layout");
  const measure = inline("measure");
  const comment = inline("comment");

  return (
    <>
      {sec.on && host && <SectionHandle host={host} section={sec} onAt={(v) => set({ section: { ...sec, at: v } })} />}
      <div class="asm-pill" ref={pillRef} role="toolbar" aria-label="View" data-testid="viewer-pill" data-folded={fold || undefined}>
        <button type="button" class="pill-btn pill-play" data-testid="pill-play" disabled={!canPlay}
          title="Watch the assembly full-window: the agent's latest animation, else an exploded tour" onClick={onPlay}>
          <span aria-hidden="true">▶</span> Play
        </button>
        {animations.length > 1 && onPlayAnimation && (
          <span class="pill-anim" ref={menuRef} onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setAnimMenu(false); }
            const items = [...(menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
            const i = items.indexOf(document.activeElement as HTMLElement);
            if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
            if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
          }}>
            <button type="button" class="pill-btn pill-more" data-testid="pill-animations" aria-haspopup="menu" aria-expanded={animMenu}
              aria-label="The build's saved animations" title="Pick a saved animation" onClick={() => setAnimMenu(!animMenu)}>▾</button>
            {animMenu && (
              <span class="pill-menu" role="menu" aria-label="Saved animations" data-testid="animation-menu">
                {[...animations].reverse().map((name, k) => {
                  const i = animations.length - 1 - k;
                  return (
                    <button key={i} type="button" role="menuitem" class="pill-menu-item" onClick={() => { setAnimMenu(false); onPlayAnimation(i); }}>
                      {name}{k === 0 ? <span class="dim"> · newest</span> : null}
                    </button>
                  );
                })}
              </span>
            )}
          </span>
        )}
        <span class="pill-sep" aria-hidden="true" />
        <button type="button" class="pill-btn" aria-pressed={sec.on} data-testid="pill-section" disabled={!box}
          title="Cut the assembly with a plane" onClick={() => set({ section: sec.on ? { ...sec, on: false } : { on: true, normal: towardCamera(host, sec.normal), at: null } })}>
          <span aria-hidden="true">◧</span> Section
        </button>
        {sec.on && (
          <span class="pill-section" data-testid="pill-section-controls">
            <span class="pill-axes" role="group" aria-label="Section plane normal">
              {AXES.map((a) => (
                <button key={a.key} type="button" class="pill-axis" aria-pressed={axisOf(sec.normal) === a.key}
                  onClick={() => set({ section: { on: true, normal: towardCamera(host, a.normal), at: null } })}>{a.key}</button>
              ))}
              <button type="button" class="pill-axis" title="Look at the cut from the other side" aria-label="Flip the section"
                onClick={() => set({ section: { on: true, normal: [-sec.normal[0], -sec.normal[1], -sec.normal[2]], at: -at } })}>⇄</button>
            </span>
            <input type="range" class="pill-range" min={range[0]} max={range[1]} step={step} value={at}
              aria-label="Section plane position" data-testid="section-slider"
              onInput={(e) => set({ section: { ...sec, at: Number(e.currentTarget.value) } })} />
            <span class="pill-val num" data-testid="section-value">{`${at.toFixed(1)} mm`}</span>
          </span>
        )}
        {xray && btn(xray)}
        <button type="button" class="pill-btn" aria-pressed={exploding} data-testid="pill-explode" disabled={!canExplode && !exploding}
          title={canExplode || exploding ? "Move the parts apart" : "Explode needs two or more parts"}
          onClick={() => set({ explode: exploding ? 0 : 1, layout: 0 }, true)}>Explode</button>
        {layout && btn(layout)}
        {(measure || comment) && <span class="pill-sep" aria-hidden="true" data-ov-sep="" />}
        {measure && btn(measure)}
        {comment && btn(comment)}
        {moreItems.length > 0 && (
          <span class="pill-anim pill-overflow" ref={moreRef} data-ov-more="" onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setMoreOpen(false); moreRef.current?.querySelector<HTMLElement>("[data-testid=pill-overflow]")?.focus(); }
            const items = [...(moreRef.current?.querySelectorAll<HTMLElement>("[role=menuitemcheckbox]:not([disabled])") ?? [])];
            const i = items.indexOf(document.activeElement as HTMLElement);
            if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
            if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
          }}>
            <button type="button" class="pill-btn" data-testid="pill-overflow" aria-haspopup="menu" aria-expanded={moreOpen}
              aria-pressed={moreItems.some((f) => f.pressed)} title={`More: ${moreItems.map((f) => f.label).join(", ")}`}
              onClick={() => setMoreOpen(!moreOpen)}>More <span aria-hidden="true">▾</span></button>
            {moreOpen && (
              <span class="pill-menu pill-menu-end" role="menu" aria-label="More view tools" data-testid="pill-overflow-menu">
                {moreItems.map((f) => (
                  <button key={f.key} type="button" role="menuitemcheckbox" class="pill-menu-item" aria-checked={f.pressed} disabled={f.disabled}
                    title={f.title} data-testid={`${f.testid}-more`} onClick={() => { setMoreOpen(false); f.run(); }}>{f.label}</button>
                ))}
              </span>
            )}
          </span>
        )}
        {(xrayOn || sec.on || exploding || laid) && (
          <button type="button" class="pill-btn pill-clear" title="Turn every view mode off" aria-label="Clear the view modes"
            onClick={() => { animate.current = modes.explode > 0 || laid; onModes(MODES_OFF); }}>✕</button>
        )}
        {issues.length > 0 && (
          <span ref={chipRef} class="pill-chip" data-testid="assembly-check-chip" data-status={fails ? "fail" : "warn"} tabIndex={0}
            title={issues.map((i) => i.text).join("\n")} aria-label={`Check: ${issues.map((i) => i.text).join("; ")}`}>
            <span class={`led ${fails ? "led-red" : "led-amber"}`} aria-hidden="true" />
            {fails ? `${fails} clash${fails === 1 ? "" : "es"}` : `${issues.length} warning${issues.length === 1 ? "" : "s"}`}
          </span>
        )}
      </div>
    </>
  );
}
