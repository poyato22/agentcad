// Images attached to the next chat message (P11 F7): pasted (⌘V), dropped on the composer or
// picked with 📎. Each is shrunk here first — at most MAX_EDGE px on its long side and under
// MAX_BYTES (PNG kept when it fits, else JPEG, stepping quality then size down); HEIC turns into
// JPEG on the way (the WebKit view decodes it) — and the core checks it all again. At most
// MAX_IMAGES per message. An image that looks like a technical drawing (mostly white paper,
// dark lines, no colour) offers the drawing-import path on its thumbnail. The encoder is
// injectable (jsdom has no canvas).
import { signal, type Signal } from "@preact/signals";
import type { ChatImageIn } from "../../../api/client";

export const MAX_IMAGES = 5;
export const MAX_EDGE = 2048;
export const MAX_BYTES = 1_000_000;
const ACCEPTED = /^image\/(png|jpeg|webp|gif|heic|heif)$/i;
const BY_NAME = /\.(png|jpe?g|webp|gif|heic|heif)$/i;

export interface Attachment {
  id: string;
  name: string;
  mediaType: string;
  dataB64: string;
  width: number;
  height: number;
  /** Looks like a technical drawing: offer "Make a part from this drawing". */
  drawing: boolean;
  /** The original file (the drawing import takes it as it is). */
  file: File;
}

export interface Encoded { mediaType: string; dataB64: string; width: number; height: number; drawing: boolean }
export type Encoder = (file: File) => Promise<Encoded>;

/** The images waiting for the next message. */
export const attachments: Signal<readonly Attachment[]> = signal<readonly Attachment[]>([]);

let seq = 0;

/** Whether `file` is an image the chat takes (by type, or by name when the type is empty). */
export function isImage(file: { type: string; name: string }): boolean {
  return ACCEPTED.test(file.type) || (!file.type && BY_NAME.test(file.name));
}

/** A drawing on paper: mostly near-white, some dark lines, hardly any colour. `rgba` is a small
 *  sample of the image's pixels. */
export function looksLikeDrawing(rgba: Uint8ClampedArray, name = ""): boolean {
  if (/drawing|blueprint|dwg|sheet|print|schematic/i.test(name)) return true;
  const n = Math.floor(rgba.length / 4);
  if (n === 0) return false;
  let white = 0, dark = 0, colour = 0, mid = 0;
  for (let i = 0; i < n; i++) {
    const r = rgba[i * 4], g = rgba[i * 4 + 1], b = rgba[i * 4 + 2];
    const hi = Math.max(r, g, b), lo = Math.min(r, g, b);
    if (hi - lo > 40) colour++;
    if (lo > 225) white++;
    else if (hi < 110) dark++;
    else mid++;
  }
  // lines, not filled shapes: a little dark ink, hardly any mid-grey (a shaded render has both)
  return white / n > 0.7 && dark / n > 0.005 && dark / n < 0.12 && mid / n < 0.12 && colour / n < 0.05;
}

const b64 = (bytes: Uint8Array): string => {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`could not read ${file.name || "the image"}`)); };
    img.src = url;
  });
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

/** The browser's encoder: canvas downscale + PNG / JPEG under MAX_BYTES. */
export const browserEncoder: Encoder = async (file) => {
  const img = await loadImage(file);
  let scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
  const canvas = document.createElement("canvas");
  const draw = () => {
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const g = canvas.getContext("2d");
    if (!g) throw new Error("no canvas to shrink the image with");
    g.fillStyle = "#fff";              // transparent PNGs read as paper, as the core does
    g.fillRect(0, 0, canvas.width, canvas.height);
    g.drawImage(img, 0, 0, canvas.width, canvas.height);
    return g;
  };
  draw();
  const sample = document.createElement("canvas");
  sample.width = 64; sample.height = 64;
  const sg = sample.getContext("2d");
  sg?.drawImage(canvas, 0, 0, 64, 64);
  const drawing = sg ? looksLikeDrawing(sg.getImageData(0, 0, 64, 64).data, file.name) : false;
  const lossless = /png|gif/i.test(file.type);
  for (let round = 0; round < 8; round++) {
    if (lossless && round === 0) {
      const png = await toBlob(canvas, "image/png");
      if (png && png.size < MAX_BYTES) return { mediaType: "image/png", dataB64: b64(new Uint8Array(await png.arrayBuffer())), width: canvas.width, height: canvas.height, drawing };
    }
    for (const q of [0.85, 0.7, 0.55]) {
      const jpg = await toBlob(canvas, "image/jpeg", q);
      if (jpg && jpg.size < MAX_BYTES) return { mediaType: "image/jpeg", dataB64: b64(new Uint8Array(await jpg.arrayBuffer())), width: canvas.width, height: canvas.height, drawing };
    }
    scale *= 0.75;
    draw();
  }
  throw new Error(`${file.name || "the image"} can't be made small enough`);
};

/** The encoder the composer uses (tests swap it: jsdom has no canvas). */
export const imageEncoder: { current: Encoder } = { current: browserEncoder };

/** Add `files` (images only, up to MAX_IMAGES in all). Returns why some were left out, or null. */
export async function addFiles(files: Iterable<File>, encode: Encoder = imageEncoder.current): Promise<string | null> {
  const list = [...files];
  const images = list.filter(isImage);
  const notes: string[] = [];
  if (images.length < list.length) notes.push("only images can be attached (PNG, JPEG, WebP, GIF or HEIC)");
  const room = MAX_IMAGES - attachments.peek().length;
  if (images.length > room) notes.push(`at most ${MAX_IMAGES} images per message`);
  for (const file of images.slice(0, Math.max(0, room))) {
    try {
      const e = await encode(file);
      if (attachments.peek().length >= MAX_IMAGES) break;
      attachments.value = [...attachments.peek(), { id: `att-${++seq}`, name: file.name || "image", file, ...e }];
    } catch (err) {
      notes.push(err instanceof Error ? err.message : String(err));
    }
  }
  return notes.length ? notes.join(" · ") : null;
}

export function removeAttachment(id: string): void {
  attachments.value = attachments.peek().filter((a) => a.id !== id);
}

export function clearAttachments(): void { attachments.value = []; }

/** The attachments as the core's message images. */
export function toImagesIn(list: readonly Attachment[]): ChatImageIn[] {
  return list.map((a) => ({ media_type: a.mediaType, data_b64: a.dataB64 }));
}
