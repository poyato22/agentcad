// Fake core: the Profile's claude_code_env (P10 E3, Profile → Claude Code → Network) like core
// profile.py — the allowlisted names, each value's shape, a CA path that must exist when it
// changes, and a proxy URL's credentials masked on the way out and kept when the masked URL comes
// back. Error messages are the core's (test/contract.p10.test.ts checks them against
// core/tests/fixtures/ui/p10/claude_env_*.json).
import { existsSync, statSync } from "node:fs";

export const CLAUDE_CODE_ENV_KEYS = ["HTTPS_PROXY", "HTTP_PROXY", "NODE_EXTRA_CA_CERTS", "NO_PROXY", "SSL_CERT_DIR", "SSL_CERT_FILE", "http_proxy", "https_proxy", "no_proxy"];
const CA_FILE = new Set(["NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"]);
const CA_DIR = new Set(["SSL_CERT_DIR"]);
const PROXY = new Set(["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"]);
const SCHEMES = new Set(["http:", "https:", "socks5:", "socks5h:"]);
const NO_PROXY_ITEM = /^(\*|\.?[A-Za-z0-9*][A-Za-z0-9.*-]*(:\d{1,5})?|\[?[0-9A-Fa-f:.]+\]?(\/\d{1,3})?(:\d{1,5})?)$/;
// eslint-disable-next-line no-control-regex
const CTRL = /[\x00-\x1f\x7f]/;
const MASK = "***";

/** Core check_claude_env_value: null when fine, else the core's message. */
function valueError(name: string, value: string): string | null {
  if (CTRL.test(value) || value.length > 4096) return `the value of '${name}' must be one line under 4096 characters, with no control characters`;
  if (CA_FILE.has(name) || CA_DIR.has(name)) {
    if (!value.startsWith("/") || `${value}/`.includes("/../") || value.endsWith("/..")) return `${name} must be an absolute path to the CA ${CA_DIR.has(name) ? "directory" : "bundle file"}`;
    return null;
  }
  if (PROXY.has(name)) {
    let u: URL | null = null;
    try { u = new URL(value); } catch { /* not a URL */ }
    if (!u || !SCHEMES.has(u.protocol) || !u.hostname || value.includes(" ") || (u.pathname !== "" && u.pathname !== "/")) {
      return `${name} must be a proxy URL like http://proxy.example:8080 (http, https, socks5 or socks5h)`;
    }
    return null;
  }
  const items = value.split(",").map((x) => x.trim());
  return items.some((x) => !x || !NO_PROXY_ITEM.test(x)) ? `${name} must be a comma-separated list of hosts, domains or IPs` : null;
}

/** A proxy URL with its credentials masked (core mask_claude_env). */
export function maskClaudeEnv(env: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, value] of Object.entries(env)) {
    out[name] = value;
    if (!PROXY.has(name)) continue;
    try {
      const u = new URL(value);
      if (u.username || u.password) out[name] = `${u.protocol}//${MASK}@${u.hostname}${u.port ? `:${u.port}` : ""}${u.pathname === "/" && !value.endsWith("/") ? "" : u.pathname}`;
    } catch { /* stored values are valid */ }
  }
  return out;
}

/** PUT /profile's claude_code_env: the cleaned value, or the core's 422 message. `old` is the
 *  stored env (a masked URL that still names the same proxy keeps its credentials). */
export function putClaudeEnv(v: unknown, old: Record<string, string>): { env: Record<string, string> } | { error: string } {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return { error: "invalid profile: claude_code_env: Input should be a valid dictionary" };
  const masked = maskClaudeEnv(old);
  const env: Record<string, string> = {};
  for (const [name, raw] of Object.entries(v as Record<string, unknown>)) {
    if (!CLAUDE_CODE_ENV_KEYS.includes(name)) {
      return { error: `invalid profile: claude_code_env: Value error, '${name}' is not an allowed Claude Code environment variable (allowed: ${CLAUDE_CODE_ENV_KEYS.join(", ")})` };
    }
    if (typeof raw !== "string") return { error: "invalid profile: claude_code_env: Input should be a valid string" };
    let value = raw.trim();
    if (value.includes(`//${MASK}@`) && name in old && masked[name] === value) value = old[name] as string;
    if (!value) continue;
    const bad = valueError(name, value);
    if (bad) return { error: `invalid profile: claude_code_env: Value error, ${bad}` };
    env[name] = value;
  }
  const missing = Object.entries(env).filter(([name, value]) => value !== old[name] && (
    (CA_FILE.has(name) && !(existsSync(value) && statSync(value).isFile())) || (CA_DIR.has(name) && !(existsSync(value) && statSync(value).isDirectory()))));
  if (missing.length) return { error: `invalid profile: claude_code_env: no such file or folder for ${missing.map(([n]) => n).join(", ")}` };
  return { env };
}
