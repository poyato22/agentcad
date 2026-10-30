// The user's script edits for one part (PUT /parts/{id}/source {source, base_version}, C6). The
// editor always starts from a fresh GET /source and saves against the version it read. A 409
// means someone (usually the agent) changed the part since: the user chooses to load theirs or
// keep mine — keep mine re-reads the source first (the contract's fresh read) and then writes the
// user's text on top of that version. A version bump seen while the text is unchanged just
// reloads; while it has unsaved changes the editor says so before the save is refused.
import { buildErrorLine, editConflictSchema, type PartEditResult, type PartSource } from "../../api/builds";
import { CoreError, type CoreClient } from "../../api/client";
import { trackEdit } from "../../state/partEdits";

export type SourceStatus =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "saving" }
  | { kind: "saved"; version: number }
  | { kind: "behind"; current: number }                    // changed elsewhere; you have unsaved edits
  /** `current`: the version the notice names (null: the 409 didn't say; only Load theirs). */
  | { kind: "conflict"; current: number | null }
  | { kind: "error"; message: string };

export interface SourceDeps {
  client: () => Pick<CoreClient, "partSource" | "putPartSource">;
  onSaved(r: PartEditResult): void;
}

export class SourceSession {
  readonly partId: string;
  status: SourceStatus = { kind: "loading" };
  /** The editor's text now. */
  text = "";
  /** The text as last read from / saved to the core, at `base`. */
  loaded = "";
  base: number | null = null;
  private readonly deps: SourceDeps;
  private readonly listeners = new Set<(reload: boolean) => void>();

  constructor(partId: string, deps: SourceDeps) {
    this.partId = partId;
    this.deps = deps;
  }

  subscribe(cb: (reload: boolean) => void): () => void { this.listeners.add(cb); return () => { this.listeners.delete(cb); }; }
  /** `reload`: the text was replaced from the core (the editor swaps its document). */
  private changed(reload = false): void { for (const cb of [...this.listeners]) cb(reload); }

  get dirty(): boolean { return this.base !== null && this.text !== this.loaded; }

  private take(s: PartSource): void {
    this.base = s.version;
    this.loaded = s.source;
    this.text = s.source;
  }

  /** Fresh read; replaces the text. */
  async open(): Promise<void> {
    this.status = { kind: "loading" };
    this.changed();
    try {
      this.take(await this.deps.client().partSource(this.partId));
      this.status = { kind: "ready" };
      this.changed(true);
    } catch (e) {
      this.status = { kind: "error", message: `Couldn't read the script: ${e instanceof Error ? e.message : String(e)}` };
      this.changed();
    }
  }

  edit(text: string): void {
    this.text = text;
    if (this.status.kind === "saved" || this.status.kind === "error") this.status = { kind: "ready" };
    this.changed();
  }

  async save(): Promise<void> {
    if (!this.dirty || this.base === null || this.status.kind === "saving" || this.status.kind === "conflict") return;
    const sent = this.text;
    this.status = { kind: "saving" };
    this.changed();
    try {
      const r = await trackEdit(this.partId, this.deps.client().putPartSource(this.partId, sent, this.base));
      this.deps.onSaved(r);
      // the core may rewrite the script after a build (refreshed op references): re-read it,
      // unless the user typed on meanwhile
      const fresh = await this.deps.client().partSource(this.partId).catch(() => null);
      if (this.text === sent) {
        if (fresh && fresh.version >= r.version) this.take(fresh);
        else { this.base = r.version; this.loaded = sent; }
        this.status = { kind: "saved", version: this.base ?? r.version };
        this.changed(fresh !== null && fresh.source !== sent);
      } else {
        this.base = r.version;
        this.loaded = sent;
        this.status = { kind: "ready" };
        this.changed();
      }
    } catch (e) {
      if (e instanceof CoreError && e.status === 409) {
        const c = editConflictSchema.safeParse(e.body ?? {});
        this.status = { kind: "conflict", current: c.success ? c.data.current_version : null };
      } else if (e instanceof CoreError && e.status === 422) {
        this.status = { kind: "error", message: `The part didn't rebuild, nothing changed: ${buildErrorLine(e.message)}` };
      } else {
        this.status = { kind: "error", message: e instanceof Error ? e.message : String(e) };
      }
      this.changed();
    }
  }

  /** After a conflict: drop mine, load the current script. */
  async loadTheirs(): Promise<void> { await this.open(); }

  /** After a conflict: write my text over exactly the version the notice named. The fresh read
   *  (the contract's) must still be at that version; if the part moved on again, that is a new
   *  conflict the user sees, never an unseen overwrite. */
  async keepMine(): Promise<void> {
    if (this.status.kind !== "conflict" || this.status.current === null) return;
    const named = this.status.current;
    const mine = this.text;
    try {
      const s = await this.deps.client().partSource(this.partId);
      if (s.version !== named) {
        this.status = { kind: "conflict", current: s.version };
        this.changed();
        return;
      }
      this.base = s.version;
      this.loaded = s.source;
      this.text = mine;
      this.status = { kind: "ready" };
      this.changed();
      await this.save();
    } catch (e) {
      this.status = { kind: "error", message: e instanceof Error ? e.message : String(e) };
      this.changed();
    }
  }

  /** The part moved to `version` elsewhere (part_changed / the part list). */
  external(version: number): void {
    if (this.base === null || version <= this.base || this.status.kind === "saving" || this.status.kind === "loading") return;
    if (this.status.kind === "conflict") return;
    if (!this.dirty) { void this.open(); return; }
    this.status = { kind: "behind", current: version };
    this.changed();
  }
}
