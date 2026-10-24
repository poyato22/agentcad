// Agent console (right column): roster · filters · step log (approval / question cards inline)
// · command line.
import type { JSX } from "preact";
import { CommandLine } from "./command/CommandLine";
import { Filters } from "./Filters";
import { Roster } from "./Roster";
import { StepLog } from "./StepLog";
import "./console.css";

export function ConsolePane(): JSX.Element {
  return (
    <div class="con" data-console>
      <Roster />
      <Filters />
      <StepLog />
      <CommandLine />
    </div>
  );
}
