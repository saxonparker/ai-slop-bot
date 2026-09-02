"""xAI Grok video generation backend."""

import os
import time

import requests
from usage import (
    GenerationResult,
    ProviderGenerationError,
    COST_PER_VIDEO,
    classify_xai_error,
    classify_xai_video_failure,
    xai_cost_from_error,
    xai_cost_from_usage,
)


BASE_URL = "https://api.x.ai/v1"
POLL_INTERVAL = 5
MAX_POLL_ATTEMPTS = 120

DEFAULT_MODEL = "grok-imagine-video-1.5"
DEFAULT_RESOLUTION = "1080p"
# Ordered low to high so resolutions can be clamped by index.
RESOLUTIONS = ("480p", "720p", "1080p")
# xAI caps reference-guided generation below the model's native resolution.
REFERENCE_MAX_RESOLUTION = "720p"
MAX_REFERENCE_IMAGES = 7
MAX_REFERENCE_VOICES = 3


def _resolution_for(references: list, voices: list) -> str:
    """Pick the output resolution, clamped to what references allow."""
    requested = os.environ.get("VIDEO_RESOLUTION", DEFAULT_RESOLUTION).lower()
    if requested not in RESOLUTIONS:
        requested = DEFAULT_RESOLUTION
    if not references and not voices:
        return requested
    if RESOLUTIONS.index(requested) <= RESOLUTIONS.index(REFERENCE_MAX_RESOLUTION):
        return requested
    return REFERENCE_MAX_RESOLUTION


def _tag_voices(prompt: str, voices: list) -> str:
    """Bind voices to the prompt, since xAI matches them by <AUDIO_n> tag."""
    if not voices or "<AUDIO_" in prompt.upper():
        return prompt
    tags = ", ".join(f"<AUDIO_{index}>" for index in range(len(voices)))
    if len(voices) == 1:
        return f"{prompt} The speaker uses the voice from {tags}."
    return f"{prompt} The speakers use the voices from {tags}."


def _generation_payload(  # pylint: disable=too-many-arguments
    model: str,
    prompt: str,
    duration: int,
    *,
    source_image,
    references: list,
    voices: list,
) -> dict:
    """Build the /videos/generations body, validating reference limits."""
    if source_image and references:
        raise ValueError("Grok video supports either a start image or reference images, not both.")
    if len(references) > MAX_REFERENCE_IMAGES:
        raise ValueError(
            f"Grok reference-to-video supports at most {MAX_REFERENCE_IMAGES} reference images."
        )
    if len(voices) > MAX_REFERENCE_VOICES:
        raise ValueError(f"Grok video supports at most {MAX_REFERENCE_VOICES} voices.")
    if references and duration > 10:
        raise ValueError("Grok reference-to-video supports a maximum duration of 10 seconds.")

    payload = {
        "model": model,
        "prompt": _tag_voices(prompt, voices),
        "duration": duration,
        "resolution": _resolution_for(references, voices),
    }
    if source_image:
        payload["image"] = {"url": source_image.provider_url()}
    if references:
        payload["reference_images"] = [
            {"url": reference.provider_url()} for reference in references
        ]
    if voices:
        payload["reference_audios"] = [{"voice_id": voice} for voice in voices]
    return payload


class GrokProvider:
    """Video generation using xAI Grok."""

    def generate(  # pylint: disable=too-many-arguments
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
        api_key = os.environ["XAI_API_KEY"]
        model = os.environ.get("VIDEO_MODEL", DEFAULT_MODEL)
        duration = duration or int(os.environ.get("VIDEO_DURATION", "10"))
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        if video_op:
            if not video_url:
                raise ValueError("Grok video edit/extend requires a source video URL.")
            if video_op == "edit":
                endpoint = f"{BASE_URL}/videos/edits"
            elif video_op == "extend":
                endpoint = f"{BASE_URL}/videos/extensions"
            else:
                raise ValueError(f"Unsupported Grok video operation: {video_op}")
            # Verify edits/extensions against xAI docs; "video": {"url": ...}
            # mirrors the generation "image": {"url": ...} payload shape.
            payload = {
                "model": model,
                "prompt": prompt,
                "video": {"url": video_url},
                "duration": duration,
            }
        else:
            endpoint = f"{BASE_URL}/videos/generations"
            payload = _generation_payload(
                model,
                prompt,
                duration,
                source_image=source_image,
                references=references or [],
                voices=voices or [],
            )

        return self._submit_and_poll(endpoint, headers, payload, model, duration)

    @staticmethod
    def _submit_and_poll(endpoint: str, headers: dict, payload: dict,
                         model: str, duration: int) -> GenerationResult:
        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=30,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            cost_actual, cost_ticks = xai_cost_from_error(exc)
            error_type, user_message = classify_xai_error(exc)
            raise ProviderGenerationError(
                str(exc),
                backend="grok",
                model=model,
                error_type=error_type,
                user_message=user_message,
                cost_estimate=duration * COST_PER_VIDEO["grok"],
                cost_actual=cost_actual,
                cost_in_usd_ticks=cost_ticks,
            ) from exc
        request_id = resp.json()["request_id"]

        # Poll for completion
        for _ in range(MAX_POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL)
            status_resp = requests.get(
                f"{BASE_URL}/videos/{request_id}",
                headers=headers,
                timeout=30,
            )
            status_resp.raise_for_status()
            data = status_resp.json()
            status = data["status"]

            if status == "done":
                video_url = data["video"]["url"]
                duration = data["video"].get("duration", 0)
                video_data = requests.get(video_url, timeout=60).content
                cost = duration * COST_PER_VIDEO["grok"]
                cost_actual, cost_ticks = xai_cost_from_usage(data.get("usage"))
                return GenerationResult(
                    content=video_data,
                    backend="grok",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_estimate=cost,
                    cost_actual=cost_actual,
                    cost_in_usd_ticks=cost_ticks,
                )
            if status in ("failed", "expired"):
                cost_actual, cost_ticks = xai_cost_from_usage(data.get("usage"))
                error_type, user_message = classify_xai_video_failure(data)
                raise ProviderGenerationError(
                    f"Video generation {status}: {data}",
                    backend="grok",
                    model=model,
                    error_type=error_type,
                    user_message=user_message,
                    cost_estimate=duration * COST_PER_VIDEO["grok"],
                    cost_actual=cost_actual,
                    cost_in_usd_ticks=cost_ticks,
                )

        raise RuntimeError("Video generation timed out waiting for completion")
