// P11 F7: the images a user message carried, in the thread: thumbnails loaded from the build's
// store (GET /projects/{id}/chat-images/{image_id}, the admin token rides on the fetch); a click
// shows one larger. The bytes are cached (a bounded map, oldest out first); each thumbnail owns
// its object URL and revokes it when it goes (fix round 1).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { actions } from "../../state/store";
import type { ChatImageRef } from "./threadModel";

const MAX_CACHED = 48;
const blobs = new Map<string, Promise<Blob | null>>();

function blobOf(buildId: string, ref: ChatImageRef): Promise<Blob | null> {
  const key = `${buildId}/${ref.id}`;
  let p = blobs.get(key);
  if (p) {
    blobs.delete(key);          // most recently used last
  } else {
    p = actions.client().chatImage(buildId, ref.id, ref.media_type).then((b) => b, () => null);
  }
  blobs.set(key, p);
  while (blobs.size > MAX_CACHED) blobs.delete(blobs.keys().next().value as string);
  return p;
}

function Thumb({ buildId, img }: { buildId: string; img: ChatImageRef }): JSX.Element {
  const [url, setUrl] = useState<string | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    void blobOf(buildId, img).then((b) => {
      if (!live) return;
      made = b ? URL.createObjectURL(b) : null;
      setUrl(made);
    });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [buildId, img.id]);
  const label = `Attached image ${img.width}×${img.height}`;
  if (url === null) return <span class="cv-img cv-img-missing" data-chat-image={img.id}>image unavailable</span>;
  return (
    <button type="button" class={`cv-img${open ? " is-open" : ""}`} data-chat-image={img.id} aria-label={label}
      aria-pressed={open} title={label} onClick={() => setOpen((o) => !o)}>
      {url ? <img src={url} alt="" /> : <span class="cv-img-missing" />}
    </button>
  );
}

export function ChatImages({ images, buildId }: { images: readonly ChatImageRef[]; buildId: string | null }): JSX.Element | null {
  if (!buildId || images.length === 0) return null;
  return (
    <div class="cv-imgs" data-chat-images={images.length}>
      {images.map((i) => <Thumb key={i.id} buildId={buildId} img={i} />)}
    </div>
  );
}

/** Tests. */
export function resetChatImages(): void { blobs.clear(); }
