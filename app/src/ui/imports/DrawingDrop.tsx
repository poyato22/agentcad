// "Drop a drawing to make a part": a technical drawing (PDF, PNG, JPG or DXF) dropped here goes
// to the core (POST /imports/drawing), which makes the part and checks its dimensions.
//
//   · variant "tile" (Home): an always-visible drop target with a "Choose a file…" button, and the
//     import cards under it.
//   · variant "overlay" (inside a build's viewer): the whole viewer becomes the drop target while
//     a file is dragged over it. The cards float in its corner. An empty build also shows a small
//     "Choose a drawing…" line under the viewer's empty state (the keyboard path).
//   · variant "home" (Plan 9 review m1): the whole Home page is the drop target, with no hint and
//     no cards of its own (Home lists them); Home's header has <ImportDrawingButton /> for the
//     keyboard path.
//
// Only drags that carry files are taken; part / tab drags inside the app pass through untouched.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { parts } from "../../state/store";
import { Button } from "../common/Button";
import { ImportCard } from "./ImportCard";
import { importEntries, startImport } from "./state";
import "./imports.css";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.dxf,application/pdf,image/png,image/jpeg";

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");

function take(files: FileList | null | undefined, projectId: string | null): void {
  for (const f of Array.from(files ?? []).slice(0, 5)) startImport(f, projectId);
}

/** The import cards (newest first). */
export function ImportCards({ floating = false }: { floating?: boolean }): JSX.Element | null {
  const list = importEntries.value;
  if (!list.length) return null;
  return (
    <div class={`imp-stack${floating ? " imp-stack-float" : ""}`} data-import-cards>
      {list.map((e) => <ImportCard key={e.key} entry={e} />)}
    </div>
  );
}

/** A button that opens the file picker for a drawing (Home's header "Import drawing…"). */
export function ImportDrawingButton({ projectId = null, label = "Import drawing…" }: { projectId?: string | null; label?: string }): JSX.Element {
  return <ChooseFile projectId={projectId} label={label} />;
}

function ChooseFile({ projectId, label }: { projectId: string | null; label: string }): JSX.Element {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button onClick={() => input.current?.click()}>{label}</Button>
      <input ref={input} type="file" accept={ACCEPT} class="imp-file" tabIndex={-1} aria-hidden="true"
        data-drawing-input
        onChange={(e) => {
          const el = e.currentTarget;
          take(el.files, projectId);
          el.value = "";
        }} />
    </>
  );
}

function Tile({ projectId, cards }: { projectId: string | null; cards: boolean }): JSX.Element {
  const [over, setOver] = useState(false);
  return (
    <div class="imp-tile-wrap">
      <div class="imp-drop imp-drop-tile" data-drawing-drop="tile" data-over={over ? "" : undefined}
        onDragOver={(e) => { if (!hasFiles(e)) return; e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { if (!hasFiles(e)) return; e.preventDefault(); setOver(false); take(e.dataTransfer?.files, projectId); }}>
        <p class="imp-drop-title">Drop a drawing to make a part</p>
        <p class="imp-drop-sub">PDF, PNG, JPG or DXF · up to 20 MB</p>
        <ChooseFile projectId={projectId} label="Choose a file…" />
      </div>
      {cards && <ImportCards />}
    </div>
  );
}

function Overlay({ projectId, page = false }: { projectId: string | null; page?: boolean }): JSX.Element {
  const anchor = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);
  useEffect(() => {
    const host = anchor.current?.parentElement;
    if (!host) return;
    let depth = 0;
    const enter = (e: DragEvent) => { if (!hasFiles(e)) return; depth += 1; setOver(true); };
    const overFn = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const leave = (e: DragEvent) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) setOver(false); };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setOver(false);
      take(e.dataTransfer?.files, projectId);
    };
    host.addEventListener("dragenter", enter);
    host.addEventListener("dragover", overFn);
    host.addEventListener("dragleave", leave);
    host.addEventListener("drop", drop);
    return () => {
      host.removeEventListener("dragenter", enter);
      host.removeEventListener("dragover", overFn);
      host.removeEventListener("dragleave", leave);
      host.removeEventListener("drop", drop);
    };
  }, [projectId]);
  return (
    <div ref={anchor} class={page ? "imp-overlay-anchor imp-overlay-page" : "imp-overlay-anchor"}>
      {!page && !over && parts.value.length === 0 && (
        <div class="imp-empty-hint">
          <span class="faint">or drop a drawing to make a part</span>
          <ChooseFile projectId={projectId} label="Choose a drawing…" />
        </div>
      )}
      {over && (
        <div class="imp-drop imp-drop-overlay" data-drawing-drop="overlay" aria-hidden="true">
          <p class="imp-drop-title">Drop a drawing to make a part</p>
          <p class="imp-drop-sub">PDF, PNG, JPG or DXF · up to 20 MB</p>
        </div>
      )}
      {!page && <ImportCards floating />}
    </div>
  );
}

/** `cards={false}`: the host shows ImportCards itself (Home puts them in a full-width row — m2). */
export function DrawingDrop({ variant = "tile", projectId = null, cards = true }: { variant?: "tile" | "overlay" | "home"; projectId?: string | null; cards?: boolean }): JSX.Element {
  if (variant === "home") return <Overlay projectId={projectId} page />;
  return variant === "overlay" ? <Overlay projectId={projectId} /> : <Tile projectId={projectId} cards={cards} />;
}
