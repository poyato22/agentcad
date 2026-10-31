// The dock's View stream (Plan 9 D4 / D13): what the agents show of the model — presented views,
// critique rounds, part cards and renders. The newest is large, the full dock width (click opens it
// full window, ViewImmersive); the older ones sit in a filmstrip under it, and picking one shows it
// large until something new lands. Only the large view renders live; the strip shows stills.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { AnnouncementCard, ANNOUNCE_META } from "./AnnouncementCard";
import { CritiqueCard } from "./cards/critique/CritiqueCard";
import { useImageUrl, useKeyedImageUrl } from "./cards/critique/images";
import { ViewCard } from "./cards/critique/ViewCard";
import { VIEW_KIND_TEXT } from "../viewer/modes/viewSpec";
import { Item } from "./Conversation";
import type { ThreadItem } from "./threadModel";

export function ViewStream({ items, agent, runId }: { items: readonly ThreadItem[]; agent: AgentRun["agent"]; runId: string | null }): JSX.Element {
  const newest = items[items.length - 1] ?? null;
  const [pick, setPick] = useState<string | null>(null);
  // something new lands: show it
  useEffect(() => { setPick(null); }, [newest?.key]);
  if (!newest) return <p class="dock-empty faint" data-stream-empty>Views and renders the agents make show here.</p>;
  const main = (pick ? items.find((i) => i.key === pick) : null) ?? newest;
  return (
    <div class="vs" data-view-stream>
      <div class="vs-main" data-view-main={main.key}><Large item={main} agent={agent} runId={runId} /></div>
      {items.length > 1 && (
        <div class="vs-strip" role="list" aria-label="Earlier views" data-filmstrip>
          {items.map((it) => (
            <div role="listitem" key={it.key}>
              <Tile item={it} current={it.key === main.key} onPick={() => setPick(it.key === newest.key ? null : it.key)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Large({ item, agent, runId }: { item: ThreadItem; agent: AgentRun["agent"]; runId: string | null }): JSX.Element {
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

function Tile({ item, current, onPick }: { item: ThreadItem; current: boolean; onPick(): void }): JSX.Element {
  const label = tileLabel(item);
  const view = item.kind === "view" ? item.view.thumbnail : null;
  const crit = item.kind === "critique" ? item.round.after ?? item.round.before : null;
  const keyed = useKeyedImageUrl(view);
  const plain = useImageUrl(crit);
  const img = keyed ?? plain;
  return (
    <button type="button" class="vs-tile" aria-current={current ? "true" : undefined} aria-label={`Show ${label}`} title={label}
      data-tile={item.key} onClick={onPick}>
      {img ? <img src={img} alt="" draggable={false} /> : <span class="vs-tile-glyph faint" aria-hidden="true">◫</span>}
    </button>
  );
}
