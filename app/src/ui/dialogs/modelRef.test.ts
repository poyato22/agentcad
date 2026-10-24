import { describe, expect, it } from "vitest";
import type { AgentInfo, ModelsInfo } from "../../api/types";
import { defaultModelRef, splitModelRef, toModelRef } from "./modelRef";

const models: ModelsInfo = { default: "anthropic/claude-sonnet-5-5", vendors: [] };

describe("modelRef", () => {
  it("toModelRef_roundtrip", () => {
    expect(toModelRef("anthropic", "claude-sonnet-5-5")).toBe("anthropic/claude-sonnet-5-5");
    expect(splitModelRef("anthropic/claude-sonnet-5-5")).toEqual({ vendor: "anthropic", model: "claude-sonnet-5-5" });
    expect(splitModelRef("openrouter/meta/llama")).toEqual({ vendor: "openrouter", model: "meta/llama" });
    const { vendor, model } = splitModelRef("ollama/llama3:8b");
    expect(toModelRef(vendor, model)).toBe("ollama/llama3:8b");
    expect(splitModelRef("nonsense")).toEqual({ vendor: "", model: "nonsense" });
  });
  it("defaultModelRef", () => {
    const a = (model: string): AgentInfo => ({ name: "x", description: "", model });
    expect(defaultModelRef(a("inherit"), models)).toBe("anthropic/claude-sonnet-5-5");
    expect(defaultModelRef(a("openai/gpt-x"), models)).toBe("openai/gpt-x");
  });
});
