// Drawing imports in flight and finished, shared by every drop zone (Home's tile, a build's
// overlay) and the cards. Each upload is polled (GET /imports/{id}) until it is done or failed.
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../api/client";
import { DRAWING_EXTENSIONS, MAX_DRAWING_BYTES, type DrawingImport } from "../../api/imports";
import { loadParts } from "../../state/builds";
import { actions } from "../../state/store";

export interface ImportEntry {
  /** Local key (stable from the drop, before the core has answered). */
  key: string;
  fileName: string;
  rec: DrawingImport | null;
  /** The upload was refused, or polling failed for good. Message and hint are the core's. */
  error: { message: string; hint: string | null } | null;
}

export const importEntries: Signal<readonly ImportEntry[]> = signal<readonly ImportEntry[]>([]);

export const POLL_MS = { value: 1000 };

let seq = 0;
const timers = new Map<string, ReturnType<typeof setTimeout>>();

function update(key: string, patch: Partial<ImportEntry>): void {
  importEntries.value = importEntries.value.map((e) => (e.key === key ? { ...e, ...patch } : e));
}

const isFinal = (r: DrawingImport | null) => r !== null && (r.status === "done" || r.status === "failed");

/** Why a file can't be sent, checked before upload (the core checks the content again). */
export function precheck(file: { name: string; size: number }): string | null {
  const lower = file.name.toLowerCase();
  if (!DRAWING_EXTENSIONS.some((x) => lower.endsWith(x))) return "Use a PDF, PNG, JPG or DXF file.";
  if (file.size > MAX_DRAWING_BYTES) return "The file is larger than 20 MB.";
  if (file.size === 0) return "The file is empty.";
  return null;
}

function errorOf(e: unknown): { message: string; hint: string | null } {
  if (e instanceof CoreError) return { message: e.message, hint: e.hint };
  return { message: e instanceof Error ? e.message : String(e), hint: null };
}

function poll(key: string, id: string, misses = 0): void {
  timers.set(key, setTimeout(() => {
    timers.delete(key);
    if (!importEntries.peek().some((e) => e.key === key)) return;     // dismissed
    actions.client().drawingImport(id).then(
      (rec) => {
        update(key, { rec });
        if (!isFinal(rec)) poll(key, id);
        else if (rec.status === "done") void loadParts().catch(() => { /* the part list refreshes on its own too */ });
      },
      (e: unknown) => {
        if (misses >= 4) update(key, { error: errorOf(e) });
        else poll(key, id, misses + 1);
      },
    );
  }, POLL_MS.value));
}

/** Upload a dropped / chosen file and follow it. Returns the entry's key. */
export function startImport(file: File, projectId: string | null): string {
  const key = `drop-${++seq}`;
  const entry: ImportEntry = { key, fileName: file.name, rec: null, error: null };
  importEntries.value = [entry, ...importEntries.value];
  const bad = precheck(file);
  if (bad) {
    update(key, { error: { message: bad, hint: null } });
    return key;
  }
  actions.client().importDrawing(file, file.name, { projectId }).then(
    (rec) => {
      update(key, { rec });
      if (!isFinal(rec)) poll(key, rec.import_id);
    },
    (e: unknown) => update(key, { error: errorOf(e) }),
  );
  return key;
}

export function dismissImport(key: string): void {
  const t = timers.get(key);
  if (t !== undefined) clearTimeout(t);
  timers.delete(key);
  importEntries.value = importEntries.value.filter((e) => e.key !== key);
}

export function resetImports(): void {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  importEntries.value = [];
}
