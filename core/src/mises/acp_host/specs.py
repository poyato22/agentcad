"""Known external ACP agents and how to launch them.

Command lines were checked on 2026-10-24 against the official ACP registry
(https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json), npm, and each
agent's own docs. Versions are pinned to the registry's current entry, as the ACP docs
advise: the adapters move fast. `verified` means the command line was confirmed from an
official source; no agent here was actually run (they all need a login or API key).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AcpAgentSpec(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    verified: bool = False
    install_hint: str


KNOWN_AGENTS: dict[str, AcpAgentSpec] = {
    # Anthropic's ACP adapter (built on the Claude Agent SDK). Renamed from the deprecated
    # @zed-industries/claude-code-acp. Needs ANTHROPIC_API_KEY (pass it via env).
    "claude": AcpAgentSpec(
        name="claude", command="npx",
        args=["-y", "@agentclientprotocol/claude-agent-acp@0.95.0"],
        verified=True,
        install_hint="Needs Node.js (npx) and an Anthropic API key in ANTHROPIC_API_KEY."),
    # OpenAI Codex ACP adapter; replaces the deprecated @zed-industries/codex-acp.
    "codex": AcpAgentSpec(
        name="codex", command="npx",
        args=["-y", "@agentclientprotocol/codex-acp@2.2.2"],
        verified=True,
        install_hint="Needs Node.js (npx) and an OpenAI API key (OPENAI_API_KEY or CODEX_API_KEY)."),
    # Gemini CLI: `--experimental-acp` is deprecated in 0.65.0 ("use --acp instead").
    "gemini": AcpAgentSpec(
        name="gemini", command="npx",
        args=["-y", "@google/gemini-cli@0.65.0", "--acp"],
        verified=True,
        install_hint="Needs Node.js (npx) and a Gemini API key in GEMINI_API_KEY."),
    "opencode": AcpAgentSpec(
        name="opencode", command="opencode", args=["acp"],
        verified=True,
        install_hint="Install OpenCode from https://opencode.ai so `opencode` is on PATH, "
                     "then configure a model provider."),
    "goose": AcpAgentSpec(
        name="goose", command="goose", args=["acp"],
        verified=True,
        install_hint="Install the goose CLI from https://goose-docs.ai so `goose` is on PATH, "
                     "then run `goose configure` to pick a model provider."),
}
