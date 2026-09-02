"""Google Veo video generation backend."""

import io
import os
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image
from usage import (
    COST_PER_VIDEO,
    GenerationResult,
    ProviderGenerationError,
    classify_gemini_error,
)


POLL_INTERVAL = 10
MAX_POLL_ATTEMPTS = 60  # ~10 minutes
# Veo renders fixed-length clips; a requested duration is snapped to one of these.
SUPPORTED_DURATIONS = (4, 6, 8)


class GeminiProvider:
    """Video generation using Google Veo (with native audio/dialogue)."""

    def generate(  # pylint: disable=too-many-arguments,unused-argument
        self,
        prompt: str,
        duration: int | None = None,
        source_image=None,
        references: list | None = None,
        *,
        voices: list | None = None,
        video_op: str | None = None,
        video_url: str | None = None,
    ) -> GenerationResult:
        if video_op:
            raise ValueError("Edit/extend video is only supported on the grok backend; use -b grok.")
        if voices:
            raise ValueError("Voices are only supported on the grok backend; use -b grok.")
        if references:
            raise ValueError("Veo reference images are not supported by this backend yet; use -b grok.")
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        model = os.environ.get("VIDEO_MODEL", "veo-3.1-fast-generate-preview")
        requested = duration or int(os.environ.get("VIDEO_DURATION", "8"))
        # Veo only produces 4, 6, or 8 second clips — snap to the nearest.
        duration = min(SUPPORTED_DURATIONS, key=lambda d: abs(d - requested))

        # Submit the generation request (returns a long-running operation).
        request = {
            "model": model,
            "prompt": prompt,
            "config": types.GenerateVideosConfig(duration_seconds=duration),
        }
        if source_image:
            request["image"] = _to_pil_image(source_image)
        cost = duration * COST_PER_VIDEO["gemini"]
        try:
            operation = client.models.generate_videos(**request)

            # Poll the operation until the video is ready.
            for _ in range(MAX_POLL_ATTEMPTS):
                time.sleep(POLL_INTERVAL)
                operation = client.operations.get(operation)
                if operation.done:
                    break
            else:
                raise ProviderGenerationError(
                    "Video generation timed out waiting for completion",
                    backend="gemini",
                    model=model,
                    error_type="timeout",
                    user_message="Veo timed out generating this video. Try again.",
                    cost_estimate=cost,
                )
        except genai_errors.APIError as exc:
            error_type, user_message = classify_gemini_error(exc)
            raise ProviderGenerationError(
                str(exc),
                backend="gemini",
                model=model,
                error_type=error_type,
                user_message=user_message,
                cost_estimate=cost,
            ) from exc

        if operation.error:
            message = (
                operation.error.get("message", operation.error)
                if isinstance(operation.error, dict) else operation.error
            )
            raise ProviderGenerationError(
                f"Video generation failed: {operation.error}",
                backend="gemini",
                model=model,
                error_type="provider_error",
                user_message=f"Veo failed to generate this video: {message}",
                cost_estimate=cost,
            )

        videos = operation.response.generated_videos if operation.response else None
        if not videos:
            filtered_reasons = (
                getattr(operation.response, "rai_media_filtered_reasons", None)
                if operation.response else None
            )
            if filtered_reasons:
                raise ProviderGenerationError(
                    f"Veo filtered video: {filtered_reasons}",
                    backend="gemini",
                    model=model,
                    error_type="moderation",
                    user_message=(
                        "Veo denied this video request — flagged by content moderation: "
                        + "; ".join(filtered_reasons)
                    ),
                    cost_estimate=cost,
                )
            raise ProviderGenerationError(
                f"Veo returned no video. Response: {operation.response}",
                backend="gemini",
                model=model,
                error_type="provider_error",
                user_message="Veo returned no video for this prompt. Try again.",
                cost_estimate=cost,
            )

        video = videos[0].video
        video_data = client.files.download(file=video) or video.video_bytes
        return GenerationResult(
            content=video_data,
            backend="gemini",
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_estimate=cost,
        )


def _to_pil_image(reference):
    """Convert a resolved reference to a PIL image for the Gen AI SDK."""
    return Image.open(io.BytesIO(reference.data))
