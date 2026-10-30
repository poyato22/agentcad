// Browsing section (R3): the curated sites an agent may read without an approval card, and how
// many web fetches one agent run may make. Each site is a name, a domain and an optional search
// URL (https, on the site's own domain, with {q} where the query goes); a site with no search URL
// is opened by the agent but never searched (datasheet hosts). Suggested defaults are tagged, and
// "Restore defaults" brings back Mises's own list (POST /browser/sites/restore_defaults).
//
// Saving writes the whole list back with PUT /profile (suggested_sites); every entry the core
// sent is kept as it was, so editing one site never drops another. Rendered as plain text.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { FETCHES_MAX, FETCHES_MIN, onDomain, type SuggestedSite } from "../../../api/browser";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Card, ConfirmRemove, ErrorLine, SectionBox, useTask, wholeNumber, type SectionProps } from "../common";

/** Core DEFAULT_BROWSER_FETCHES, for a profile that predates the field. */
const DEFAULT_FETCHES = 60;
const DOMAIN_RE = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

function hostname(raw: string): string {
  return raw.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/[/?#].*$/, "").replace(/\.$/, "");
}

/** Why a search URL is refused (the core's rule), or null when it is fine. "" is fine (none). */
export function searchUrlProblem(url: string, domain: string): string | null {
  const v = url.trim();
  if (v === "") return null;
  if (v.split("{q}").length !== 2) return "Put {q} in the search URL once, where the query goes.";
  let u: URL;
  try { u = new URL(v.replace("{q}", "q")); } catch { return "The search URL isn't a valid URL."; }
  if (u.protocol !== "https:" || (u.port !== "" && u.port !== "443") || u.username || u.password) return "The search URL must start with https://.";
  if (domain && !onDomain(u.hostname, domain)) return `The search URL must be on ${domain}.`;
  return null;
}

export function SitesSection({ data, update }: SectionProps): JSX.Element {
  const sites = data.profile.suggested_sites ?? [];
  const task = useTask();
  const [confirm, setConfirm] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  async function save(next: SuggestedSite[]): Promise<boolean> {
    return task.run(async () => {
      const profile = await actions.client().updateProfile({ suggested_sites: next });
      update({ profile });
    }, "saved");
  }

  const remove = async (domain: string) => {
    setConfirm(null);
    await save(sites.filter((s) => s.domain !== domain));
  };
  const restore = async () => {
    setConfirmReset(false);
    await task.run(async () => {
      const list = await actions.client().restoreDefaultSites();
      update((d) => ({ profile: { ...d.profile, suggested_sites: list } }));
    }, "restored the suggested sites");
  };

  return (
    <SectionBox title="Browsing">
      <Card title="Suggested sites">
        <p class="profile-hint faint text">
          Agents read these sites without asking first, for example to look up a servo's dimensions or a datasheet. Any other site needs your approval each time. Some sites block automated access, and those pages may not load.
        </p>
        {sites.length === 0
          ? <p class="faint text">No suggested sites. Add one below, or restore the defaults.</p>
          : (
            <ul class="sites-list">
              {sites.map((s) => (
                <li key={s.domain} class="sites-row" data-site={s.domain}>
                  <div class="sites-main">
                    <span class="sites-name text">{s.name || s.domain}</span>
                    <span class="sites-domain faint text">{s.domain}</span>
                    {s.suggested && <span class="sites-tag" title="One of Mises's suggestions">suggested</span>}
                    {s.search_url === null && <span class="sites-tag" title="Agents open pages on this site but don't search it">no search</span>}
                  </div>
                  {s.categories.length > 0 && <div class="sites-cats faint text">{s.categories.join(", ")}</div>}
                  {confirm === s.domain
                    ? <ConfirmRemove question={`Remove ${s.name || s.domain}?`} busy={task.busy} onYes={() => void remove(s.domain)} onKeep={() => setConfirm(null)} />
                    : <Button variant="ghost" disabled={task.busy} aria-label={`Remove ${s.name || s.domain}`} onClick={() => setConfirm(s.domain)}>Remove</Button>}
                </li>
              ))}
            </ul>
          )}
        <div class="profile-row">
          {confirmReset
            ? (
              <span class="profile-confirm" role="group" aria-label="Confirm restore">
                <span class="text">Replace your list with Mises's suggested sites?</span>
                <Button variant="danger" disabled={task.busy} onClick={() => void restore()}>Restore</Button>
                <Button variant="ghost" disabled={task.busy} onClick={() => setConfirmReset(false)}>Keep mine</Button>
              </span>
            )
            : <Button variant="ghost" disabled={task.busy} onClick={() => setConfirmReset(true)}>Restore defaults</Button>}
          {task.note && <span class="dim">{task.note}</span>}
        </div>
        <ErrorLine error={task.error} />
      </Card>

      <AddSite existing={sites} busy={task.busy} onAdd={(site) => save([...sites.filter((s) => s.domain !== site.domain), site])} />
      <FetchLimit data={data} update={update} />
    </SectionBox>
  );
}

function AddSite({ existing, busy, onAdd }: { existing: SuggestedSite[]; busy: boolean; onAdd: (s: SuggestedSite) => Promise<boolean> }): JSX.Element {
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [searchUrl, setSearchUrl] = useState("");
  const [cats, setCats] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const host = hostname(domain);
  const prev = existing.find((s) => s.domain === host);

  async function submit(e: Event): Promise<void> {
    e.preventDefault();
    setErr(null);
    if (!DOMAIN_RE.test(host)) { setErr("Enter a domain, like pololu.com."); return; }
    const problem = searchUrlProblem(searchUrl, host);
    if (problem) { setErr(problem); return; }
    const site: SuggestedSite = {
      name: (name.trim() || host).slice(0, 60), domain: host, search_url: searchUrl.trim() || null,
      categories: cats.split(",").map((c) => c.trim().slice(0, 30)).filter(Boolean).slice(0, 12),
      suggested: prev?.suggested ?? false,
    };
    const ok = await onAdd(site);
    if (ok) { setName(""); setDomain(""); setSearchUrl(""); setCats(""); }
  }

  return (
    <Card title="Add a site">
      <form class="sites-add" onSubmit={(e) => void submit(e)} aria-label="Add a suggested site">
        <label class="profile-field"><span class="label">Name</span>
          <input aria-label="Site name" value={name} placeholder="Pololu" onInput={(e) => { setName(e.currentTarget.value); setErr(null); }} />
        </label>
        <label class="profile-field"><span class="label">Domain</span>
          <input aria-label="Site domain" value={domain} placeholder="pololu.com" onInput={(e) => { setDomain(e.currentTarget.value); setErr(null); }} />
        </label>
        <label class="profile-field"><span class="label">Search URL (optional)</span>
          <input aria-label="Search URL" value={searchUrl} placeholder="https://www.pololu.com/search?query={q}" onInput={(e) => { setSearchUrl(e.currentTarget.value); setErr(null); }} />
        </label>
        <label class="profile-field"><span class="label">Categories</span>
          <input aria-label="Categories" value={cats} placeholder="motors, electronics" onInput={(e) => { setCats(e.currentTarget.value); setErr(null); }} />
        </label>
        <p class="faint text">Leave the search URL empty for a site agents should only open, not search.</p>
        {prev && <p class="faint text">This domain is already in the list. Adding it again replaces that entry.</p>}
        <div class="profile-row"><Button type="submit" variant="ink" disabled={busy}>Add site</Button></div>
        <ErrorLine error={err} />
      </form>
    </Card>
  );
}

function FetchLimit({ data, update }: SectionProps): JSX.Element {
  const current = data.profile.browser_fetches_per_run ?? DEFAULT_FETCHES;
  const [val, setVal] = useState(String(current));
  const task = useTask();
  const n = wholeNumber(val, FETCHES_MIN, FETCHES_MAX);
  async function save(): Promise<void> {
    if (n === null) return;
    await task.run(async () => {
      const profile = await actions.client().updateProfile({ browser_fetches_per_run: n });
      update({ profile });
      setVal(String(profile.browser_fetches_per_run ?? n));
    }, "saved");
  }
  return (
    <Card title="Browser fetches per run">
      <p class="profile-hint faint text">How many pages one agent run may search or open. When an agent runs out, it stops browsing and tells you.</p>
      <label class="profile-field"><span class="label">Fetches</span>
        <input aria-label="Browser fetches per run" inputMode="numeric" value={val} onInput={(e) => { setVal(e.currentTarget.value); task.clear(); }} />
      </label>
      {n === null && <div class="profile-error text" role="alert">{`A whole number from ${FETCHES_MIN} to ${FETCHES_MAX}.`}</div>}
      <div class="profile-row">
        <Button variant="ink" disabled={n === null || n === current || task.busy} onClick={() => { void save(); }}>Save</Button>
        {task.note && <span class="dim">{task.note}</span>}
      </div>
      <ErrorLine error={task.error} />
    </Card>
  );
}
