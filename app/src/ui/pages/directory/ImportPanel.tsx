// "Import from link" and the review form (also used by Edit on an imported item).
//
// The core saves an imported item the moment it reads the page (C5 ruling), so this panel is a
// review of something already stored: Save sends only the changed fields (PATCH), Discard deletes
// the item this import just added (DELETE). A link that was already in the directory comes back
// unchanged (`existing`); Discard never deletes that one, and only "Read the page again" asks the
// core to re-read it (`refresh: true`, which replaces the extracted specs and drops earlier edits).
//
// The page text is untrusted: it is shown in a <pre> as plain text and nothing in it is followed.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { CatalogItem } from "../../../api/directory";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { describeError } from "../../profile/common";
import { CATEGORY_LABELS, draftFrom, editDiff, httpsLink, parseDraft, type ReviewDraft } from "./model";

export function ImportPanel({ review, onClose, onChanged, onSaved }: {
  /** Edit an item already in the directory (no import step). */
  review?: CatalogItem;
  onClose: () => void;
  /** The directory changed (an item was added or removed). */
  onChanged: () => void;
  /** Saved: show this item. */
  onSaved: (id: string) => void;
}): JSX.Element {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<null | "fetch" | "refresh" | "save" | "discard">(null);
  const [error, setError] = useState<string | null>(null);
  const [item, setItem] = useState<CatalogItem | null>(review ?? null);
  /** This import added the item (so Discard deletes it). */
  const [added, setAdded] = useState(false);
  /** Bumped per page read, so the form restarts from what was read. */
  const [ver, setVer] = useState(0);
  const urlRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!review) urlRef.current?.focus(); }, []);

  async function fetchPage(refresh = false): Promise<void> {
    const target = refresh && item?.url ? item.url : url.trim();
    if (!httpsLink(target)) { setError("Paste an https:// link to a product page."); return; }
    setBusy(refresh ? "refresh" : "fetch");
    setError(null);
    try {
      const it = await actions.client().catalogImport(refresh ? { url: target, refresh: true } : { url: target });
      setItem(it);
      setVer((v) => v + 1);
      if (!refresh) setAdded(!it.existing);
      onChanged();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  }

  async function discard(): Promise<void> {
    if (!item || !added) { onClose(); return; }
    setBusy("discard");
    try {
      await actions.client().catalogDelete(item.id);
      onChanged();
      onClose();
    } catch (e) {
      setError(describeError(e));
      setBusy(null);
    }
  }

  return (
    <div class="dir-import" data-import>
      <div class="dir-detail-head">
        <h2 class="dir-detail-name">{review ? "Edit part" : "Import from link"}</h2>
        <Button variant="ghost" aria-label="Close" onClick={onClose}>×</Button>
      </div>
      {!review && (
        <form class="dir-import-url" onSubmit={(e) => { e.preventDefault(); void fetchPage(); }}>
          <input ref={urlRef} type="url" aria-label="Product page link" placeholder="https://shop.example.com/motor-2207" value={url}
            disabled={busy !== null || item !== null} onInput={(e) => setUrl(e.currentTarget.value)} />
          {item === null && <Button variant="ink" type="submit" disabled={busy !== null || !url.trim()}>{busy === "fetch" ? "Reading the page…" : "Read the page"}</Button>}
        </form>
      )}
      {!review && item === null && (
        <p class="dir-note dim">AgentCAD reads the page (https only, up to 2 MB, 10 s), pulls out the specs and saves the part to the directory. You can correct it next.</p>
      )}
      {error && <p class="dir-err" role="alert">{error}</p>}
      {item && !review && (
        <p class="dir-note" role="status" data-import-state={item.existing ? "existing" : "added"}>
          {item.existing
            ? "This link is already in the directory, so it was not read again. Below is the saved part, your earlier edits included."
            : "Saved to the directory. Check what was read, correct anything wrong, then Save. Discard removes it again."}
        </p>
      )}
      {item && (
        <ReviewForm key={`${item.id}:${ver}`} item={item} busy={busy}
          canDiscard={added} onDiscard={() => { void discard(); }} onCancel={onClose}
          onSave={async (patch) => {
            setBusy("save");
            setError(null);
            try {
              if (Object.keys(patch).length > 0) await actions.client().catalogEdit(item.id, patch);
              onSaved(item.id);
            } catch (e) {
              setError(describeError(e));
              setBusy(null);
            }
          }}
          onRefresh={item.existing && item.url ? () => { void fetchPage(true); } : null} />
      )}
    </div>
  );
}

function ReviewForm({ item, busy, canDiscard, onSave, onDiscard, onCancel, onRefresh }: {
  item: CatalogItem; busy: string | null; canDiscard: boolean;
  onSave: (patch: ReturnType<typeof editDiff>) => Promise<void>; onDiscard: () => void; onCancel: () => void;
  onRefresh: (() => void) | null;
}): JSX.Element {
  const [d, setD] = useState<ReviewDraft>(() => draftFrom(item));
  const [errors, setErrors] = useState<string[]>([]);
  const set = (patch: Partial<ReviewDraft>) => setD((cur) => ({ ...cur, ...patch }));
  const setSpec = (i: number, patch: Partial<ReviewDraft["specs"][number]>) =>
    set({ specs: d.specs.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const setDim = (i: number, patch: Partial<ReviewDraft["dims"][number]>) =>
    set({ dims: d.dims.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  function save(): void {
    const { fields, errors: errs } = parseDraft(d);
    setErrors(errs);
    if (errs.length) return;
    void onSave(editDiff(item, fields));
  }

  const host = httpsLink(item.url)?.host;
  return (
    <form class="dir-review" aria-label="Review the part" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <label class="dir-field"><span>Name</span>
        <input value={d.name} maxLength={200} onInput={(e) => set({ name: e.currentTarget.value })} />
      </label>
      <div class="dir-field-row">
        <label class="dir-field"><span>Category</span>
          <select value={d.category} onChange={(e) => set({ category: e.currentTarget.value })}>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            {!(d.category in CATEGORY_LABELS) && <option value={d.category}>{d.category}</option>}
          </select>
        </label>
        <label class="dir-field"><span>Vendor</span>
          <input value={d.vendor} maxLength={200} onInput={(e) => set({ vendor: e.currentTarget.value })} />
        </label>
      </div>

      <fieldset class="dir-rows">
        <legend>Specs</legend>
        {d.specs.map((s, i) => (
          <div class="dir-row" key={i} data-spec-row={i}>
            <input aria-label={`Spec ${i + 1} name`} value={s.key} maxLength={80} onInput={(e) => setSpec(i, { key: e.currentTarget.value })} />
            <input aria-label={`Spec ${i + 1} value`} value={s.value} maxLength={200} onInput={(e) => setSpec(i, { value: e.currentTarget.value })} />
            <input aria-label={`Spec ${i + 1} unit`} class="dir-unit" value={s.unit} maxLength={20} onInput={(e) => setSpec(i, { unit: e.currentTarget.value })} />
            <Button variant="ghost" aria-label={`Remove spec ${i + 1}`} onClick={() => set({ specs: d.specs.filter((_, j) => j !== i) })}>×</Button>
          </div>
        ))}
        <Button variant="ghost" onClick={() => set({ specs: [...d.specs, { key: "", value: "", unit: "" }] })}>Add spec</Button>
      </fieldset>

      <fieldset class="dir-rows">
        <legend>Dimensions (mm, g)</legend>
        {d.dims.map((x, i) => (
          <div class="dir-row dir-row-dim" key={i}>
            <input aria-label={`Dimension ${i + 1} name`} value={x.key} placeholder="length_mm" maxLength={40} onInput={(e) => setDim(i, { key: e.currentTarget.value })} />
            <input aria-label={`Dimension ${i + 1} value`} value={x.value} inputMode="decimal" onInput={(e) => setDim(i, { value: e.currentTarget.value })} />
            <Button variant="ghost" aria-label={`Remove dimension ${i + 1}`} onClick={() => set({ dims: d.dims.filter((_, j) => j !== i) })}>×</Button>
          </div>
        ))}
        <Button variant="ghost" onClick={() => set({ dims: [...d.dims, { key: "", value: "" }] })}>Add dimension</Button>
        <p class="dir-note faint">Add mass_g to state the mass. Without it, inserting the part estimates the mass from its volume.</p>
      </fieldset>

      {errors.length > 0 && <ul class="dir-err" role="alert">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}

      <div class="dir-actions">
        <Button variant="ink" type="submit" disabled={busy !== null}>{busy === "save" ? "Saving…" : "Save"}</Button>
        {canDiscard
          ? <Button variant="danger" disabled={busy !== null} onClick={onDiscard}>{busy === "discard" ? "Discarding…" : "Discard"}</Button>
          : <Button variant="ghost" disabled={busy !== null} onClick={onCancel}>Cancel</Button>}
        {onRefresh && (
          <Button variant="ghost" disabled={busy !== null} title="Replaces the specs read before; your earlier edits are dropped" onClick={onRefresh}>
            {busy === "refresh" ? "Reading…" : "Read the page again"}
          </Button>
        )}
      </div>

      {item.raw_text !== undefined && (
        <details class="dir-raw" open={!item.edited_by_user}>
          <summary>Page text{host ? ` from ${host}` : ""}, shown as plain text</summary>
          <p class="dir-note faint">This is what the page said. AgentCAD and its agents treat it as data and never follow instructions in it.</p>
          <pre class="dir-raw-text" data-raw-text>{item.raw_text}</pre>
        </details>
      )}
    </form>
  );
}
