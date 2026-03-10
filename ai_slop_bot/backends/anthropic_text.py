"""Anthropic Claude text generation backend."""

import os

import anthropic


class AnthropicProvider:
    """Text generation using Anthropic Claude."""

    def generate(self, system: str, prompt: str) -> str:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        message = client.messages.create(
            model=os.environ.get("TEXT_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
