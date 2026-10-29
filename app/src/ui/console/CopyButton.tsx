// A small "Copy" control for an agent reply: copies the raw markup source to the clipboard and
// flashes "Copied" briefly. Keyboard-reachable; falls back silently if the clipboard is blocked.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { debug } from "../../lib/log";

export function CopyButton({ text }: { text: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = () => {
    void (async () => {
      try {
        await navigator.clipboard?.writeText(text);
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1200);
      } catch (e) {
        debug("copy failed", e);
      }
    })();
  };

  return (
    <button type="button" class="cv-copy" aria-label="Copy reply" title="Copy reply" onClick={copy}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
