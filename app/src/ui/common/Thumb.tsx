// A thumbnail <img> backed by an authenticated Blob fetch (the core needs the bearer token, so
// the URL can't go straight into src). It owns the object URL's lifecycle and re-fetches when
// `cacheKey` changes. Before the image arrives — and when the part/build has no preview yet — it
// shows a quiet placeholder glyph instead of a broken image.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

export function Thumb({ load, alt, cacheKey, class: className, glyph = "◇" }: {
  load: () => Promise<Blob>;
  alt: string;
  /** Re-fetch whenever this changes (e.g. `${id}:${version}`). */
  cacheKey: string;
  class?: string;
  glyph?: string;
}): JSX.Element {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    let made: string | null = null;
    setUrl(null);
    setFailed(false);
    load()
      .then((b) => { if (live) { made = URL.createObjectURL(b); setUrl(made); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [cacheKey]);

  if (url) return <img class={className} src={url} alt={alt} loading="lazy" draggable={false} />;
  return (
    <div class={`thumb-blank${className ? ` ${className}` : ""}`} role="img" aria-label={alt}
      data-thumb-state={failed ? "none" : "loading"}>
      <span aria-hidden="true">{glyph}</span>
    </div>
  );
}
