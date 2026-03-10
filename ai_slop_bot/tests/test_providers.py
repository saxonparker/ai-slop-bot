"""Tests for providers module."""

import sys

sys.path.append(".")

import pytest
import providers


def test_text_registry_has_expected_backends():
    assert "anthropic" in providers.TEXT_PROVIDERS
    assert "gemini" in providers.TEXT_PROVIDERS
    assert "openai" in providers.TEXT_PROVIDERS


def test_image_registry_has_expected_backends():
    assert "gemini" in providers.IMAGE_PROVIDERS
    assert "openai" in providers.IMAGE_PROVIDERS


def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        providers.get_text_provider("nonexistent")


def test_unknown_image_backend_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        providers.get_image_provider("nonexistent")


def test_load_anthropic_provider():
    provider = providers.get_text_provider("anthropic")
    assert type(provider).__name__ == "AnthropicProvider"


def test_load_gemini_text_provider():
    provider = providers.get_text_provider("gemini")
    assert type(provider).__name__ == "GeminiProvider"


def test_load_openai_text_provider():
    provider = providers.get_text_provider("openai")
    assert type(provider).__name__ == "OpenAIProvider"


def test_load_gemini_image_provider():
    provider = providers.get_image_provider("gemini")
    assert type(provider).__name__ == "GeminiProvider"


def test_load_openai_image_provider():
    provider = providers.get_image_provider("openai")
    assert type(provider).__name__ == "OpenAIProvider"
