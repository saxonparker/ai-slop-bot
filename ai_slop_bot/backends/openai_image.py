"""OpenAI GPT Image generation and editing backend."""

import base64
import io
import os

from openai import OpenAI
import requests
import model_config
from usage import GenerationResult, estimate_openai_image_cost


class OpenAIProvider:
    """Image generation and reference edits using OpenAI's Images API."""

    def generate(self, prompt: str, references: list | None = None) -> GenerationResult:
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            organization=os.environ["OPENAI_ORGANIZATION"],
        )
        references = references or []
        if references:
            return self._edit(client, prompt, references)

        model = model_config.get_model("image", "openai")
        quality = "hd" if model == "dall-e-3" else "standard" if model == "dall-e-2" else "medium"
        response = client.images.generate(
            prompt=prompt, n=1, size="1024x1024", model=model, quality=quality
        )
        return _result(response, model)

    def _edit(self, client, prompt: str, references: list) -> GenerationResult:
        model = model_config.get_model("image", "openai", image_edit=True)
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
            quality="medium",
        )
        return _result(response, model)


def _result(response, model: str) -> GenerationResult:
    """Decode GPT Image base64 responses, retaining legacy URL support."""
    if not response.data:
        raise RuntimeError("OpenAI returned no image. Please try again.")
    image = response.data[0]
    if image.b64_json:
        content = base64.b64decode(image.b64_json)
    elif image.url:
        image_response = requests.get(image.url, timeout=10000)
        image_response.raise_for_status()
        content = image_response.content
    else:
        raise RuntimeError("OpenAI returned no image data. Please try again.")
    api_usage = getattr(response, "usage", None)
    return GenerationResult(
        content=content,
        backend="openai",
        model=model,
        input_tokens=getattr(api_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(api_usage, "output_tokens", 0) or 0,
        cost_estimate=estimate_openai_image_cost(model, api_usage),
    )


def _extension_for(mime_type: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime_type, "png")
