// One render-and-critique round in the thread (R18: compact, expands on click). Collapsed: the
// before / after renders side by side, the round, the score against its bar and how many issues
// were fixed. Expanded: each fixed issue (view, severity, what was wrong and the fix), what is
// still open, and the silhouette match per view when a reference was given. All text is the
// critique model's output, shown as text.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { Led, type LedTone } from "../../../common/Led";
import type { CritiqueIssue, CritiqueRound, ImageRef } from "./announce";
import { useImageUrl } from "./images";
import "./critique.css";

const STOP_TEXT: Record<string, string> = {
  passed: "stopped: the score passed", score_passed: "stopped: the score passed", score_threshold: "stopped: the score passed",
  no_high_issues: "stopped: nothing serious left", no_high_severity: "stopped: nothing serious left", max_rounds: "stopped: round limit",
  image_budget: "stopped: image budget",
  budget: "stopped: time or cost budget", time_budget: "stopped: time budget", cost_budget: "stopped: cost budget",
  unavailable: "no vision model to critique with",
};

export function scoreTone(r: Pick<CritiqueRound, "score" | "threshold" | "open">): LedTone {
  if (r.score === null) return r.open.some((i) => i.severity === "high") ? "amber" : "faint";
  if (r.threshold !== null) return r.score >= r.threshold ? "green" : "amber";
  return r.open.some((i) => i.severity === "high") ? "amber" : "green";
}

function Shot({ image, label }: { image: ImageRef | null; label: string }): JSX.Element {
  const url = useImageUrl(image);
  return (
    <figure class="crit-shot" data-has={url ? "image" : "none"}>
      {url ? <img src={url} alt={label} width={72} height={48} /> : <span class="crit-shot-empty" aria-hidden="true" />}
      <figcaption class="label">{label}</figcaption>
    </figure>
  );
}

function Issue({ i, fixed }: { i: CritiqueIssue; fixed: boolean }): JSX.Element {
  return (
    <li class="crit-issue" data-fixed={fixed ? "" : undefined} data-severity={i.severity ?? "none"}>
      <span class={`crit-mark${fixed ? " tone-green" : i.severity === "high" ? " tone-amber" : " faint"}`} aria-hidden="true">{fixed ? "✓" : "·"}</span>
      <span class="crit-issue-body">
        <span class="crit-issue-head">
          {i.view && <span class="crit-view">{i.view}</span>}
          {i.severity && <span class="crit-sev" data-sev={i.severity}>{i.severity}</span>}
        </span>
        <span class="text">{i.issue}</span>
        {i.fix && <span class="text faint">{fixed ? `fixed: ${i.fix}` : `try: ${i.fix}`}</span>}
      </span>
    </li>
  );
}

export function CritiqueCard({ round: r }: { round: CritiqueRound }): JSX.Element {
  const [open, setOpen] = useState(false);
  const tone = scoreTone(r);
  const roundText = r.round !== null ? `Round ${r.round}${r.maxRounds !== null ? ` of ${r.maxRounds}` : ""}` : "Critique";
  const score = r.score !== null ? `score ${r.score.toFixed(2)}${r.threshold !== null ? ` / ${r.threshold.toFixed(2)}` : ""}` : null;
  const counts = [`${r.fixed.length} fixed`, r.open.length ? `${r.open.length} open` : null].filter(Boolean).join(" · ");
  // the core's own title / detail ("Critique round 1/3: …", "2 fixed · 1 open …") repeat what the
  // card already shows; anything else (a summary of the round) is shown
  const title = /^critique round \d/i.test(r.title) ? "" : r.title;
  const detail = /^\d+ fixed\b/i.test(r.detail) ? "" : r.detail;
  const canOpen = r.fixed.length > 0 || r.open.length > 0 || r.iou.length > 0 || detail !== "";
  return (
    <article class="crit" data-testid="critique-card" data-round={r.round ?? ""} data-open={open ? "true" : "false"} aria-label={`Critique ${roundText.toLowerCase()}`}>
      <button type="button" class="crit-sum" aria-expanded={open} disabled={!canOpen} onClick={() => setOpen(!open)}>
        <span class="crit-shots">
          <Shot image={r.before} label="before" />
          <span class="crit-arrow faint" aria-hidden="true">→</span>
          <Shot image={r.after} label="after" />
        </span>
        <span class="crit-main">
          <span class="crit-title">{`Critique · ${roundText}`}</span>
          <span class="crit-line">
            <Led tone={tone} />
            {score && <span class={`crit-score tone-${tone}`} data-testid="critique-score">{score}</span>}
            <span class="dim">{counts}</span>
          </span>
          {title && <span class="crit-what text dim">{title}</span>}
        </span>
        {canOpen && <span class="crit-x faint" aria-hidden="true">{open ? "▾" : "▸"}</span>}
      </button>
      {open && (
        <div class="crit-more" data-testid="critique-details">
          {detail && <p class="text crit-detail">{detail}</p>}
          {r.fixed.length > 0 && <ul class="crit-issues" aria-label="Fixed this round">{r.fixed.map((i, k) => <Issue key={k} i={i} fixed />)}</ul>}
          {r.open.length > 0 && <ul class="crit-issues" aria-label="Still open">{r.open.map((i, k) => <Issue key={k} i={i} fixed={false} />)}</ul>}
          {r.iou.length > 0 && (
            <dl class="crit-iou" aria-label="Silhouette match against the reference">
              {r.iou.map((v) => (
                <div key={v.view}><dt class="label">{v.view}</dt><dd><span class="crit-bar"><i style={{ width: `${Math.round(v.value * 100)}%` }} /></span>{v.value.toFixed(2)}</dd></div>
              ))}
            </dl>
          )}
          {r.stopReason && <p class="faint crit-stop">{STOP_TEXT[r.stopReason] ?? `stopped: ${r.stopReason}`}</p>}
        </div>
      )}
    </article>
  );
}
