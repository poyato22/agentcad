// The Studies page's agent pane (Plan 9 U-D, D19): the studies agent's opening proposals (the next
// 1–3 studies with why, each with a Run button that starts the study through POST /sims — the
// same Gate path as the manual form), the study that Run started as a card, the agent's
// conversation and a composer. Questions and approvals stay in the agent dock (one place answers
// them): the pane shows that one is waiting and opens it. Every text from the core (labels, why,
// replies) renders as text or Mises markup, never as HTML.
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { StudyProposal, StudiesContext } from "../../../../api/studiesAgent";
import type { StudyRecord } from "../../../../api/studies";
import type { AgentRun } from "../../../../api/types";
import { partName } from "../../../../lib/names";
import { approvals, inflight, liveText, parts, questions, runs, steps } from "../../../../state/store";
import { Button } from "../../../common/Button";
import { Led } from "../../../common/Led";
import { PhaseBar } from "../../../console/studycard/StudyCard";
import { buildLog } from "../../../console/logModel";
import { buildThread, type ThreadItem } from "../../../console/threadModel";
import { Markup } from "../../../markup/render";
import type { ActiveStudy } from "../controller";
import { fmtStudyMetric, honesty, metricLabel, phaseText, summaryMetrics, TYPE_NAMES, verdictTone } from "../model";
import { isBusy, PLAN_PROMPT, typeWord, type StudiesAgentController } from "./model";
import "./agent.css";

export interface AgentPaneProps {
  agent: StudiesAgentController;
  run: AgentRun | null;
  /** Why a proposal cannot run now (no solver, part not built), or null. */
  blocked: (p: StudyProposal) => string | null;
  onRun: (p: StudyProposal) => void;
  /** The proposal whose own study is running now (its Run is the only one disabled). */
  runningId: string | null;
  /** The proposal queued to run once the running study ends. */
  queuedId: string | null;
  /** The study Run started (or the page's own running study). */
  active: ActiveStudy | null;
  /** The finished study the last Run produced, once the page has loaded it. */
  ranRecord: StudyRecord | null;
  onCancel: () => void;
  onWatch: () => void;
  onOpenResult: (simId: string) => void;
  /** Opens the run in the agent dock (questions, approvals, the full log). */
  onOpenRun: (runId: string) => void;
}

/** A part's own name (its label, else its id with spaces), as the tree shows it. */
const nameOf = (id: string): string => partName(parts.value.find((x) => x.part_id === id) ?? { part_id: id });

const FILTER = { scope: "run", kind: "everything" } as const;
const NO_FOLDS: ReadonlySet<string> = new Set();

function useThread(runId: string | null): ThreadItem[] {
  const run = runId ? runs.value.get(runId) : undefined;
  const st = steps.value, ap = approvals.value, qs = questions.value, lt = liveText.value, inf = inflight.value;
  return useMemo(() => (runId ? buildThread(buildLog({
    runs: run ? [run] : [], steps: st, approvals: [...ap.values()], questions: [...qs.values()],
    liveText: lt, inflight: inf, filter: FILTER, selectedRunId: runId, expandedFolds: NO_FOLDS,
  })) : []), [runId, run, st, ap, qs, lt, inf]);
}

export function AgentPane(p: AgentPaneProps): JSX.Element {
  const ctx = p.agent.context.value;
  const runId = p.run?.id ?? null;
  const busy = isBusy(p.run);
  const thread = useThread(runId);
  const scroller = useRef<HTMLDivElement>(null);
  const shown = thread.length;
  useEffect(() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; }, [shown]);
  // the study a Run started sits right under its proposal (M7); once the proposal retired (its
  // study is done), under the proposals
  const ranId = p.agent.ranProposal.value?.id ?? null;
  const cardUnderProposal = ranId !== null && (ctx?.proposals.some((x) => x.id === ranId) ?? false);
  const card = <RanCard active={p.active} rec={p.ranRecord} onCancel={p.onCancel} onWatch={p.onWatch} onOpen={p.onOpenResult} />;

  return (
    <section class="sa" data-testid="studies-agent" aria-label="Studies agent">
      <header class="sa-head">
        <h2 class="sa-title">Studies agent</h2>
        <span class="sa-state" data-testid="studies-agent-state" data-busy={String(busy)}>
          <Led tone={busy ? "blue" : "faint"} />{busy ? "Working" : p.run ? "Ready" : "Knows this build"}
        </span>
        {runId && <button type="button" class="sa-link" onClick={() => p.onOpenRun(runId)} data-testid="studies-agent-open">Open in dock</button>}
      </header>
      <div class="sa-body" ref={scroller}>
        <Opening ctx={ctx} error={p.agent.contextError.value} loading={p.agent.loading.value} blocked={p.blocked} onRun={p.onRun}
          ranId={ranId} ranCard={cardUnderProposal ? card : null} runningId={p.runningId} queuedId={p.queuedId} anyRunning={p.active !== null} />
        {!cardUnderProposal && card}
        {thread.map((it) => <Item key={it.key} item={it} onOpenRun={() => runId && p.onOpenRun(runId)} />)}
      </div>
      <Composer agent={p.agent} run={p.run} busy={busy} />
    </section>
  );
}

function Opening({ ctx, error, loading, blocked, onRun, ranId, ranCard, runningId, queuedId, anyRunning }: {
  ctx: StudiesContext | null; error: string | null; loading: boolean; blocked: AgentPaneProps["blocked"]; onRun: AgentPaneProps["onRun"]; ranId: string | null;
  ranCard: JSX.Element | null; runningId: string | null; queuedId: string | null; anyRunning: boolean;
}): JSX.Element {
  if (error) return <p class="sa-msg tone-red text" data-testid="studies-agent-error" role="alert">Could not read the build: {error}</p>;
  if (!ctx) return <p class="sa-msg dim">{loading ? "Reading the build…" : "Open a build to plan its studies."}</p>;
  const built = ctx.parts.filter((x) => x.kind === "part" && x.built).length;
  const props = ctx.proposals;
  const lead = props.length === 0
    ? built === 0 ? "Nothing to study yet: build a part on the Design page first." : "Every part has a current study. Ask me for something specific."
    : `Here is what I would run next${ctx.build_name ? ` on ${ctx.build_name}` : ""}.`;
  return (
    <div class="sa-open" data-testid="studies-agent-opening">
      <p class="sa-lead">{lead}</p>
      <p class="sa-basis dim" data-testid="proposals-source" data-source={ctx.proposals_source}>
        {ctx.proposals_source === "agent" ? "Proposed by the studies agent" : "From the build's parts, spec and past studies"}
        {` · ${built} built part${built === 1 ? "" : "s"} · ${ctx.studies_total} past stud${ctx.studies_total === 1 ? "y" : "ies"}`}
      </p>
      {props.length > 0 && (
        <ol class="sa-props" aria-label="Proposed studies">
          {props.map((x, i) => (
            <ProposalCard key={x.id} p={x} n={i + 1} ctx={ctx} why={blocked(x)} ran={x.id === ranId} onRun={() => onRun(x)}
              running={x.status === "running" || x.id === runningId} queued={x.id === queuedId} anyRunning={anyRunning}>
              {x.id === ranId ? ranCard : null}
            </ProposalCard>
          ))}
        </ol>
      )}
    </div>
  );
}

function ProposalCard({ p, n, ctx, why, ran, onRun, running, queued, anyRunning, children }: {
  p: StudyProposal; n: number; ctx: StudiesContext; why: string | null; ran: boolean; onRun: () => void;
  /** Its own study is running (only then is its Run disabled). */
  running: boolean;
  /** Queued behind the study that runs now. */
  queued: boolean;
  /** Some other study runs: Run queues this one. */
  anyRunning: boolean;
  children?: ComponentChildren;
}): JSX.Element {
  const part = ctx.parts.find((x) => x.part_id === p.part_id);
  const label = running ? "Running…" : queued ? "Queued" : anyRunning ? "Run next" : ran ? "Run again" : "Run";
  const state = running ? "running" : queued ? "queued" : ran ? "ran" : "new";
  const fid = honesty(p.type, null, { settings: p.settings }).fidelity;
  return (
    <li class="sa-prop" data-testid="study-proposal" data-proposal={p.id} data-type={p.type} data-part={p.part_id}>
      <div class="sa-prop-head">
        <span class="sa-prop-n num" aria-hidden="true">{n}</span>
        <span class="sa-prop-title">{p.title}</span>
      </div>
      <p class="sa-prop-why">{p.why}</p>
      <div class="sa-prop-foot">
        <span class="sa-prop-meta dim">{part?.label ?? nameOf(p.part_id)} · {typeWord(p.type)} · {fid}</span>
        <Button variant="ink" class="sa-run" onClick={onRun} disabled={why !== null || running} title={running ? "Its study is running" : queued ? "Runs when the current study ends; press to take it off" : why ?? `Run the ${TYPE_NAMES[p.type].toLowerCase()} study`}
          aria-label={`${label}: ${p.title}`} aria-pressed={queued || undefined} data-testid="run-proposal" data-state={state}>{label}</Button>
      </div>
      {why && !running && <p class="sa-prop-why-not faint" data-testid="proposal-blocked">{why}</p>}
      {children}
    </li>
  );
}

/** The study a proposal's Run started: progress while it runs, then its verdict and numbers. */
function RanCard({ active, rec, onCancel, onWatch, onOpen }: {
  active: ActiveStudy | null; rec: StudyRecord | null; onCancel: () => void; onWatch: () => void; onOpen: (simId: string) => void;
}): JSX.Element | null {
  // a new card (a study started, or its result arrived) scrolls into view inside the pane
  const ref = useRef<HTMLDivElement>(null);
  const key = active ? `run:${active.runId}` : rec ? `rec:${rec.sim_id}:${rec.status}` : "";
  useEffect(() => { if (key) ref.current?.scrollIntoView?.({ block: "nearest" }); }, [key]);
  if (active) {
    return (
      <div class="sa-card" ref={ref} data-testid="agent-study-card" data-status="running" role="status">
        <div class="sa-card-head"><Led tone="faint" /><span class="sa-card-title">{TYPE_NAMES[active.type]} · {nameOf(active.partId)}</span>
          <span class="dim">{active.cancelling ? "cancelling…" : phaseText(active.phase)}</span></div>
        <PhaseBar phase={active.phase} pct={active.pct} detail={active.detail} />
        <div class="sa-card-actions">
          <Button onClick={onWatch} data-testid="agent-study-watch">Watch</Button>
          <Button variant="danger" onClick={onCancel} disabled={active.cancelling} data-testid="agent-study-cancel">Cancel</Button>
        </div>
      </div>
    );
  }
  if (!rec) return null;
  const card = rec.card ?? null;
  const verdict = card?.verdict ?? null;
  const h = honesty(rec.type, card, { settings: rec.settings });
  const nums = card ? summaryMetrics(card.metrics, rec.results).slice(0, 4) : [];
  return (
    <div class="sa-card" ref={ref} data-testid="agent-study-card" data-status={rec.status} data-verdict={verdict ?? ""}>
      <div class="sa-card-head"><Led tone={verdictTone(verdict)} /><span class="sa-card-title">{TYPE_NAMES[(rec.type ?? "static") as keyof typeof TYPE_NAMES] ?? rec.type} · {nameOf(rec.part_id)}</span>
        <span class={`sa-verdict tone-${verdictTone(verdict)}`}>{verdict ?? rec.status}</span></div>
      {nums.length > 0 && (
        <dl class="sa-nums">
          {nums.map((m) => <div key={m.key}><dt class="dim">{metricLabel(m, rec.type)}</dt><dd class="num">{fmtStudyMetric(m, rec.warnings)}</dd></div>)}
        </dl>
      )}
      <p class="sa-card-note dim">Guide only · {h.fidelity}{h.band ? ` · ${h.band}` : ""}</p>
      {rec.error && <p class="tone-red text">{rec.error}</p>}
      <div class="sa-card-actions"><Button onClick={() => onOpen(rec.sim_id)} data-testid="agent-study-open">Show result</Button></div>
    </div>
  );
}

function Item({ item, onOpenRun }: { item: ThreadItem; onOpenRun: () => void }): JSX.Element | null {
  switch (item.kind) {
    case "user": return <div class="sa-user" data-testid="sa-user">{item.text}</div>;
    case "agent": return <div class="sa-agent" data-testid="sa-agent" data-live={String(item.live)}><Markup source={item.text} /></div>;
    case "activity":
      return <div class={`sa-act tone-${item.tone === "fail" ? "red" : "faint"}`} data-testid="sa-activity">{item.text}{item.hint ? ` · ${item.hint}` : ""}</div>;
    case "error": return <div class="sa-act tone-red" role="alert">{item.message}{item.hint ? ` · ${item.hint}` : ""}</div>;
    case "note": return <div class="sa-act dim">{item.text}</div>;
    case "question":
    case "approval":
      return (
        <div class="sa-needs" data-testid="sa-needs">
          <Led tone="blue" /><span>{item.kind === "question" ? "The studies agent asks you something." : "The studies agent needs your approval."}</span>
          <Button onClick={onOpenRun}>Answer</Button>
        </div>
      );
    case "studies": return <div class="sa-act dim">{item.calls.length} studies run</div>;
    case "queued": return <div class="sa-user sa-queued">{item.text}</div>;
    default: return null;
  }
}

function Composer({ agent, run, busy }: { agent: StudiesAgentController; run: AgentRun | null; busy: boolean }): JSX.Element {
  const [text, setText] = useState("");
  const sending = agent.sending.value;
  const send = async (t: string) => {
    const id = await agent.send(t, run);
    if (id) setText("");
  };
  return (
    <form class="sa-compose" onSubmit={(e) => { e.preventDefault(); void send(text); }} aria-label="Message the studies agent">
      {!run && (
        <button type="button" class="sa-chip" disabled={sending} onClick={() => { void send(PLAN_PROMPT); }} data-testid="studies-agent-plan">
          Plan the next studies with me
        </button>
      )}
      {agent.sendError.value && <p class="tone-red text sa-send-err" role="alert" data-testid="studies-agent-send-error">{agent.sendError.value}</p>}
      <div class="sa-input">
        <textarea rows={2} value={text} placeholder={busy ? "The studies agent is working…" : "Ask the studies agent about loads, results or what to run"}
          aria-label="Message the studies agent" data-testid="studies-agent-input"
          onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); void send(text); } }} />
        <button type="submit" class="sa-send" disabled={sending || busy || !text.trim()} aria-label="Send" data-testid="studies-agent-send">↑</button>
      </div>
    </form>
  );
}
