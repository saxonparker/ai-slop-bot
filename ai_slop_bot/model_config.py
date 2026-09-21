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


# xAI video output resolutions, ordered low to high so callers can clamp by index.
# The strings match the API enum exactly; see README.md's video resolution notes.
VIDEO_RESOLUTIONS = ("480p", "720p", "1080p")
DEFAULT_VIDEO_RESOLUTION = "1080p"
# xAI caps reference-guided generation below the model's native resolution.
REFERENCE_MAX_VIDEO_RESOLUTION = "720p"


def normalize_resolution(value: str | None) -> str | None:
    """Canonicalize a requested resolution, accepting `720` as well as `720p`.

    Returns None for anything the video API does not accept, so callers can
    tell "not requested" from "requested something unsupported".
    """
    if not value:
        return None
    candidate = value.strip().lower()
    if not candidate.endswith("p"):
        candidate += "p"
    return candidate if candidate in VIDEO_RESOLUTIONS else None


def resolve_video_resolution(requested: str | None = None, *,
                             has_references: bool = False) -> str:
    """Resolve the resolution xAI will actually render, after clamping.

    Grok video is billed per second at a rate that scales with resolution, so
    generation and cost estimation must agree on this one answer — a request
    clamped down to 720p is billed at the 720p rate, not the requested one.
    """
    resolution = (
        normalize_resolution(requested)
        or normalize_resolution(os.environ.get("VIDEO_RESOLUTION"))
        or DEFAULT_VIDEO_RESOLUTION
    )
    if not has_references:
        return resolution
    cap = REFERENCE_MAX_VIDEO_RESOLUTION
    return resolution if VIDEO_RESOLUTIONS.index(resolution) <= VIDEO_RESOLUTIONS.index(cap) else cap
