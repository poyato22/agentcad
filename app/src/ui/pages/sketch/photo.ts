// Photo upload for the Sketch page. The app checks type (by content, not by name) and size before
// sending; the core checks again and converts everything (HEIC included) to PNG.

export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
export type PhotoKind = "png" | "jpeg" | "heic";

export interface PickedPhoto {
  name: string;
  kind: PhotoKind;
  size: number;
  data_b64: string;
  /** blob: URL for the preview (null where the webview can't make one). */
  previewUrl: string | null;
}

const HEIF_BRANDS = new Set(["heic", "heix", "heim", "heis", "hevc", "hevx", "mif1", "msf1", "heif"]);

/** Same rule as core sketch.sniff. */
export function sniffImage(b: Uint8Array): PhotoKind | null {
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 && b[4] === 0x0d && b[5] === 0x0a && b[6] === 0x1a && b[7] === 0x0a) return "png";
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "jpeg";
  if (b.length >= 12 && String.fromCharCode(b[4], b[5], b[6], b[7]) === "ftyp" && HEIF_BRANDS.has(String.fromCharCode(b[8], b[9], b[10], b[11]))) return "heic";
  return null;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let s = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) s += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  return btoa(s);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

async function readBytes(blob: Blob): Promise<Uint8Array> {
  if (typeof blob.arrayBuffer === "function") return new Uint8Array(await blob.arrayBuffer());
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(new Uint8Array(r.result as ArrayBuffer));
    r.onerror = () => fail(r.error ?? new Error("could not read the file"));
    r.readAsArrayBuffer(blob);
  });
}

export async function readPhoto(file: File): Promise<{ ok: true; photo: PickedPhoto } | { ok: false; error: string }> {
  if (file.size > MAX_PHOTO_BYTES) return { ok: false, error: `${file.name || "This photo"} is ${formatBytes(file.size)}; photos can be 10 MB at most.` };
  if (file.size === 0) return { ok: false, error: "That file is empty." };
  let bytes: Uint8Array;
  try {
    bytes = await readBytes(file);
  } catch {
    return { ok: false, error: "Could not read that file." };
  }
  const kind = sniffImage(bytes);
  if (!kind) return { ok: false, error: "Photos must be PNG, JPEG or HEIC images." };
  let previewUrl: string | null = null;
  try {
    previewUrl = typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : null;
  } catch { /* no preview */ }
  return { ok: true, photo: { name: file.name, kind, size: file.size, data_b64: bytesToBase64(bytes), previewUrl } };
}

export function releasePhoto(p: PickedPhoto | null): void {
  if (p?.previewUrl && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(p.previewUrl);
}
