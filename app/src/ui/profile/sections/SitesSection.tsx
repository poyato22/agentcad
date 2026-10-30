// Browsing section (R3): the curated sites an agent may browse without an approval card. The user
// can add, remove and reset the list. Each entry is a name, a domain and a search URL template
// with {q} where the query goes; a category list is optional. Suggested defaults are tagged.
//
// These are the user's own entries; still, they're rendered as plain text. Saving writes the whole
// list back with PUT /profile (suggested_sites).
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { SuggestedSite } from "../../../api/browser";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Card, ConfirmRemove, ErrorLine, SectionBox, useTask, type SectionProps } from "../common";

function hostname(raw: string): string {
  const s = raw.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  return s.toLowerCase();
}

export function SitesSection({ data, update }: SectionProps): JSX.Element {
  const sites = data.profile.suggested_sites ?? [];
  const task = useTask();
  const [confirm, setConfirm] = useState<string | null>(null);

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

  return (
    <SectionBox title="Browsing">
      <Card title="Suggested sites">
        <p class="profile-hint faint text">
          Agents look up parts here without asking first — a servo's dimensions, a datasheet. Any other site needs your approval each time. Some sites block automated access; those may not load.
        </p>
        {sites.length === 0
          ? <p class="faint text">No suggested sites. Add one below, or reset to the defaults.</p>
          : (
            <ul class="sites-list">
              {sites.map((s) => (
                <li key={s.domain} class="sites-row" data-site={s.domain}>
                  <div class="sites-main">
                    <span class="sites-name text">{s.name || s.domain}</span>
                    <span class="sites-domain faint text">{s.domain}</span>
                    {s.suggested && <span class="sites-tag" title="A built-in suggestion">suggested</span>}
                  </div>
                  {s.categories.length > 0 && <div class="sites-cats faint text">{s.categories.join(" · ")}</div>}
                  {confirm === s.domain
                    ? <ConfirmRemove question={`Remove ${s.name || s.domain}?`} busy={task.busy} onYes={() => void remove(s.domain)} onKeep={() => setConfirm(null)} />
                    : <Button variant="ghost" disabled={task.busy} aria-label={`Remove ${s.name || s.domain}`} onClick={() => setConfirm(s.domain)}>Remove</Button>}
                </li>
              ))}
            </ul>
          )}
        {task.note && <span class="dim">{task.note}</span>}
        <ErrorLine error={task.error} />
      </Card>

      <AddSite existing={sites} busy={task.busy} onAdd={(site) => save([...sites.filter((s) => s.domain !== site.domain), site])} />
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
  const dup = host !== "" && existing.some((s) => s.domain === host);

  async function submit(e: Event): Promise<void> {
    e.preventDefault();
    setErr(null);
    if (host === "") { setErr("Enter a domain, like mcmaster.com."); return; }
    const url = searchUrl.trim();
    if (!/^https:\/\//i.test(url)) { setErr("The search URL must start with https://."); return; }
    if (!url.includes("{q}")) { setErr("Put {q} in the search URL where the query goes."); return; }
    const site: SuggestedSite = {
      name: name.trim() || host, domain: host, search_url: url,
      categories: cats.split(",").map((c) => c.trim()).filter(Boolean), suggested: false,
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
        <label class="profile-field"><span class="label">Search URL</span>
          <input aria-label="Search URL" value={searchUrl} placeholder="https://www.pololu.com/search?query={q}" onInput={(e) => { setSearchUrl(e.currentTarget.value); setErr(null); }} />
        </label>
        <label class="profile-field"><span class="label">Categories</span>
          <input aria-label="Categories" value={cats} placeholder="motors, electronics" onInput={(e) => { setCats(e.currentTarget.value); setErr(null); }} />
        </label>
        {dup && <p class="faint text">This domain is already in the list; adding will update it.</p>}
        <div class="profile-row"><Button type="submit" variant="ink" disabled={busy}>Add site</Button></div>
        <ErrorLine error={err} />
      </form>
    </Card>
  );
}
