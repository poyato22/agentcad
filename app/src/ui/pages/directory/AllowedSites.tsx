// "Allowed import sites": the profile's catalog_allowed_domains (GET/POST/DELETE
// /catalog/allowed_domains, admin token only; no agent can reach them). When an agent imports a
// product page from a site on this list, it needs no approval card; any other site asks first.
// Links the user pastes into Import from link never need one.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { describeError } from "../../profile/common";

export function AllowedSites(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [domains, setDomains] = useState<string[] | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let live = true;
    actions.client().allowedDomains().then((d) => { if (live) setDomains(d); }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, [open]);

  async function run(fn: () => Promise<string[]>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      setDomains(await fn());
      return true;
    } catch (e) {
      setError(describeError(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(): Promise<void> {
    const raw = draft.trim();
    if (!raw) return;
    // a pasted link is fine: keep only its host
    let domain = raw;
    try { if (/^[a-z]+:\/\//i.test(raw)) domain = new URL(raw).hostname; } catch { /* the core refuses it */ }
    if (await run(() => actions.client().allowDomain(domain))) setDraft("");
  }

  return (
    <section class="dir-sites" aria-label="Allowed import sites">
      <button type="button" class="dir-sites-toggle" aria-expanded={open} aria-controls="dir-sites-body" onClick={() => setOpen(!open)}>
        Allowed import sites{domains ? ` (${domains.length})` : ""}
      </button>
      {open && (
        <div id="dir-sites-body" class="dir-sites-body">
          <p class="dir-note faint">Agents can import parts from these sites without asking. Any other site needs your approval each time.</p>
          {domains === null && !error && <p class="faint">Loading…</p>}
          {domains && domains.length === 0 && <p class="dim dir-note">No sites yet.</p>}
          {domains && domains.length > 0 && (
            <ul class="dir-sites-list">
              {domains.map((d) => (
                <li key={d}>
                  <span class="dir-site">{d}</span>
                  <Button variant="ghost" aria-label={`Remove ${d}`} disabled={busy} onClick={() => { void run(() => actions.client().disallowDomain(d)); }}>Remove</Button>
                </li>
              ))}
            </ul>
          )}
          <form class="dir-sites-add" onSubmit={(e) => { e.preventDefault(); void add(); }}>
            <input aria-label="Site to allow" placeholder="shop.example.com" value={draft} onInput={(e) => setDraft(e.currentTarget.value)} />
            <Button type="submit" disabled={busy || !draft.trim()}>Allow</Button>
          </form>
          {error && <p class="dir-err" role="alert">{error}</p>}
        </div>
      )}
    </section>
  );
}
