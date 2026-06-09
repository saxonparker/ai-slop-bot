"""xAI Grok image generation backend."""

import base64
import os

from openai import OpenAI
import requests
from usage import GenerationResult, COST_PER_IMAGE


BASE_URL = "https://api.x.ai/v1"
DEFAULT_EDIT_TIMEOUT_SECONDS = 180


class GrokProvider:
    """Image generation using xAI Grok."""

    def generate(self, prompt: str, references: list | None = None) -> GenerationResult:
        references = references or []
        if references:
            return self._edit(prompt, references)

        client = OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url=BASE_URL,
        )
        model = os.environ.get("IMAGE_MODEL", "grok-imagine-image-quality")
        full_prompt = (
            "CRITICAL INSTRUCTION: Never place the user's prompt as visible "
            "text in the image. Do not write the prompt on signs, banners, "
            "posters, or any surface. The prompt describes what to depict, "
            "not text to display. Generate the scene without any visible "
            "rendering of these instructions.\n\n" + prompt
        )
        response = client.images.generate(
            prompt=full_prompt, n=1, model=model,
        )
        image_url = response.data[0].url
        image_response = requests.get(image_url, timeout=10000)
        return GenerationResult(
            content=image_response.content,
            backend="grok",
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_estimate=COST_PER_IMAGE["grok"],
        )

    def _edit(self, prompt: str, references: list) -> GenerationResult:
        """Use xAI's JSON image edit endpoint for one or more reference images."""
        if len(references) > 3:
            raise ValueError("Grok image editing supports at most 3 reference images.")

        api_key = os.environ["XAI_API_KEY"]
        model = os.environ.get("IMAGE_MODEL", "grok-imagine-image-quality")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "prompt": prompt,
        }
        image_payloads = [
            {"type": "image_url", "url": reference.provider_url()}
            for reference in references
        ]
        if len(image_payloads) == 1:
            payload["image"] = image_payloads[0]
        else:
            payload["images"] = image_payloads

        timeout = int(os.environ.get(
            "GROK_IMAGE_EDIT_TIMEOUT_SECONDS",
            str(DEFAULT_EDIT_TIMEOUT_SECONDS),
        ))
        try:
            response = requests.post(
                f"{BASE_URL}/images/edits",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RuntimeError(
                f"Grok image edit timed out after {timeout} seconds. Please retry.",
            ) from exc
        response.raise_for_status()
        data = response.json()
        image = data["data"][0]
        if image.get("url"):
            image_response = requests.get(image["url"], timeout=10000)
            image_response.raise_for_status()
            content = image_response.content
        else:
            content = base64.b64decode(image["b64_json"])
        return GenerationResult(
            content=content,
            backend="grok",
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_estimate=COST_PER_IMAGE["grok"] * (1 + len(references)),
        )
