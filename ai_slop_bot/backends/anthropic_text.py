"""Anthropic Claude text generation backend."""

import os

import anthropic

import conversations
from usage import GenerationResult, estimate_text_cost


class AnthropicProvider:
    """Text generation using Anthropic Claude."""

    def chat(self, system: str, messages: list[dict]) -> GenerationResult:
        """Generate a completion given a multi-turn message history."""
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = os.environ.get("TEXT_MODEL", "claude-sonnet-4-20250514")
        api_msgs = conversations.to_anthropic(messages)
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=api_msgs,
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

    def generate(self, system: str, prompt: str) -> GenerationResult:
        """Single-shot generation; thin wrapper around chat()."""
        return self.chat(system, [conversations.synth_user_message(prompt)])
