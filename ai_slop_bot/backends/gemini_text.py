"""Google Gemini text generation backend."""

import os

from google import genai

import conversations
from usage import GenerationResult, estimate_text_cost


class GeminiProvider:
    """Text generation using Google Gemini."""

    def chat(self, system: str, messages: list[dict]) -> GenerationResult:
        """Generate a completion given a multi-turn message history."""
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        model = os.environ.get("TEXT_MODEL", "gemini-3-flash-preview")
        contents = conversations.to_gemini(messages)
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
        cost = estimate_text_cost("gemini", input_tokens, output_tokens)
        return GenerationResult(
            content=text,
            backend="gemini",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )

    def generate(self, system: str, prompt: str) -> GenerationResult:
        """Single-shot generation; thin wrapper around chat()."""
        return self.chat(system, [conversations.synth_user_message(prompt)])
