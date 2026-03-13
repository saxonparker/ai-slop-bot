"""Anthropic Claude text generation backend."""

import os

import anthropic
from usage import GenerationResult, estimate_text_cost


class AnthropicProvider:
    """Text generation using Anthropic Claude."""

    def generate(self, system: str, prompt: str) -> GenerationResult:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = os.environ.get("TEXT_MODEL", "claude-sonnet-4-20250514")
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        cost = estimate_text_cost("anthropic", input_tokens, output_tokens)
        return GenerationResult(
            content=message.content[0].text,
            backend="anthropic",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )
