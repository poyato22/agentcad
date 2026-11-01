"""Resolve "vendor/model-id" references to a provider and model id."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from mises.platform import keychain_get
from mises.providers.anthropic_provider import AnthropicProvider
from mises.providers.base import ModelProvider, RateLimiter
from mises.providers.ollama_provider import OllamaProvider
from mises.providers.openai_provider import OpenAICompatProvider
from mises.types import ErrorCode, MisesError

DEFAULT_MODEL = "anthropic/claude-sonnet-5-5"


@dataclass(frozen=True)
class VendorSpec:
    kind: Literal["anthropic", "openai"]
    base_url: str | None
    env_key: str | None
    # stream `reasoning_content` and send it back on later turns (Moonshot/Kimi requires it)
    reasoning_content: bool = False


VENDORS: dict[str, VendorSpec] = {
    "anthropic": VendorSpec("anthropic", None, "ANTHROPIC_API_KEY"),
    "openai": VendorSpec("openai", None, "OPENAI_API_KEY"),
    "gemini": VendorSpec("openai", "https://generativelanguage.googleapis.com/v1beta/openai/",
                         "GEMINI_API_KEY"),
    "openrouter": VendorSpec("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    # Kimi (platform.kimi.ai docs, formerly platform.moonshot.ai): OpenAI-compatible
    "moonshot": VendorSpec("openai", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY",
                           reasoning_content=True),
    "ollama": VendorSpec("openai", "http://localhost:11434/v1", None),
    "lmstudio": VendorSpec("openai", "http://localhost:1234/v1", None),
}


# Kimi ids from the Kimi API docs (K3.1 Pro quickstart; K3 in the thinking-model guide)
MOONSHOT_MODELS = ("moonshot/kimi-k3.1-pro", "moonshot/kimi-k3.1-pro-highspeed", "moonshot/kimi-k3")

# OpenRouter free models: 20 requests/min per account (openrouter.ai/docs/api-reference/limits),
# shared by every run using one
OPENROUTER_FREE_LIMITER = RateLimiter(20, 60.0)


def is_openrouter_free(vendor: str, model: str) -> bool:
    from mises.hardware import is_free_model
    return is_free_model(f"{vendor}/{model}")


def ref_vendor(model_ref: str) -> str | None:
    """The vendor a `vendor/model` (or bare `claude-*`) ref names, else None."""
    if "/" in model_ref:
        return model_ref.split("/", 1)[0]
    if model_ref.startswith("claude-"):
        return "anthropic"
    return None


def has_key(vendor: str) -> bool:
    """True when `vendor` needs no API key (a local vendor, or an unknown name) or one is
    configured for it (env var or Keychain). Used to decide, with no network call, whether a
    model ref can actually be reached before a run starts (E34 default_model_ref)."""
    spec = VENDORS.get(vendor)
    if spec is None or spec.env_key is None:
        return True
    return bool(os.environ.get(spec.env_key) or keychain_get("mises", vendor))


def ref_has_key(model_ref: str) -> bool:
    """True when the model ref's vendor needs no key or has one; a claude-code alias (no vendor
    here) counts as reachable only via the Claude Code path, so it returns True as well."""
    vendor = ref_vendor(model_ref)
    return True if vendor is None else has_key(vendor)


def resolve(model_ref: str,
            *, local_endpoints: dict[str, str] | None = None) -> tuple[ModelProvider, str]:
    if "/" in model_ref:
        vendor, model = model_ref.split("/", 1)
    elif model_ref.startswith("claude-"):
        vendor, model = "anthropic", model_ref
    else:
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"model reference {model_ref!r} must look like 'vendor/model-id'",
                         hint=f"vendors: {', '.join(VENDORS)}")
    spec = VENDORS.get(vendor)
    if spec is None or not model:
        raise MisesError(ErrorCode.INVALID_INPUT, f"unknown vendor or empty model in {model_ref!r}",
                         hint=f"vendors: {', '.join(VENDORS)}")
    # a local vendor (ollama/lmstudio) honours the profile's configured base URL, falling back
    # to the default port; cloud vendors always use their fixed base URL
    base_url = spec.base_url
    if vendor in ("ollama", "lmstudio") and local_endpoints:
        base_url = local_endpoints.get(vendor) or base_url
    key: str | None = None
    if spec.env_key:
        key = os.environ.get(spec.env_key) or keychain_get("mises", vendor)
        if not key:
            raise MisesError(ErrorCode.DENIED, f"no API key for {vendor}",
                             hint=f"add a key in Profile → Model (or set {spec.env_key}, or "
                                  f"run `mises keys set {vendor}`)")
    if spec.kind == "anthropic":
        return AnthropicProvider(key or "", base_url), model
    if vendor == "ollama":
        # native /api/chat: the only Ollama API that takes num_ctx (see ollama_provider.py)
        return OllamaProvider(base_url), model
    limiter = OPENROUTER_FREE_LIMITER if is_openrouter_free(vendor, model) else None
    return OpenAICompatProvider(vendor, key, base_url,
                                reasoning_content=spec.reasoning_content,
                                rate_limiter=limiter), model
