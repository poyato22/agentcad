// The hook the conversation's tool-call rows use: a `part_show` call (R4) renders a part card
// under its line; any other call renders nothing. Kept in its own folder so the console's own
// files gain only one import and one line (ready to fold into U3a's announcement cards at merge).
import type { JSX } from "preact";
import type { LogItem } from "../../logModel";
import { partCardView } from "./cardModel";
import { PartCard } from "./PartCard";
import "./partcard.css";

export { isPartShowName } from "./cardModel";

type Call = Extract<LogItem, { kind: "call" }>;

export function PartCardSlot({ call, runId }: { call: Call; runId?: string | null }): JSX.Element | null {
  const v = partCardView(call, runId ?? call.runId ?? null);
  if (!v || v.failed) return null;        // a failed part_show stays just the red activity line
  return <div class="pcard-slot" data-part-slot>{<PartCard view={v} />}</div>;
}
