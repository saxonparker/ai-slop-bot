"""Google Veo video generation backend."""

import io
import os
import time

from google import genai
from google.genai import types
from PIL import Image
from usage import GenerationResult, COST_PER_VIDEO


POLL_INTERVAL = 10
MAX_POLL_ATTEMPTS = 60  # ~10 minutes
# Veo renders fixed-length clips; a requested duration is snapped to one of these.
SUPPORTED_DURATIONS = (4, 6, 8)


class GeminiProvider:
    """Video generation using Google Veo (with native audio/dialogue)."""

    def generate(self, prompt: str, duration: int | None = None,
                 source_image=None, references: list | None = None) -> GenerationResult:
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
        operation = client.models.generate_videos(**request)

        # Poll the operation until the video is ready.
        for _ in range(MAX_POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL)
            operation = client.operations.get(operation)
            if operation.done:
                break
        else:
            raise RuntimeError("Video generation timed out waiting for completion")

        if operation.error:
            raise RuntimeError(f"Video generation failed: {operation.error}")

        videos = operation.response.generated_videos if operation.response else None
        if not videos:
            raise RuntimeError(f"Veo returned no video. Response: {operation.response}")

        video = videos[0].video
        video_data = client.files.download(file=video) or video.video_bytes
        cost = duration * COST_PER_VIDEO["gemini"]
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
