// P10 E3: error rows explain themselves (the core's cause / hint first, the app's own fallback).
import { describe, expect, it } from "vitest";
import { errorKind, explainError, isApiErrorText } from "./errorText";

const SSL = "Claude Code: API Error: Unable to connect to API due to SSL error (UNABLE_TO_GET_ISSUER_CERT_LOCALLY)";

describe("explainError", () => {
  it("recognises Claude Code's TLS failure and explains it with the NODE_EXTRA_CA_CERTS way out", () => {
    const v = explainError({ code: "tool_failed", message: SSL });
    expect(v.kind).toBe("tls");
    expect(v.title).toMatch(/secure connection/);
    expect(v.cause).toMatch(/VPN, proxy/);
    expect(v.hint).toMatch(/NODE_EXTRA_CA_CERTS/);
    expect(v.message).toBe(SSL);
  });

  it("the core's own cause and hint win", () => {
    const v = explainError({ code: "tool_failed", message: SSL, cause: "TLS interception by a proxy", hint: "Set it in Profile > Claude Code" });
    expect(v.cause).toBe("TLS interception by a proxy");
    expect(v.hint).toBe("Set it in Profile > Claude Code");
  });

  it("network, budget, tool and other errors", () => {
    expect(errorKind("tool_failed", "connect ECONNREFUSED 127.0.0.1:443")).toBe("network");
    expect(errorKind("transient", "getaddrinfo ENOTFOUND api.anthropic.com")).toBe("network");
    expect(errorKind("budget_exhausted", "the run's $5 cap is spent")).toBe("budget");
    expect(errorKind("tool_failed", "part_build: syntax error")).toBe("tool");
    expect(errorKind("error", "boom")).toBe("other");
    const plain = explainError({ code: "tool_failed", message: "part_build: syntax error", hint: null });
    expect(plain.cause).toBeNull();
    expect(plain.hint).toBeNull();
    expect(plain.title).toBe("A step failed");
  });

  it("an API error reply is plain text", () => {
    expect(isApiErrorText(`API Error: ${SSL}`)).toBe(true);
    expect(isApiErrorText("The bracket is 3 mm thick; snake_case is fine.")).toBe(false);
  });
});
