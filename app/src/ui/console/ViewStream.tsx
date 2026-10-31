// The dock's View stream (Plan 9 D4 / D13): what the build's agents show of the model (the lead and
// every sub-agent, treeStreams) — presented views, critique rounds, part cards and renders. The
// newest is large, the full dock width (click opens it full window, ViewImmersive), naming the
// agent that made it; the older ones sit in a filmstrip under it, and picking one shows it large
// until something new lands. Only the large view renders live; a strip tile fetches its still
// only once it scrolls into the strip's view (a long build has many).
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { AnnouncementCard, ANNOUNCE_META } from "./AnnouncementCard";
import { CritiqueCard } from "./cards/critique/CritiqueCard";
import { useImageUrl, useKeyedImageUrl } from "./cards/critique/images";
import { ViewCard } from "./cards/critique/ViewCard";
import { VIEW_KIND_TEXT } from "../viewer/modes/viewSpec";
import { Item } from "./Conversation";
import { RunTag } from "./SimStream";
import type { ThreadItem } from "./threadModel";
import type { DockEntry } from "./treeStreams";

export function ViewStream({ entries }: { entries: readonly DockEntry[] }): JSX.Element {
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
        <RunTag runId={main.runId} />
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
    case "critique": return <CritiqueCard round={item.round} />;
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
  const view = seen && item.kind === "view" ? item.view.thumbnail : null;
  const crit = seen && item.kind === "critique" ? item.round.after ?? item.round.before : null;
  const keyed = useKeyedImageUrl(view);
  const plain = useImageUrl(crit);
  const img = keyed ?? plain;
  return (
    <button type="button" ref={ref} class="vs-tile" aria-current={current ? "true" : undefined} aria-label={`Show ${label}`} title={label}
      data-tile={entry.key} data-tile-kind={item.kind === "view" ? item.view.spec.kind : item.kind} data-seen={seen ? "" : undefined} onClick={onPick}>
      {img ? <img src={img} alt="" draggable={false} /> : <span class="vs-tile-glyph faint" aria-hidden="true">◫</span>}
    </button>
  );
}
