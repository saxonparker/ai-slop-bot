"""Google Gemini text generation backend."""

import os

from google import genai
from usage import GenerationResult, estimate_text_cost


class GeminiProvider:
    """Text generation using Google Gemini."""

    def generate(self, system: str, prompt: str) -> GenerationResult:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        model = os.environ.get("TEXT_MODEL", "gemini-3-flash-preview")
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"system_instruction": system},
        )
        metadata = getattr(response, "usage_metadata", None)
        input_tokens = getattr(metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(metadata, "candidates_token_count", 0) or 0
        cost = estimate_text_cost("gemini", input_tokens, output_tokens)
        return GenerationResult(
            content=response.text,
            backend="gemini",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )
