// An <img> whose bytes come ONLY through the core's proxy (client.snapshotImage). Page- and
// model-supplied image URLs are never put on an <img src>; the caller passes a core-relative proxy
// path (api/browser.isProxyPath), we fetch it with the bearer token, and show it from a blob: URL.
// A failed or missing path renders a small placeholder, never a broken remote image.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { isProxyPath } from "../../api/browser";
import { actions } from "../../state/store";

/** blob: URLs already fetched this session, by proxy path (shared across cards and the panel). */
const cache = new Map<string, string>();

/** Tests: drop the cache. */
export function resetProxyImages(): void { cache.clear(); }

export function ProxyImage({ path, alt, class: cls }: { path: string | null; alt: string; class?: string }): JSX.Element {
  const ok = isProxyPath(path);
  const [src, setSrc] = useState<string | null>(ok ? cache.get(path) ?? null : null);
  const [failed, setFailed] = useState(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    if (!ok || cache.has(path)) return () => { live.current = false; };
    actions.client().snapshotImage(path).then((blob) => {
      const url = URL.createObjectURL(blob);
      cache.set(path, url);
      if (live.current) setSrc(url);
    }, () => { if (live.current) setFailed(true); });
    return () => { live.current = false; };
  }, [path, ok]);

  if (src) return <img class={cls} src={src} alt={alt} data-proxy-img="loaded" />;
  return (
    <span class={`${cls ?? ""} proxy-img-empty`} role="img" aria-label={alt || "image"} data-proxy-img={!ok ? "none" : failed ? "failed" : "waiting"}>
      <span aria-hidden="true">◫</span>
    </span>
  );
}
