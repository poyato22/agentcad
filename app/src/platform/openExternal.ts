// Open a URL in the system browser. The one shared entry point for every external link.
//
// MERGE NOTE (Task A2): Task A6 owns this file and implements it with tauri-plugin-opener, in
// parallel. A2 only needed the same signature, so this stand-in uses window.open; keep A6's body
// at merge. Callers have already validated the URL (http/https, no control or bidi characters)
// and shown its domain.
export async function openExternal(url: string): Promise<void> {
  window.open(url, "_blank", "noopener,noreferrer");
}
