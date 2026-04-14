"""xAI Grok image generation backend."""

import os

from openai import OpenAI
import requests
from usage import GenerationResult, COST_PER_IMAGE


class GrokProvider:
    """Image generation using xAI Grok."""

    def generate(self, prompt: str) -> GenerationResult:
        client = OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url="https://api.x.ai/v1",
        )
        model = os.environ.get("IMAGE_MODEL", "grok-imagine-image")
        full_prompt = (
            "Do not render the prompt text as a sign or banner in the image. " + prompt
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
