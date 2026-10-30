// The user's variable edits for one part: slider / field changes are debounced into one
// POST /parts/{id}/vars {vars, base_version}. `base_version` is the version the user started
// from, NOT the newest one seen: an agent edit that lands while the user is dragging must come
// back as a 409 ("Agent changed this part — Reload / Keep mine"), never be silently overwritten.
// One request at a time; changes made meanwhile go out next, on top of the version it returned.
import { buildErrorLine, editConflictSchema, type PartEditResult } from "../../api/builds";
import { CoreError, type CoreClient } from "../../api/client";
import { trackEdit } from "../../state/partEdits";

export type VarsStatus =
  | { kind: "idle" }
  | { kind: "pending" }                       // waiting out the debounce
  | { kind: "saving" }
  /** `current` null: the 409 didn't say which version is current (Reload is the only way on). */
  | { kind: "conflict"; current: number | null; theirs: Record<string, number> | null }
  | { kind: "error"; message: string };

export interface SavedEdit { result: PartEditResult; sent: Record<string, number>; prev: Record<string, number | null> }

export interface VarsEditorDeps {
  client: () => Pick<CoreClient, "setPartVars">;
  /** Called after each accepted edit (the edit line, the version note). */
  onSaved(e: SavedEdit): void;
  debounceMs?: number;
  setTimer?: (cb: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

export const VARS_DEBOUNCE_MS = 350;

export class VarsEditor {
  readonly partId: string;
  status: VarsStatus = { kind: "idle" };
  /** Values the user set that the core hasn't confirmed yet. */
  readonly drafts = new Map<string, number>();
  /** Values the core confirmed, shown until the part list catches up to `savedVersion`. */
  saved: Record<string, number> = {};
  savedVersion = 0;
  private base: number | null = null;
  private prev = new Map<string, number | null>();
  private inflight: Record<string, number> | null = null;
  private timer: unknown = null;
  private disposed = false;
  private readonly listeners = new Set<() => void>();
  private readonly deps: Required<Omit<VarsEditorDeps, "onSaved" | "client">> & Pick<VarsEditorDeps, "onSaved" | "client">;

  constructor(partId: string, deps: VarsEditorDeps) {
    this.partId = partId;
    this.deps = {
      debounceMs: VARS_DEBOUNCE_MS,
      setTimer: (cb, ms) => setTimeout(cb, ms),
      clearTimer: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
      ...deps,
    };
  }

  subscribe(cb: () => void): () => void { this.listeners.add(cb); return () => { this.listeners.delete(cb); }; }
  private changed(): void { for (const cb of [...this.listeners]) cb(); }

  /** Is anything of the user's not yet saved (drafts, in flight, or a conflict waiting)? */
  get busy(): boolean { return this.drafts.size > 0 || this.inflight !== null || this.status.kind === "conflict"; }

  /** The user moved a slider / typed a value. `version` is the version of the values on screen
   *  (the part list row, never a newer version only heard of: an edit landed since must come back
   *  as a 409) and `current` those values (for the "4 → 5" line). While the list still lags behind
   *  the user's own last save, the screen shows that save's values, so that version is the base. */
  set(name: string, value: number, version: number, current: Record<string, number>): void {
    if (this.disposed || !Number.isFinite(value)) return;
    if (!this.busy) this.base = Math.max(version, this.savedVersion);
    if (!this.prev.has(name)) this.prev.set(name, name in this.saved ? this.saved[name] : name in current ? current[name] : null);
    this.drafts.set(name, value);
    if (this.status.kind !== "conflict") this.status = { kind: "pending" };
    this.schedule();
    this.changed();
  }

  private schedule(): void {
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.timer = this.deps.setTimer(() => { this.timer = null; void this.send(); }, this.deps.debounceMs);
  }

  /** Send now (Enter in a field, or after the debounce). */
  async send(): Promise<void> {
    if (this.disposed || this.inflight || this.drafts.size === 0 || this.status.kind === "conflict" || this.base === null) return;
    if (this.timer !== null) { this.deps.clearTimer(this.timer); this.timer = null; }
    const sent = Object.fromEntries(this.drafts);
    const prev = Object.fromEntries([...Object.keys(sent)].map((k) => [k, this.prev.get(k) ?? null]));
    this.inflight = sent;
    this.status = { kind: "saving" };
    this.changed();
    try {
      const result = await trackEdit(this.partId, this.deps.client().setPartVars(this.partId, sent, this.base));
      if (this.disposed) return;
      this.inflight = null;
      this.base = result.version;
      this.saved = { ...this.saved, ...result.vars };
      this.savedVersion = result.version;
      for (const [k, v] of Object.entries(sent)) {
        if (this.drafts.get(k) === v) { this.drafts.delete(k); this.prev.delete(k); }
        else this.prev.set(k, v);                   // changed again meanwhile: "5 → 6" next
      }
      this.deps.onSaved({ result, sent, prev });
      this.status = this.drafts.size ? { kind: "pending" } : { kind: "idle" };
      this.changed();
      if (this.drafts.size) await this.send();
    } catch (e) {
      if (this.disposed) return;
      this.inflight = null;
      if (e instanceof CoreError && e.status === 409) {
        const c = editConflictSchema.safeParse(e.body ?? {});
        // an unreadable 409 never guesses a version to write on
        this.status = { kind: "conflict", current: c.success ? c.data.current_version : null, theirs: c.success ? c.data.vars : null };
      } else if (e instanceof CoreError && e.status === 422) {
        this.status = { kind: "error", message: `The part didn't rebuild: ${buildErrorLine(e.message)}` };
      } else {
        this.status = { kind: "error", message: e instanceof Error ? e.message : String(e) };
      }
      this.changed();
    }
  }

  /** After a 409: apply the user's values on top of the agent's version. */
  async keepMine(): Promise<void> {
    if (this.status.kind !== "conflict" || this.status.current === null) return;
    this.base = this.status.current;
    this.status = { kind: "pending" };
    this.changed();
    await this.send();
  }

  /** After a 409 or a failed build: drop the user's unsaved values (the part list shows the
   *  current state). */
  discard(): void {
    if (this.timer !== null) { this.deps.clearTimer(this.timer); this.timer = null; }
    this.drafts.clear();
    this.prev.clear();
    this.base = null;
    this.status = { kind: "idle" };
    this.changed();
  }

  /** The value to show: the user's draft, else the confirmed value while the list lags. */
  shown(name: string, partVersion: number, partValue: number | undefined): number | undefined {
    if (this.drafts.has(name)) return this.drafts.get(name);
    if (partVersion < this.savedVersion && name in this.saved) return this.saved[name];
    return partValue;
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.listeners.clear();
  }
}
