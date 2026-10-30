// A part an agent put in front of the user (R4). The card is fetched by id from the core
// (GET /browser/cards/{card_id}) and shows the proxied image, name, price, vendor and key specs,
// with "Add to parts list", "Insert into <build>" (catalog-backed cards) and "Open · <domain>".
// Name, vendor, price and specs are untrusted page text, rendered plainly; the image loads only
// through the core proxy (ProxyImage). The actions target the card's OWN build (its
// project_id, else its run's build), never just whichever build tab is active.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { PartCard as Card } from "../../../../api/browser";
import { CoreError } from "../../../../api/client";
import { money } from "../../../../lib/money";
import { activeBuildId, builds } from "../../../../state/builds";
import { actions, runs } from "../../../../state/store";
import { linkHost } from "../../../markup/parse";
import { Button } from "../../../common/Button";
import { ExternalLink } from "../../../common/ExternalLink";
import { ProxyImage } from "../../../common/ProxyImage";
import { specText, type PartCardView } from "./cardModel";

type Outcome = { tone: "ok" | "fail"; text: string } | null;
type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; card: Card };

const MAX_SPECS = 6;

/** Cards already fetched this session (a card never changes once shown). */
const cards = new Map<string, Card>();
/** "www.adafruit.com" → "adafruit.com"; null for no site. */
export function shopOf(domain: string | null | undefined): string | null {
  const d = (domain ?? "").trim().toLowerCase().replace(/^www\./, "");
  return d || null;
}

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
  return <CardBody card={load.card} runId={view.runId} />;
}

function CardBody({ card: c, runId }: { card: Card; runId: string | null }): JSX.Element {
  const runProject = runId ? runs.value.get(runId)?.project_id : undefined;
  const projectId = c.project_id || runProject || activeBuildId.value;
  const buildName = projectId ? builds.value.find((b) => b.id === projectId)?.name ?? null : null;
  const [busy, setBusy] = useState<"add" | "insert" | null>(null);
  const [added, setAdded] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const name = c.name || "part";
  const host = c.url ? linkHost(c.url) : "";

  const run = async (which: "add" | "insert", fn: (pid: string) => Promise<unknown>, done: string) => {
    if (!projectId) { setOutcome({ tone: "fail", text: "Open a build first" }); return; }
    setBusy(which);
    setOutcome(null);
    try {
      await fn(projectId);
      if (which === "add") setAdded(true);
      setOutcome({ tone: "ok", text: done });
    } catch (e) {
      setOutcome({ tone: "fail", text: errText(e) });
    } finally {
      setBusy(null);
    }
  };

  // exactly what the core's parts-list row accepts; an unknown currency stays unknown (null)
  // m3: the parts list's vendor is the shop you buy from (the page's site), not the brand; the
  // brand rides along as a spec so it isn't lost
  const shop = shopOf(c.domain ?? host) ?? c.vendor;
  const brand = c.vendor && c.vendor !== shop && !c.specs.some((s) => s.key.toLowerCase() === "brand")
    ? [{ key: "brand", value: c.vendor, unit: null }] : [];
  const addToList = () => run("add", (pid) => actions.client().bomAdd(pid, {
    kind: "bought", name, qty: 1, unit_cost: c.price, currency: c.currency,
    vendor: shop, url: c.url, catalog_id: c.catalog_id,
    specs: [...brand, ...c.specs.filter((s) => s.key.trim() !== "")].slice(0, 40),
  }), buildName ? `Added to the parts list of ${buildName}` : "Added to the parts list");
  const insert = () => run("insert", (pid) => actions.client().catalogInsert(c.catalog_id as string, pid),
    buildName ? `Inserted into ${buildName}` : "Inserted into the build");

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
        <Button variant="default" disabled={busy !== null || added} aria-label={added ? `${name} is in the parts list` : `Add ${name} to the parts list`} onClick={addToList}>
          {added ? "In parts list" : busy === "add" ? "Adding…" : "Add to parts list"}
        </Button>
        {c.catalog_backed && c.catalog_id && (
          <Button variant="default" disabled={busy !== null} aria-label={`Insert ${name} into ${buildName ?? "the build"}`} onClick={insert}>
            {busy === "insert" ? "Inserting…" : `Insert into ${buildName ?? "build"}`}
          </Button>
        )}
        {c.url && host && <ExternalLink class="btn btn-ghost pcard-open" href={c.url}>{`Open · ${host}`}</ExternalLink>}
      </div>
      {outcome && <div class={`pcard-outcome text ${outcome.tone === "fail" ? "cv-fail" : "pcard-ok"}`} role="status">{outcome.text}</div>}
    </div>
  );
}
