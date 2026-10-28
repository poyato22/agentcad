// Profile → Printers: the PrusaLink printers the Print page can send to. Keys go to the Keychain
// through the core (password input, never shown again); removing asks inside the panel.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { PrintCapabilities, Printer } from "../../../api/types";
import { printersChanged } from "../../../state/printers";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { LAN_NOTE, PrinterForm } from "../../pages/print/PrinterForm";
import { printerLight } from "../../pages/print/printLogic";
import { usePrinterStatus } from "../../pages/print/usePrint";
import "../../pages/print/print.css";
import { Card, ConfirmRemove, describeError, ErrorLine, SectionBox, useTask } from "../common";

export function PrintersSection(): JSX.Element {
  const [list, setList] = useState<Printer[] | null>(null);
  const [caps, setCaps] = useState<PrintCapabilities | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let live = true;
    const c = actions.client();
    c.printers().then((p) => { if (live) setList(p); }, (e: unknown) => { if (live) { setList([]); setLoadError(describeError(e)); } });
    c.printCapabilities().then((x) => { if (live) setCaps(x); }, () => {});
    return () => { live = false; };
  }, []);

  return (
    <SectionBox title="Printers">
      <Card title="PrusaLink printers">
        <p class="dim">Targets for the Print page. API keys are stored in the macOS Keychain.</p>
        {list === null && <div class="faint">loading…</div>}
        <ErrorLine error={loadError} />
        {list !== null && list.length === 0 && <div class="faint">No printers.</div>}
        {list !== null && list.length > 0 && (
          <table class="profile-table">
            <thead><tr><th class="label">Printer</th><th class="label">Host</th><th class="label">Key</th><th class="label">Status</th><th /></tr></thead>
            <tbody>
              {list.map((p) => <PrinterRow key={p.id} printer={p} onRemoved={() => { setList((l) => (l ?? []).filter((x) => x.id !== p.id)); printersChanged(); }} />)}
            </tbody>
          </table>
        )}
        {adding
          ? <PrinterForm caps={caps} onCancel={() => setAdding(false)}
              onAdded={(p) => { setList((l) => [...(l ?? []).filter((x) => x.id !== p.id), p]); setAdding(false); printersChanged(); }} />
          : (<>
            <div class="profile-row"><Button onClick={() => setAdding(true)}>Add printer</Button></div>
            <p class="faint">{LAN_NOTE}</p>
          </>)}
        <p class="faint">Bambu: not supported yet.</p>
      </Card>
    </SectionBox>
  );
}

function PrinterRow({ printer, onRemoved }: { printer: Printer; onRemoved: () => void }): JSX.Element {
  const { status, stale } = usePrinterStatus(printer.id);
  const light = printerLight(status, stale);
  const [confirm, setConfirm] = useState(false);
  const task = useTask();
  async function remove(): Promise<void> {
    if (await task.run(async () => { await actions.client().deletePrinter(printer.id); })) onRemoved();
  }
  return (
    <tr>
      <td class="text">{printer.name} · {printer.model}</td>
      <td class="text">{printer.host}</td>
      <td>{printer.has_key ? <span>saved ●●●●</span> : <span class="faint">no key</span>}</td>
      <td><span class="profile-status"><Led tone={light.tone} /><span class="text">{light.text}</span></span></td>
      <td class="profile-actions">
        {confirm
          ? <ConfirmRemove question={`Remove ${printer.name}?`} busy={task.busy} onYes={() => { void remove(); }} onKeep={() => setConfirm(false)} />
          : <Button variant="ghost" onClick={() => setConfirm(true)}>Remove</Button>}
        <ErrorLine error={task.error} />
      </td>
    </tr>
  );
}
