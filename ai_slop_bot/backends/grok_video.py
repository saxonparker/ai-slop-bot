"""xAI Grok video generation backend."""

import os
import time

import requests
from usage import GenerationResult, COST_PER_VIDEO


BASE_URL = "https://api.x.ai/v1"
POLL_INTERVAL = 5
MAX_POLL_ATTEMPTS = 120


class GrokProvider:
    """Video generation using xAI Grok."""

    def generate(self, prompt: str) -> GenerationResult:
        api_key = os.environ["XAI_API_KEY"]
        model = os.environ.get("VIDEO_MODEL", "grok-imagine-video")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # Submit generation request
        resp = requests.post(
            f"{BASE_URL}/videos/generations",
            headers=headers,
            json={"model": model, "prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
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
                return GenerationResult(
                    content=video_data,
                    backend="grok",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_estimate=cost,
                )
            if status in ("failed", "expired"):
                raise RuntimeError(f"Video generation {status}: {data}")

        raise RuntimeError("Video generation timed out waiting for completion")
