"""Anthropic Claude text generation backend."""

import os

import anthropic

import model_config
from usage import GenerationResult, estimate_text_cost


class AnthropicProvider:
    """Text generation using Anthropic Claude."""

    def chat(self, system: str, messages: list[dict]) -> GenerationResult:
        """Generate the next reply for a user/assistant message history."""
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = model_config.get_model("text", "anthropic")
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=list(messages),
            thinking={"type": "disabled"},
        )
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        cost = estimate_text_cost("anthropic", input_tokens, output_tokens, model=model)
        return GenerationResult(
            content="".join(block.text for block in message.content if block.type == "text"),
            backend="anthropic",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )

    def generate(self, system: str, prompt: str) -> GenerationResult:
        """Single-shot generation: a one-message chat."""
        return self.chat(system, [{"role": "user", "content": prompt}])
