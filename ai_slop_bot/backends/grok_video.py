"""xAI Grok video generation backend."""

import os
import time

import requests
from usage import (
    GenerationResult,
    ProviderGenerationError,
    COST_PER_VIDEO,
    xai_cost_from_error,
    xai_cost_from_usage,
)


BASE_URL = "https://api.x.ai/v1"
POLL_INTERVAL = 5
MAX_POLL_ATTEMPTS = 120


class GrokProvider:
    """Video generation using xAI Grok."""

    def generate(  # pylint: disable=too-many-arguments
        self,
        prompt: str,
        duration: int | None = None,
        source_image=None,
        references: list | None = None,
        *,
        video_op: str | None = None,
        video_url: str | None = None,
    ) -> GenerationResult:
        api_key = os.environ["XAI_API_KEY"]
        model = os.environ.get("VIDEO_MODEL", "grok-imagine-video")
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
            references = references or []
            if source_image and references:
                raise ValueError("Grok video supports either a start image or reference images, not both.")
            if references and len(references) > 7:
                raise ValueError("Grok reference-to-video supports at most 7 reference images.")
            if references and duration > 10:
                raise ValueError("Grok reference-to-video supports a maximum duration of 10 seconds.")
            endpoint = f"{BASE_URL}/videos/generations"
            payload = {"model": model, "prompt": prompt, "duration": duration}
            if source_image:
                payload["image"] = {"url": source_image.provider_url()}
            if references:
                payload["reference_images"] = [
                    {"url": reference.provider_url()}
                    for reference in references
                ]

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
            raise ProviderGenerationError(
                str(exc),
                backend="grok",
                model=model,
                error_type=_classify_error(exc),
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
                raise ProviderGenerationError(
                    f"Video generation {status}: {data}",
                    backend="grok",
                    model=model,
                    error_type=_classify_error(data),
                    cost_estimate=duration * COST_PER_VIDEO["grok"],
                    cost_actual=cost_actual,
                    cost_in_usd_ticks=cost_ticks,
                )

        raise RuntimeError("Video generation timed out waiting for completion")


def _classify_error(error) -> str:
    text = str(error).lower()
    if "moderation" in text or "safety" in text or "policy" in text:
        return "moderation"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "expired" in text:
        return "expired"
    return "provider_error"
