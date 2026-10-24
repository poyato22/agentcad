"""Resolve "vendor/model-id" references to a provider and model id."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from mises.platform import keychain_get
from mises.providers.anthropic_provider import AnthropicProvider
from mises.providers.base import ModelProvider
from mises.providers.openai_provider import OpenAICompatProvider
from mises.types import ErrorCode, MisesError

DEFAULT_MODEL = "anthropic/claude-sonnet-5-5"


@dataclass(frozen=True)
class VendorSpec:
    kind: Literal["anthropic", "openai"]
    base_url: str | None
    env_key: str | None


VENDORS: dict[str, VendorSpec] = {
    "anthropic": VendorSpec("anthropic", None, "ANTHROPIC_API_KEY"),
    "openai": VendorSpec("openai", None, "OPENAI_API_KEY"),
    "gemini": VendorSpec("openai", "https://generativelanguage.googleapis.com/v1beta/openai/",
                         "GEMINI_API_KEY"),
    "openrouter": VendorSpec("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": VendorSpec("openai", "http://localhost:11434/v1", None),
    "lmstudio": VendorSpec("openai", "http://localhost:1234/v1", None),
}


def resolve(model_ref: str) -> tuple[ModelProvider, str]:
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
    key: str | None = None
    if spec.env_key:
        key = os.environ.get(spec.env_key) or keychain_get("mises", vendor)
        if not key:
            raise MisesError(ErrorCode.DENIED, f"no API key for {vendor}",
                             hint=f"set {spec.env_key} or run `mises keys set {vendor}`")
    if spec.kind == "anthropic":
        return AnthropicProvider(key or "", spec.base_url), model
    return OpenAICompatProvider(vendor, key, spec.base_url), model
