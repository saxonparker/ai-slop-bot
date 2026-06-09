"""OpenAI DALL-E image generation backend."""

import base64
import io
import os

from openai import OpenAI
import requests
from usage import GenerationResult, COST_PER_IMAGE


class OpenAIProvider:
    """Image generation using OpenAI DALL-E."""

    def generate(self, prompt: str, references: list | None = None) -> GenerationResult:
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            organization=os.environ["OPENAI_ORGANIZATION"],
        )
        references = references or []
        if references:
            return self._edit(client, prompt, references)

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

    def _edit(self, client, prompt: str, references: list) -> GenerationResult:
        model = os.environ.get("OPENAI_IMAGE_EDIT_MODEL", "gpt-image-2")
        files = []
        for idx, reference in enumerate(references):
            file_obj = io.BytesIO(reference.data)
            file_obj.name = f"reference-{idx}.{_extension_for(reference.mime_type)}"
            files.append(file_obj)

        image_arg = files[0] if len(files) == 1 else files
        response = client.images.edit(
            model=model,
            image=image_arg,
            prompt=prompt,
            size="1024x1024",
        )
        image = response.data[0]
        if getattr(image, "b64_json", None):
            content = base64.b64decode(image.b64_json)
        else:
            image_url = image.url
            image_response = requests.get(image_url, timeout=10000)
            content = image_response.content

        usage = getattr(response, "usage", None)
        return GenerationResult(
            content=content,
            backend="openai",
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cost_estimate=COST_PER_IMAGE["openai"],
        )


def _extension_for(mime_type: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime_type, "png")
