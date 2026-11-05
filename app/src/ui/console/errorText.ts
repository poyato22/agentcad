// P10 E3: an error row that says what happened, the likely cause and what to do. The core's own
// `cause` / `hint` (C1 classifies Claude Code's network and TLS failures) always win; for a core
// that predates them the app recognises the same failures in the message itself, so a
// UNABLE_TO_GET_ISSUER_CERT_LOCALLY never reaches the user as a bare code. Pure; every string it
// returns renders as plain text (the message is untrusted).

export type ErrorKind = "tls" | "network" | "budget" | "tool" | "other";

export interface ErrorView {
  kind: ErrorKind;
  /** One sentence-case line: what happened. */
  title: string;
  code: string;
  message: string;
  cause: string | null;
  hint: string | null;
}

const TLS = /UNABLE_TO_GET_ISSUER_CERT|UNABLE_TO_VERIFY_LEAF_SIGNATURE|SELF_SIGNED_CERT|DEPTH_ZERO_SELF_SIGNED|CERT_HAS_EXPIRED|ERR_TLS|unable to verify the first certificate|certificate verify failed|self[- ]signed certificate|SSL routines/i;
const NETWORK = /ECONNREFUSED|ENOTFOUND|ETIMEDOUT|ECONNRESET|EAI_AGAIN|getaddrinfo|fetch failed|socket hang up|network (error|is unreachable)|proxy error|tunneling socket/i;

const FALLBACK: Partial<Record<ErrorKind, { title: string; cause: string; hint: string }>> = {
  tls: {
    title: "Claude Code couldn't make a secure connection to Anthropic",
    cause: "Something between this Mac and Anthropic (a VPN, proxy or antivirus that inspects HTTPS) shows a certificate Claude Code doesn't trust.",
    hint: "Set your VPN or proxy's CA file as NODE_EXTRA_CA_CERTS in Profile & settings → Claude Code → Network, then retry. Or turn the VPN or proxy off.",
  },
  network: {
    title: "Claude Code couldn't reach Anthropic",
    cause: "There is no network route to Anthropic: offline, DNS, a firewall, or a proxy AgentCAD doesn't know about.",
    hint: "Check the connection, then retry. Behind a proxy, set HTTPS_PROXY in Profile & settings → Claude Code → Network.",
  },
};

const TITLE: Record<ErrorKind, string> = {
  tls: FALLBACK.tls?.title ?? "", network: FALLBACK.network?.title ?? "",
  budget: "The agent stopped at its budget", tool: "A step failed", other: "Something went wrong",
};

export function errorKind(code: string, message: string, cause: string | null = null): ErrorKind {
  const text = `${message}\n${cause ?? ""}`;
  if (TLS.test(text)) return "tls";
  if (NETWORK.test(text)) return "network";
  if (code === "budget_exhausted") return "budget";
  if (code === "tool_failed") return "tool";
  return "other";
}

export function explainError(e: { code: string; message: string; hint?: string | null; cause?: string | null }): ErrorView {
  const message = e.message.trim();
  const kind = errorKind(e.code, message, e.cause ?? null);
  const fb = FALLBACK[kind];
  return {
    kind, title: TITLE[kind], code: e.code, message,
    cause: e.cause?.trim() || fb?.cause || null,
    hint: e.hint?.trim() || fb?.hint || null,
  };
}

/** A failed agent reply that is really Claude Code's API error ("API Error: … UNABLE_TO_…"):
 *  shown as plain text, never markup. Narrow on purpose (review minor 6): only a reply that starts
 *  as an API error, or a short one carrying a TLS failure code, loses its markup. */
export function isApiErrorText(text: string): boolean {
  const t = text.trimStart();
  return /^(API Error\b|Claude Code: API Error\b)/.test(t) || (TLS.test(t) && t.length < 600);
}
