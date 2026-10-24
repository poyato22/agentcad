import type { JSX } from "preact";
import type { Approval } from "../../../api/types";

// STUB (Task 2). Task 8 replaces this file. Final props signature; it never approves anything.
export function ApprovalCard(props: { approval: Approval; runLabel: string }): JSX.Element {
  return (
    <div data-card data-stub="ApprovalCard" tabIndex={0} role="group" aria-label={`Needs you: ${props.approval.action}`}
      style={{ margin: "6px 8px", padding: "6px 8px" }}>
      <span class="label">needs you</span> <span class="text">{props.approval.action}</span>
      <span class="faint text"> · {props.runLabel}</span>
    </div>
  );
}
