"""Google Gemini (Nano Banana) image generation backend."""

import io
import os

from google import genai
from google.genai import types


class GeminiProvider:
    """Image generation using Google Gemini Nano Banana."""

    def generate(self, prompt: str) -> bytes:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        response = client.models.generate_content(
            model=os.environ.get("IMAGE_MODEL", "gemini-3.1-flash-image-preview"),
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data
        raise RuntimeError("No image was generated in the response")
