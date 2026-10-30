"""Which models are sent images (tool renders): vision vendors, minus their text-only models."""
import pytest

from mises.runtime import is_vision_model


@pytest.mark.parametrize("ref", ["anthropic/claude-sonnet-5-5", "claude-opus-5", "openai/gpt-5",
                                 "openai/gpt-4o-mini", "openai/o3", "gemini/gemini-2.5-pro",
                                 "claude-code/opus"])
def test_vision_capable(ref):
    assert is_vision_model(ref)


@pytest.mark.parametrize("ref", ["openai/o3-mini", "openai/o1-mini", "openai/gpt-3.5-turbo",
                                 "openai/gpt-3.5-turbo-instruct", "openai/text-embedding-3-small",
                                 "gemini/gemini-pro", "ollama/llava", "lmstudio/qwen-vl",
                                 "openrouter/anthropic/claude-sonnet", "moonshot/kimi-k3"])
def test_text_only_or_unknown(ref):
    assert not is_vision_model(ref)
