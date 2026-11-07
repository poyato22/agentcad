// The usage of one turn (a user message's agent run, sub-agents included), a small line under
// the turn in the conversation: "≈$0.42 · 182k tokens". A click opens the breakdown by agent and
// model; the hover says what the dollar figure is (an API-price equivalent, not a charge).
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { BuildUsage, TurnUsage } from "../../api/usage";
import { fmtTokens, fmtUsd, sourceNote, usageText } from "../../lib/usage";
import "./usage.css";

function who(a: TurnUsage["agents"][number]): string {
  return a.title || a.agent || a.run_id;
}

export function UsageLine({ turn }: { turn: TurnUsage }): JSX.Element {
  const [open, setOpen] = useState(false);
  const text = usageText(turn);
  return (
    <div class="usage-line" data-usage-turn>
      <button type="button" class="usage-sum" aria-expanded={open} title={sourceNote(turn.usd_source)}
        aria-label={`Usage of this message: ${text}. ${sourceNote(turn.usd_source)} Show the breakdown`}
        onClick={() => setOpen(!open)}>{text}</button>
      {open && (
        <table class="usage-table" data-usage-breakdown>
          <thead><tr><th>Agent</th><th>Model</th><th>In</th><th>Out</th><th>Cache</th><th>Cost</th></tr></thead>
          <tbody>
            {turn.agents.map((a) => (
              <tr key={`${a.run_id}:${a.model}`}>
                <td>{who(a)}{a.role === "lead" ? " (lead)" : ""}</td>
                <td>{a.model ?? "?"}</td>
                <td class="num">{fmtTokens(a.input_tokens)}</td>
                <td class="num">{fmtTokens(a.output_tokens)}</td>
                <td class="num" title="cache read / cache write">{`${fmtTokens(a.cache_read_tokens)} / ${fmtTokens(a.cache_write_tokens)}`}</td>
                <td class="num">{a.usd_source === "unknown" ? "not reported" : `≈${fmtUsd(a.usd)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** The build's running total (top bar): its recorded turns, plus a note for older runs whose
 *  usage was not recorded per turn. Nothing before the build has any usage. */
export function BuildUsageChip({ usage }: { usage: BuildUsage | null | undefined }): JSX.Element | null {
  const [open, setOpen] = useState(false);
  if (!usage || (!usage.recorded && !usage.unrecorded)) return null;
  const text = usage.recorded ? usageText(usage) : "usage not recorded";
  const older = usage.unrecorded;
  return (
    <div class="usage-chip-wrap">
      <button type="button" class="usage-chip" data-build-usage aria-expanded={open}
        title={`This build's usage. ${sourceNote(usage.usd_source)}`}
        aria-label={`This build's usage: ${text}. Show it by message`} onClick={() => setOpen(!open)}>{text}</button>
      {open && (
        <div class="usage-pop" role="dialog" aria-label="Usage by message">
          <p class="usage-note">{sourceNote(usage.usd_source)}</p>
          <ol class="usage-turns">
            {usage.turns.map((t, i) => (
              <li key={`${t.root_id}:${t.turn_ts}`}>
                <span>{`Message ${i + 1} · ${new Date(t.turn_ts * 1000).toLocaleString()}`}</span>
                <span class="num">{usageText(t)}</span>
              </li>
            ))}
          </ol>
          {older && (
            <p class="usage-note" data-unrecorded>
              {`Older runs (${older.runs}): not recorded per message. Their totals: ${fmtTokens(older.tokens)} tokens${older.usd > 0 ? `, ≈${fmtUsd(older.usd)}` : ""}.`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
