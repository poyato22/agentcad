// A part the agent put in front of the user (R4). Shows the proxied image, name, price, vendor and
// the key specs, with "Add to parts list", "Insert into build" (catalog-backed parts) and "Open".
// Name, vendor, price and specs are untrusted text, rendered plainly; the image loads only through
// the core proxy (ProxyImage). The actions target the active build.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { CoreError } from "../../../../api/client";
import { activeBuildId } from "../../../../state/builds";
import { actions } from "../../../../state/store";
import { Button } from "../../../common/Button";
import { ExternalLink } from "../../../common/ExternalLink";
import { ProxyImage } from "../../../common/ProxyImage";
import { priceText, specText, type PartCardView } from "./cardModel";

type Outcome = { tone: "ok" | "fail"; text: string } | null;

const MAX_SPECS = 6;

export function PartCard({ view }: { view: PartCardView }): JSX.Element {
  const c = view.card;
  const buildId = activeBuildId.value;
  const [busy, setBusy] = useState<"add" | "insert" | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const price = priceText(c.price, c.currency);

  const run = async (which: "add" | "insert", fn: () => Promise<unknown>, done: string) => {
    if (!buildId) { setOutcome({ tone: "fail", text: "Open a build first" }); return; }
    setBusy(which);
    setOutcome(null);
    try {
      await fn();
      setOutcome({ tone: "ok", text: done });
    } catch (e) {
      setOutcome({ tone: "fail", text: e instanceof CoreError ? (e.hint ? `${e.message} (${e.hint})` : e.message) : "Something went wrong" });
    } finally {
      setBusy(null);
    }
  };

  const addToList = () => run("add", () => actions.client().bomAdd(buildId as string, {
    kind: "bought", name: c.name, qty: 1, unit_cost: c.price, currency: c.currency ?? "USD",
    vendor: c.vendor, url: c.url, specs: c.specs, catalog_id: c.catalog_id, snapshot_id: c.snapshot_id, source: "user",
  }), "Added to parts list");

  const insert = () => run("insert", () => actions.client().catalogInsert(c.catalog_id as string, buildId as string), "Inserted into build");

  return (
    <div class="pcard" data-part-card data-state={view.failed ? "failed" : view.running ? "running" : "ready"}>
      <div class="pcard-main">
        <ProxyImage path={c.image} alt={c.name || "part"} class="pcard-img" />
        <div class="pcard-info">
          <div class="pcard-name text">{c.name || "Unnamed part"}</div>
          <div class="pcard-meta">
            {price && <span class="pcard-price text">{price}</span>}
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
        <Button variant="default" disabled={busy !== null} aria-label={`Add ${c.name || "part"} to the parts list`} onClick={addToList}>
          {busy === "add" ? "Adding…" : "Add to parts list"}
        </Button>
        {c.catalog_id && (
          <Button variant="default" disabled={busy !== null} aria-label={`Insert ${c.name || "part"} into the build`} onClick={insert}>
            {busy === "insert" ? "Inserting…" : "Insert into build"}
          </Button>
        )}
        {c.url && <ExternalLink class="btn btn-ghost pcard-open" href={c.url}>Open</ExternalLink>}
      </div>
      {outcome && <div class={`pcard-outcome text ${outcome.tone === "fail" ? "cv-fail" : "pcard-ok"}`} role="status">{outcome.text}</div>}
    </div>
  );
}
