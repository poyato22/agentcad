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

export function resetImageCache(): void { cache.clear(); keyed.clear(); }

/** m10: the core renders on a light backdrop (white; G-R's studio grey #eeeef0), a light box in the
 *  dark theme. Make the backdrop that touches the image's edge transparent (a flood fill from the
 *  border of pixels within `tolerance` of the corner's colour, so a white part's own faces stay),
 *  so the card's sheet shows through. A dark corner: nothing to key. Pure, for tests. */
export function knockOutBackground(px: Uint8ClampedArray, w: number, h: number, tolerance = 11): number {
  const key = [px[0], px[1], px[2]];
  if (key.some((c) => c < 200)) return 0;
  const white = (i: number) => Math.abs(px[i * 4] - key[0]) <= tolerance && Math.abs(px[i * 4 + 1] - key[1]) <= tolerance
    && Math.abs(px[i * 4 + 2] - key[2]) <= tolerance && px[i * 4 + 3] > 0;
  const seen = new Uint8Array(w * h);
  const stack: number[] = [];
  const push = (i: number) => { if (!seen[i] && white(i)) { seen[i] = 1; stack.push(i); } };
  for (let x = 0; x < w; x++) { push(x); push((h - 1) * w + x); }
  for (let y = 0; y < h; y++) { push(y * w); push(y * w + w - 1); }
  let n = 0;
  while (stack.length) {
    const i = stack.pop() as number;
    px[i * 4 + 3] = 0; n++;
    const x = i % w; const y = (i - x) / w;
    if (x > 0) push(i - 1);
    if (x < w - 1) push(i + 1);
    if (y > 0) push(i - w);
    if (y < h - 1) push(i + w);
  }
  return n;
}

const keyed = new Map<string, Promise<string>>();

/** `url` with its white backdrop knocked out (a PNG data URL); the original when that can't be
 *  done here (no 2D canvas, a tainted image). */
export function keyedUrl(url: string): Promise<string> {
  let p = keyed.get(url);
  if (!p) {
    p = new Promise<string>((done) => {
      const img = new Image();
      img.onload = () => {
        try {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth; c.height = img.naturalHeight;
          const g = c.getContext("2d");
          if (!g || !c.width || !c.height) { done(url); return; }
          g.drawImage(img, 0, 0);
          const d = g.getImageData(0, 0, c.width, c.height);
          if (knockOutBackground(d.data, c.width, c.height) === 0) { done(url); return; }
          g.putImageData(d, 0, 0);
          done(c.toDataURL("image/png"));
        } catch { done(url); }
      };
      img.onerror = () => done(url);
      img.src = url;
    });
    keyed.set(url, p);
  }
  return p;
}

/** useImageUrl, with the render's white backdrop made transparent (m10). */
export function useKeyedImageUrl(ref: ImageRef | null): string | null {
  const raw = useImageUrl(ref);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!raw) { setUrl(null); return; }
    let on = true;
    setUrl(raw);
    void keyedUrl(raw).then((u) => { if (on) setUrl(u); });
    return () => { on = false; };
  }, [raw]);
  return url;
}
