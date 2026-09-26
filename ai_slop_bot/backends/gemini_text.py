"""Google Gemini text generation backend."""

import os

from google import genai

import model_config
from usage import GenerationResult, estimate_text_cost


class GeminiProvider:
    """Text generation using Google Gemini."""

    def generate(self, system: str, prompt: str) -> GenerationResult:
        return self._generate(system, prompt)

    def chat(self, system: str, messages: list[dict]) -> GenerationResult:
        """Generate the next reply for a user/assistant message history."""
        contents = [
            {
                "role": "user" if message["role"] == "user" else "model",
                "parts": [{"text": message["content"]}],
            }
            for message in messages
        ]
        return self._generate(system, contents)

    def _generate(self, system: str, contents) -> GenerationResult:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        model = model_config.get_model("text", "gemini")
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config={"system_instruction": system},
        )
        text = response.text
        if text is None:
            print(f"GEMINI TEXT: response.text is None. Full response: {response}")
            raise RuntimeError("Gemini returned no text (likely a safety block).")
        metadata = getattr(response, "usage_metadata", None)
        input_tokens = getattr(metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(metadata, "candidates_token_count", 0) or 0
        output_tokens += getattr(metadata, "thoughts_token_count", 0) or 0
        cost = estimate_text_cost("gemini", input_tokens, output_tokens, model=model)
        return GenerationResult(
            content=text,
            backend="gemini",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )
