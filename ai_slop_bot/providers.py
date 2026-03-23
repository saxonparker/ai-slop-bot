"""Provider protocols and registry for text and image generation backends."""

import importlib
import os
from typing import Protocol

from usage import GenerationResult


class TextProvider(Protocol):
    """Interface for text generation backends."""
    def generate(self, system: str, prompt: str) -> GenerationResult: ...


class ImageProvider(Protocol):
    """Interface for image generation backends."""
    def generate(self, prompt: str) -> GenerationResult: ...


class VideoProvider(Protocol):
    """Interface for video generation backends."""
    def generate(self, prompt: str, duration: int | None = None) -> GenerationResult: ...


TEXT_PROVIDERS = {
    "anthropic": "backends.anthropic_text.AnthropicProvider",
    "gemini": "backends.gemini_text.GeminiProvider",
    "openai": "backends.openai_text.OpenAIProvider",
    "grok": "backends.grok_text.GrokProvider",
}

VIDEO_PROVIDERS = {
    "grok": "backends.grok_video.GrokProvider",
}

IMAGE_PROVIDERS = {
    "gemini": "backends.gemini_image.GeminiProvider",
    "openai": "backends.openai_image.OpenAIProvider",
    "grok": "backends.grok_image.GrokProvider",
}


def _load_provider(registry: dict, name: str):
    """Import and instantiate a provider class from a dotted path in the registry."""
    if name not in registry:
        raise ValueError(f"Unknown backend: {name!r}. Available: {list(registry.keys())}")
    module_path, class_name = registry[name].rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def get_text_provider(override: str | None = None) -> TextProvider:
    """Get a text provider instance, using override, env var, or default (gemini)."""
    backend = override or os.environ.get("TEXT_BACKEND", "gemini")
    return _load_provider(TEXT_PROVIDERS, backend)


def get_video_provider(override: str | None = None) -> VideoProvider:
    """Get a video provider instance, using override, env var, or default (grok)."""
    backend = override or os.environ.get("VIDEO_BACKEND", "grok")
    return _load_provider(VIDEO_PROVIDERS, backend)


def get_image_provider(override: str | None = None) -> ImageProvider:
    """Get an image provider instance, using override, env var, or default (gemini)."""
    backend = override or os.environ.get("IMAGE_BACKEND", "gemini")
    return _load_provider(IMAGE_PROVIDERS, backend)
