"""Provider model defaults shared by generation and failure accounting.

Reviewed 2026-09-21. See README.md's model maintenance notes for sources.
"""

import os


DEFAULT_MODELS = {
    "text": {
        "anthropic": "claude-sonnet-5",
        "gemini": "gemini-3.8-flash",
        "openai": "gpt-5.6-sol",
        "grok": "grok-4.3",
    },
    "image": {
        "gemini": "gemini-3.1-flash-image",
        "openai": "gpt-image-2.5-flare",
        "grok": "grok-imagine-image-2.0",
    },
    "video": {
        "gemini": "veo-3.1-fast-generate-preview",
        "grok": "grok-imagine-video-1.5",
    },
}
OPENAI_IMAGE_EDIT_MODEL = "gpt-image-2.5-sunburst"


def get_model(mode: str, backend: str, *, image_edit: bool = False) -> str:
    """Resolve explicit overrides before the shared backend default."""
    if mode == "image" and backend == "openai" and image_edit:
        return os.environ.get("OPENAI_IMAGE_EDIT_MODEL") or OPENAI_IMAGE_EDIT_MODEL
    override = os.environ.get(f"{mode.upper()}_MODEL")
    if mode == "image" and backend == "openai":
        override = override or os.environ.get("OPENAI_IMAGE_MODEL")
    return override or DEFAULT_MODELS.get(mode, {}).get(backend, "")
