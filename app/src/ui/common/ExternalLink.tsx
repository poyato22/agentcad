// A link that opens in the system browser through platform/openExternal (http/https only). The
// href stays on the element (hover, copy link) and the full URL is in the title; callers put the
// host in the visible label so the domain is shown before it opens. A URL that isn't http(s)
// renders as plain text, not a link.
import type { ComponentChildren, JSX } from "preact";
import { externalUrl, openExternal } from "../../platform/openExternal";

export function ExternalLink({ href, class: cls, children }: { href: string; class?: string; children: ComponentChildren }): JSX.Element {
  const url = externalUrl(href);
  if (!url) return <span class={cls}>{children}</span>;
  return (
    <a class={cls} href={url} target="_blank" rel="noopener noreferrer" title={url} data-external
      onClick={(e) => { e.preventDefault(); void openExternal(url); }}>{children}</a>
  );
}
