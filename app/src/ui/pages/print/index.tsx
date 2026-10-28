// Print page: printer (PrusaLink) with its status light → part + material → Slice (PrusaSlicer
// in the core, cancellable) → preview, time, grams, cost → "Send to printer".
//
// Print safety: sending is irreversible. "Send to printer" only asks the core to start the
// print_send call; the core raises an approval card in the agent console (opened and pointed at
// that thread here) and uploads nothing until the user approves it on the card. The button is
// disabled unless a finished slice for exactly this part / material / printer is shown and the
// printer reports a sendable state (never while it reports an error); the core checks the state
// again before the card and right before the upload. A missing slicer shows the install command.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { CoreError } from "../../../api/client";
import type { PrintCapabilities, Printer } from "../../../api/types";
import { fmtUsd } from "../../../lib/format";
import { partLabel } from "../../../lib/names";
import { activePart } from "../../../state/selection";
import { actions } from "../../../state/store";
import { printersChanged } from "../../../state/printers";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { describeError } from "../../profile/common";
import { PartPicker } from "../drawing/PartPicker";
import "../drawing/drawing.css";
import { PageShell } from "../PageShell";
import { setConsoleCollapsed } from "../router";
import { LAN_NOTE, PrinterForm } from "./PrinterForm";
import { printerLight, printOutcome, sendBlock } from "./printLogic";
import { currentPrint, currentSlice, POLL, usePoll, usePrinterStatus } from "./usePrint";
import "./print.css";

const INSTALL_FALLBACK = "brew install --cask prusaslicer";

export function PrintPage(): JSX.Element {
  const part = activePart.value;
  const partId = part?.part_id ?? null;
  const [caps, setCaps] = useState<PrintCapabilities | null>(null);
  const [capsAttempt, setCapsAttempt] = useState(0);
  const [printers, setPrinters] = useState<Printer[] | null>(null);
  const [printersError, setPrintersError] = useState<string | null>(null);
  const [printerId, setPrinterId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [material, setMaterial] = useState<string>("");
  const [slicerMissing, setSlicerMissing] = useState<string | null>(null);
  const [sliceError, setSliceError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const slice = currentSlice.value;
  const request = currentPrint.value;

  useEffect(() => {
    let live = true;
    actions.client().printCapabilities().then((c) => { if (live) setCaps(c); }, () => { if (live) setCaps(null); });
    return () => { live = false; };
  }, [capsAttempt]);

  useEffect(() => {
    let live = true;
    const c = actions.client();
    c.printers().then(
      (list) => {
        if (!live) return;
        setPrinters(list);
        setPrinterId((cur) => (cur && list.some((p) => p.id === cur) ? cur : list[0]?.id ?? null));
      },
      (e: unknown) => { if (live) { setPrinters([]); setPrintersError(describeError(e)); } },
    );
    c.getProfile().then((p) => { if (live) setMaterial((m) => m || p.default_material.toUpperCase()); }, () => {});
    return () => { live = false; };
  }, []);

  // without a profile answer, fall back to the catalogue's first material
  useEffect(() => { if (!material && caps?.materials[0]) setMaterial(caps.materials[0].name); }, [caps, material]);

  const printer = printers?.find((p) => p.id === printerId) ?? null;
  const { status, stale } = usePrinterStatus(printer?.id ?? null);
  const light = printerLight(status, stale);
  const slicerInstalled = caps ? caps.slicer.installed : true;
  const installCommand = caps?.slicer.install_command ?? INSTALL_FALLBACK;
  const showInstall = !slicerInstalled || slicerMissing !== null;
  const slicing = slice?.status === "running";
  const waiting = starting || request?.call?.status === "running" || (request !== null && request.call === null);

  // poll the slice while it runs
  usePoll(slice?.slice_id ?? null, slicing, () => POLL.sliceMs, async () => {
    const sid = currentSlice.peek()?.slice_id;
    if (!sid) return false;
    const s = await actions.client().getSlice(sid);
    if (currentSlice.peek()?.slice_id !== sid) return false;
    currentSlice.value = s;
    return s.status === "running";
  });

  // poll the print request until its card is decided and the call ends
  usePoll(request?.start.call_id ?? null, waiting && !!request, () => POLL.printMs, async () => {
    const req = currentPrint.peek();
    if (!req) return false;
    const call = await actions.client().getPrint(req.start.call_id);
    if (currentPrint.peek()?.start.call_id !== req.start.call_id) return false;
    currentPrint.value = { ...req, call };
    if (call.status !== "running") {
      const s = currentSlice.peek();
      if (s) actions.client().getSlice(s.slice_id).then((x) => { if (currentSlice.peek()?.slice_id === x.slice_id) currentSlice.value = x; }, () => {});
      return false;
    }
    return true;
  });

  // the slice's preview thumbnail (PNG from the G-code), as a blob: URL
  const previewKey = slice?.status === "done" && slice.preview ? slice.slice_id : null;
  useEffect(() => {
    setPreviewUrl(null);
    if (!previewKey) return;
    let live = true;
    let made: string | null = null;
    actions.client().slicePreview(previewKey).then((b) => {
      if (!live) return;
      made = URL.createObjectURL(b);
      setPreviewUrl(made);
    }, () => {});
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [previewKey]);

  async function startSlice(): Promise<void> {
    if (!printer || !partId || slicing) return;
    setSliceError(null);
    setSendError(null);
    try {
      const s = await actions.client().startSlice({ part_id: partId, printer_id: printer.id, material: material || null });
      currentPrint.value = null;
      currentSlice.value = {
        slice_id: s.slice_id, status: "running", part_id: partId, printer_id: printer.id, material: material.toUpperCase(), model: printer.model,
        created_at: Date.now() / 1000, estimate: null, preview: false, error: null, hint: null, sent_to: null,
      };
    } catch (e) {
      if (e instanceof CoreError && e.status === 424) setSlicerMissing(e.hint ?? `install it with: ${installCommand}`);
      else setSliceError(describeError(e));
    }
  }

  async function cancelSlice(): Promise<void> {
    const s = currentSlice.peek();
    if (!s || s.status !== "running") return;
    try {
      await actions.client().cancelSlice(s.slice_id);
    } catch (e) {
      setSliceError(describeError(e));
    }
  }

  const block = sendBlock({ slice, printer, status, partId, material: material.toUpperCase(), busy: waiting });

  async function send(): Promise<void> {
    if (block !== null || !slice || !printer) return;
    setSendError(null);
    setStarting(true);
    try {
      // re-check with a fresh status read at click time: the shown one may be seconds old
      const fresh = await actions.client().printerStatus(printer.id);
      const why = sendBlock({ slice, printer, status: fresh, partId, material: material.toUpperCase(), busy: false });
      if (why !== null) {
        setSendError(why);
        setStarting(false);
        return;
      }
      const start = await actions.client().startPrint(slice.slice_id, printer.id);
      currentPrint.value = { start, call: null };
      // the approval card is in this run's thread in the console
      setConsoleCollapsed(false);
      actions.selectRun(start.run_id);
    } catch (e) {
      setSendError(describeError(e));
    }
    setStarting(false);
  }

  const onAdded = (p: Printer) => {
    setPrinters((list) => [...(list ?? []).filter((x) => x.id !== p.id), p]);
    setPrinterId(p.id);
    setAdding(false);
    printersChanged();
  };

  const noPrinters = printers !== null && printers.length === 0;
  const est = slice?.status === "done" ? slice.estimate : null;
  const outcome = request?.call ? printOutcome(request.call) : null;

  return (
    <PageShell title="Print">
      <div class="print">
        {showInstall && (
          <section class="pr-card pr-install" role="region" aria-label="Install PrusaSlicer">
            <h2 class="pr-card-title">PrusaSlicer is not installed</h2>
            <p class="dim">Slicing needs PrusaSlicer. Install it, then check again:</p>
            <code class="pr-code text">{installCommand}</code>
            <div class="pr-row">
              <Button onClick={() => { setSlicerMissing(null); setCapsAttempt((n) => n + 1); }}>Check again</Button>
            </div>
          </section>
        )}

        <section class="pr-card" role="region" aria-label="Printer">
          <h2 class="pr-card-title">Printer</h2>
          {printers === null && <div class="faint">loading printers…</div>}
          {printersError && <div class="pg-error text" role="alert">{printersError}</div>}
          {printers !== null && printers.length > 0 && (
            <div class="pr-row">
              <label class="pg-field">
                <span class="label">Printer</span>
                <select aria-label="Printer" value={printerId ?? ""} onChange={(e) => setPrinterId(e.currentTarget.value)}>
                  {printers.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}
                </select>
              </label>
              {printer && (
                <div class="pr-status" role="status" aria-label="Printer status">
                  <Led tone={light.tone} />
                  <span class="text">{light.text}</span>
                  {status?.hint && !status.reachable && <span class="faint text"> — {status.hint}</span>}
                </div>
              )}
              {!adding && <Button variant="ghost" onClick={() => setAdding(true)}>Add printer</Button>}
            </div>
          )}
          {printer && <div class="faint text">{printer.host} · PrusaLink{printer.has_key ? " · key saved in Keychain" : " · no key saved"}</div>}
          {(noPrinters || adding) && (
            <PrinterForm caps={caps} onAdded={onAdded} onCancel={noPrinters ? undefined : () => setAdding(false)} />
          )}
          {!noPrinters && !adding && <p class="faint pr-note">{LAN_NOTE}</p>}
          <p class="faint pr-note">Bambu: not supported yet.</p>
        </section>

        <section class="pr-card" role="region" aria-label="Slice">
          <h2 class="pr-card-title">Slice</h2>
          <div class="pg-toolbar">
            <PartPicker disabled={slicing} />
            <label class="pg-field">
              <span class="label">Material</span>
              <select aria-label="Material" value={material} disabled={slicing} onChange={(e) => setMaterial(e.currentTarget.value)}>
                {(caps?.materials ?? [{ name: material || "PLA", density_g_cm3: 0, usd_per_kg: 0 }]).map((m) => (
                  <option key={m.name} value={m.name}>{m.name}{m.usd_per_kg ? ` · ${fmtUsd(m.usd_per_kg)}/kg` : ""}</option>
                ))}
              </select>
            </label>
            <Button variant="ink" disabled={!printer || !partId || slicing || showInstall} onClick={() => { void startSlice(); }}>Slice</Button>
            {slicing && <Button variant="ghost" onClick={() => { void cancelSlice(); }}>Cancel slice</Button>}
          </div>
          {!part && <p class="faint">No parts. Build one on the Design page.</p>}
          {slicing && <div class="dim" role="status" aria-label="Slicing">slicing {partLabel(slice.part_id)} in PrusaSlicer…</div>}
          {sliceError && <div class="pg-error text" role="alert">{sliceError}</div>}
          {slice?.status === "failed" && (
            <div class="pg-error text" role="alert">Slicing failed: {slice.error ?? "unknown error"}{slice.hint ? ` — ${slice.hint}` : ""}</div>
          )}
          {slice?.status === "cancelled" && <div class="dim">Slice cancelled.</div>}
        </section>

        <section class="pr-card pr-result" role="region" aria-label="Slice result">
          <h2 class="pr-card-title">Result</h2>
          {!est && <p class="faint">Slice for preview, print time, filament and cost.</p>}
          {est && slice && (
            <div class="pr-result-body">
              <div class="pr-preview">
                {previewUrl
                  ? <img src={previewUrl} alt={`Preview · ${partLabel(slice.part_id)}`} />
                  : <span class="faint">{slice.preview ? "loading preview…" : "no preview image"}</span>}
              </div>
              <dl class="pr-facts">
                <dt class="label">Part</dt><dd class="text">{partLabel(slice.part_id)}</dd>
                <dt class="label">Material</dt><dd class="text">{slice.material}</dd>
                <dt class="label">Printer</dt><dd class="text">{slice.model}</dd>
                <dt class="label">Time</dt><dd class="text">{est.time_text}</dd>
                <dt class="label">Filament</dt><dd>{est.filament_g.toFixed(2)} g · {(est.filament_mm / 1000).toFixed(2)} m</dd>
                <dt class="label">Cost</dt><dd>{fmtUsd(est.cost_usd)}</dd>
              </dl>
            </div>
          )}
          <div class="pr-send">
            <Button variant="ink" disabled={block !== null} onClick={() => { void send(); }}>Send to printer</Button>
            {block && <span class="dim text">{block}</span>}
            {!block && <span class="dim">Irreversible: printing starts when you approve the card in the console.</span>}
          </div>
          {sendError && <div class="pg-error text" role="alert">{sendError}</div>}
          {request && (
            <div class="pr-request" role="status" aria-label="Print request">
              {!outcome && (<><Led tone="blue" /><span>Approve or reject the card in the console. Nothing is sent until then.</span></>)}
              {outcome && (<><Led tone={outcome.tone === "dim" ? "faint" : outcome.tone} /><span class="text">{outcome.text}</span></>)}
            </div>
          )}
        </section>
      </div>
    </PageShell>
  );
}
