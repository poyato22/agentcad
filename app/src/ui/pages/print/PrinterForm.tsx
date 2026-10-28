// "Add a printer" form, used on the Print page and in Profile → Printers. The API key is a
// password input: it goes out once in the POST body (the core puts it in the Keychain), is
// cleared right after, and is never rendered or returned.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { PrintCapabilities, Printer } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { describeError, SECRET_INPUT } from "../../profile/common";
import { validHost } from "./printLogic";

/** Shown wherever printers are added: PrusaLink has no TLS. */
export const LAN_NOTE = "PrusaLink is plain HTTP on your local network: the API key and the G-code travel unencrypted. Add printers only on a network you trust.";

const DEFAULT_KINDS: PrintCapabilities["kinds"] = [{ kind: "prusalink", available: true, note: null }, { kind: "bambu", available: false, note: "coming later" }];
const KIND_LABEL: Record<string, string> = { prusalink: "PrusaLink (Prusa MK4, MINI)", bambu: "Bambu" };

export function PrinterForm({ caps, onAdded, onCancel }: {
  caps: PrintCapabilities | null; onAdded: (p: Printer) => void; onCancel?: () => void;
}): JSX.Element {
  const models = caps?.models.length ? caps.models : ["MK4", "MINI"];
  const kinds = caps?.kinds.length ? caps.kinds : DEFAULT_KINDS;
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [model, setModel] = useState(models[0]);
  const [kind, setKind] = useState("prusalink");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hostOk = validHost(host);
  const can = !!name.trim() && hostOk && !!key.trim() && !busy;

  async function submit(e?: Event): Promise<void> {
    e?.preventDefault();
    if (!can) return;
    const secret = key.trim();
    setKey(""); // never kept in state past the request
    setBusy(true);
    setError(null);
    try {
      const p = await actions.client().addPrinter({ name: name.trim(), host: host.trim(), api_key: secret, model, kind });
      onAdded(p);
    } catch (err) {
      setError(describeError(err));
    }
    setBusy(false);
  }

  return (
    <form class="pr-form" aria-label="Add a printer" onSubmit={(e) => { void submit(e); }}>
      <div class="pr-form-grid">
        <label class="pg-field"><span class="label">Name</span>
          <input aria-label="Name" value={name} maxLength={60} placeholder="Bench MK4" onInput={(e) => setName(e.currentTarget.value)} />
        </label>
        <label class="pg-field"><span class="label">Host</span>
          <input aria-label="Host" value={host} placeholder="192.168.1.20" spellcheck={false} autocapitalize="off"
            onInput={(e) => setHost(e.currentTarget.value)} aria-invalid={host !== "" && !hostOk} />
        </label>
        <label class="pg-field"><span class="label">Kind</span>
          <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
            {kinds.map((k) => (
              <option key={k.kind} value={k.kind} disabled={!k.available}>
                {KIND_LABEL[k.kind] ?? k.kind}{!k.available && k.note ? ` · ${k.note}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label class="pg-field"><span class="label">Model</span>
          <select aria-label="Model" value={model} onChange={(e) => setModel(e.currentTarget.value)}>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label class="pg-field pr-form-key"><span class="label">API key</span>
          <input {...SECRET_INPUT} aria-label="API key" value={key} placeholder="PrusaLink → Settings → API key" onInput={(e) => setKey(e.currentTarget.value)} />
        </label>
      </div>
      {host !== "" && !hostOk && <div class="pg-error">Host is an IP address or hostname, optionally :port — no http:// and no path.</div>}
      <p class="dim pr-note">{LAN_NOTE}</p>
      <div class="pr-row">
        <Button type="submit" variant="ink" disabled={!can}>Add printer</Button>
        {onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
      {error && <div class="pg-error text" role="alert">{error}</div>}
    </form>
  );
}
