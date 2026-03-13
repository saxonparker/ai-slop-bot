"""Google Gemini (Nano Banana) image generation backend."""

import os

from google import genai
from google.genai import types
from usage import GenerationResult, COST_PER_IMAGE


class GeminiProvider:
    """Image generation using Google Gemini Nano Banana."""

    def generate(self, prompt: str) -> GenerationResult:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        model = os.environ.get("IMAGE_MODEL", "gemini-3.1-flash-image-preview")
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        if not response.candidates:
            print(f"GEMINI IMAGE: No candidates returned. Full response: {response}")
            raise RuntimeError("Gemini returned no candidates — prompt may have been blocked")

        candidate = response.candidates[0]
        if candidate.finish_reason and candidate.finish_reason.name != "STOP":
            print(f"GEMINI IMAGE: finish_reason={candidate.finish_reason}")

        text_parts = []
        for part in candidate.content.parts:
            if part.inline_data is not None:
                return GenerationResult(
                    content=part.inline_data.data,
                    backend="gemini",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_estimate=COST_PER_IMAGE["gemini"],
                )
            if part.text is not None:
                text_parts.append(part.text)

        # No image — log whatever text Gemini returned instead
        text_response = " ".join(text_parts) if text_parts else "(no text either)"
        print(f"GEMINI IMAGE: No image in response. Text returned: {text_response}")
        raise RuntimeError(f"No image generated. Gemini said: {text_response}")
