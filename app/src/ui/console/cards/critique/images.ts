// Render images for the cards: a render id is fetched once (GET /renders/{id}.png) and kept as a
// blob: URL; an inline data URL is used as is (announce.ts validated it).
import { useEffect, useState } from "preact/hooks";
import { actions } from "../../../../state/store";
import type { ImageRef } from "./announce";

const cache = new Map<string, Promise<string | null>>();

export function imageUrl(ref: ImageRef): Promise<string | null> {
  if ("dataUrl" in ref) return Promise.resolve(ref.dataUrl);
  let p = cache.get(ref.id);
  if (!p) {
    p = (async () => {
      try { return URL.createObjectURL(await actions.client().renderImage(ref.id)); } catch { cache.delete(ref.id); return null; }
    })();
    cache.set(ref.id, p);
  }
  return p;
}

/** The image's URL once known (null while loading, missing or failed). */
export function useImageUrl(ref: ImageRef | null): string | null {
  const key = ref ? ("dataUrl" in ref ? ref.dataUrl : ref.id) : null;
  const [url, setUrl] = useState<string | null>(ref && "dataUrl" in ref ? ref.dataUrl : null);
  useEffect(() => {
    if (!ref) { setUrl(null); return; }
    let on = true;
    void imageUrl(ref).then((u) => { if (on) setUrl(u); });
    return () => { on = false; };
  }, [key]);
  return url;
}

export function resetImageCache(): void { cache.clear(); }
