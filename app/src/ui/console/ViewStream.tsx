// The dock's View stream (Plan 9 D4 / D13): what the build's agents show of the model (the lead and
// every sub-agent, treeStreams) — presented views, critique rounds, part cards and renders. The
// newest is large, the full dock width (click opens it full window, ViewImmersive), naming the
// agent that made it; the older ones sit in a filmstrip under it, and picking one shows it large
// until something new lands. Only the large view renders live; a strip tile fetches its still
// only once it scrolls into the strip's view (a long build has many). Every tile shows a picture
// (FW-app M4): a view's still, a critique's image, a part card's part render, a concept sketch's
// first image, a view not rendered yet its part; a card with none (a pitch) shows its kind's mark.
import type { JSX } from "preact";
import { memo } from "preact/compat";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { actions, parts } from "../../state/store";
import { Thumb } from "../common/Thumb";
import { AnnouncementCard, ANNOUNCE_META } from "./AnnouncementCard";
import { CritiqueCard } from "./cards/critique/CritiqueCard";
import { useImageUrl, useKeyedImageUrl } from "./cards/critique/images";
import { ViewCard } from "./cards/critique/ViewCard";
import { partsForSpec, VIEW_KIND_TEXT } from "../viewer/modes/viewSpec";
import { Item } from "./Conversation";
import { manyRuns, RunTag } from "./SimStream";
import type { ThreadItem } from "./threadModel";
import type { DockEntry } from "./treeStreams";

/** Memoized: re-renders only when the entries change (a streamed token never changes them). */
export const ViewStream = memo(ViewStreamList);

function ViewStreamList({ entries }: { entries: readonly DockEntry[] }): JSX.Element {
  const newest = entries[entries.length - 1] ?? null;
  const [pick, setPick] = useState<string | null>(null);
  const strip = useRef<HTMLDivElement>(null);
  // something new lands: show it, and bring the strip's end (the newest) into view
  useEffect(() => {
    setPick(null);
    const el = strip.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [newest?.key]);
  if (!newest) return <p class="dock-empty faint" data-stream-empty>Views and renders the agents make show here.</p>;
  const main = (pick ? entries.find((e) => e.key === pick) : null) ?? newest;
  return (
    <div class="vs" data-view-stream>
      <div class="vs-main" data-view-main={main.key} data-run={main.runId}>
        {manyRuns.value && <RunTag runId={main.runId} />}
        <Large key={main.key} item={main.item} agent={main.agent} runId={main.runId} />
      </div>
      {entries.length > 1 && (
        <div class="vs-strip" role="list" aria-label="Earlier views" data-filmstrip ref={strip}>
          {entries.map((e) => (
            <div role="listitem" key={e.key}>
              <Tile entry={e} root={strip} current={e.key === main.key} onPick={() => setPick(e.key === newest.key ? null : e.key)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Large({ item, agent, runId }: { item: ThreadItem; agent: AgentRun["agent"]; runId: string }): JSX.Element {
  switch (item.kind) {
    case "view": return <ViewCard view={item.view} group={item.group} />;
    case "critique": return <CritiqueCard round={item.round} large />;
    case "announce": return <AnnouncementCard announcement={item.announcement} />;
    default: return <Item item={item} agent={agent} runId={runId} />;
  }
}

/** What a filmstrip tile says about its item. */
export function tileLabel(item: ThreadItem): string {
  switch (item.kind) {
    case "view": return item.view.spec.caption || VIEW_KIND_TEXT[item.view.spec.kind];
    case "critique": return item.round.title || "Critique round";
    case "announce": return item.announcement.title || (ANNOUNCE_META[item.announcement.kind]?.label ?? "Update");
    case "activity": return item.text;
    case "note": return item.text;
    default: return "View";
  }
}

/** True once the element has been within (or near) the strip's visible part. */
function useSeenIn(root: { current: HTMLElement | null }): [{ current: HTMLButtonElement | null }, boolean] {
  const ref = useRef<HTMLButtonElement>(null);
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    if (seen || !ref.current) return;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect(); } },
      { root: root.current, rootMargin: "0px 140px" });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

function Tile({ entry, root, current, onPick }: { entry: DockEntry; root: { current: HTMLElement | null }; current: boolean; onPick(): void }): JSX.Element {
  const item = entry.item;
  const label = tileLabel(item);
  const [ref, seen] = useSeenIn(root);
  const ann = item.kind === "announce" ? item.announcement : null;
  const view = seen && item.kind === "view" ? item.view.thumbnail : null;
  const crit = seen && item.kind === "critique" ? item.round.after ?? item.round.before
    : seen && ann?.ref.image_id ? { id: ann.ref.image_id } : null;
  const keyed = useKeyedImageUrl(view);
  const plain = useImageUrl(crit);
  const img = keyed ?? plain;
  // no still: a part card's part, or a view (not rendered yet) its first part
  const partId = ann && !ann.ref.image_id ? ann.ref.part_id ?? null
    : item.kind === "view" ? partsForSpec(item.view.spec, parts.value)[0] ?? null : null;
  const glyph = ann ? ANNOUNCE_META[ann.kind]?.glyph ?? "◫" : "◫";
  return (
    <button type="button" ref={ref} class="vs-tile" aria-current={current ? "true" : undefined} aria-label={`Show ${label}`} title={label}
      data-tile={entry.key} data-tile-kind={item.kind === "view" ? item.view.spec.kind : ann ? ann.kind : item.kind} data-seen={seen ? "" : undefined} onClick={onPick}>
      {img ? <img src={img} alt="" draggable={false} data-tile-img />
        : partId && seen ? <PartTile partId={partId} glyph={glyph} />
          : <span class="vs-tile-glyph faint" aria-hidden="true">{glyph}</span>}
    </button>
  );
}

/** A part card's tile: the part's render (the same route as the card's, small). */
function PartTile({ partId, glyph }: { partId: string; glyph: string }): JSX.Element {
  const version = parts.value.find((p) => p.part_id === partId)?.version ?? 0;
  return (
    <Thumb class="vs-tile-thumb" alt="" glyph={glyph} cacheKey={`${partId}:${version}:tile`}
      load={() => { try { return actions.client().partThumb(partId, TILE_PX); } catch (e) { return Promise.reject(e); } }} />
  );
}

/** A tile's render size (2x its 64 px width). */
const TILE_PX = 128;
