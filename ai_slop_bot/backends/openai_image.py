"""OpenAI DALL-E image generation backend."""

import os

from openai import OpenAI
import requests
from usage import GenerationResult, COST_PER_IMAGE


class OpenAIProvider:
    """Image generation using OpenAI DALL-E."""

    def generate(self, prompt: str) -> GenerationResult:
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            organization=os.environ["OPENAI_ORGANIZATION"],
        )
        model = os.environ.get("IMAGE_MODEL", "dall-e-3")
        response = client.images.generate(
            prompt=prompt, n=1, size="1024x1024", model=model, quality="hd"
        )
        image_url = response.data[0].url
        image_response = requests.get(image_url, timeout=10000)
        return GenerationResult(
            content=image_response.content,
            backend="openai",
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_estimate=COST_PER_IMAGE["openai"],
        )
