// A part an agent put in front of the user (R4). The card is fetched by id from the core
// (GET /browser/cards/{card_id}) and shows the proxied image, name, price, vendor, key specs and
// "Open · <domain>". It is display-only (P10 E15): a part the agent selects is inserted into the
// build and listed in its parts list by the core itself (C1), and the card says so when the result
// names the part / the row. Name, vendor, price and specs are untrusted page text, rendered
// plainly; the image loads only through the core proxy (ProxyImage).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { PartCard as Card } from "../../../../api/browser";
import { CoreError } from "../../../../api/client";
import { money } from "../../../../lib/money";
import { activeBuildId, builds } from "../../../../state/builds";
import { actions, runs } from "../../../../state/store";
import { linkHost } from "../../../markup/parse";
import { ExternalLink } from "../../../common/ExternalLink";
import { ProxyImage } from "../../../common/ProxyImage";
import { specText, type PartCardView } from "./cardModel";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; card: Card };

const MAX_SPECS = 6;

/** Cards already fetched this session (a card never changes once shown). */
const cards = new Map<string, Card>();

/** Tests. */
export function resetPartCards(): void { cards.clear(); }

const errText = (e: unknown): string => (e instanceof CoreError ? (e.hint ? `${e.message} (${e.hint})` : e.message) : "Something went wrong");

export function PartCard({ view }: { view: PartCardView }): JSX.Element {
  const id = view.cardId;
  const [load, setLoad] = useState<Load>(() => (id && cards.has(id) ? { state: "ready", card: cards.get(id) as Card } : { state: "loading" }));

  useEffect(() => {
    if (!id || cards.has(id)) return;
    let live = true;
    actions.client().partCard(id).then(
      (card) => { cards.set(id, card); if (live) setLoad({ state: "ready", card }); },
      (e: unknown) => { if (live) setLoad({ state: "error", message: errText(e) }); },
    );
    return () => { live = false; };
  }, [id]);

  if (view.running || !id) {
    return <div class="pcard pcard-pending faint" data-part-card data-state="running">Looking up the part…</div>;
  }
  if (load.state === "loading") return <div class="pcard pcard-pending faint" data-part-card data-state="loading">Loading the part card…</div>;
  if (load.state === "error") return <div class="pcard cv-fail text" data-part-card data-state="error" role="status">{`Couldn't load the part card: ${load.message}`}</div>;
  return <CardBody card={load.card} view={view} />;
}

function CardBody({ card: c, view }: { card: Card; view: PartCardView }): JSX.Element {
  const runProject = view.runId ? runs.value.get(view.runId)?.project_id : undefined;
  const projectId = c.project_id || runProject || activeBuildId.value;
  const buildName = projectId ? builds.value.find((b) => b.id === projectId)?.name ?? null : null;
  const name = c.name || "part";
  const host = c.url ? linkHost(c.url) : "";
  return (
    <div class="pcard" data-part-card data-state="ready" data-card={c.id}>
      <div class="pcard-main">
        <ProxyImage path={c.image} alt={name} class="pcard-img" />
        <div class="pcard-info">
          <div class="pcard-name text">{name}</div>
          <div class="pcard-meta">
            {c.price !== null && <span class="pcard-price text">{money(c.price, c.currency)}</span>}
            {c.vendor && <span class="pcard-vendor faint text">{c.vendor}</span>}
          </div>
          {c.specs.length > 0 && (
            <ul class="pcard-specs">
              {c.specs.slice(0, MAX_SPECS).map((s, i) => <li key={i} class="faint text">{specText(s)}</li>)}
            </ul>
          )}
        </div>
      </div>
      <div class="pcard-actions">
        {view.partId && <span class="pcard-status" data-in-build={view.partId}>{buildName ? `In ${buildName}` : "In the build"}</span>}
        {view.bomId && <span class="pcard-status" data-in-list={view.bomId}>In the parts list</span>}
        {c.url && host && <ExternalLink class="btn btn-ghost pcard-open" href={c.url}>{`Open · ${host}`}</ExternalLink>}
      </div>
    </div>
  );
}
