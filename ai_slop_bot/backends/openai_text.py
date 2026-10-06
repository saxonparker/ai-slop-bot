"""OpenAI ChatGPT text generation backend."""

import os
import re

from openai import OpenAI

import model_config
from backends.responses_text import search_response
from usage import GenerationResult, estimate_text_cost


def clean_response(text: str) -> str:
    """Clean the OpenAI disclaimer nonsense from a response."""
    match = re.match(
        r"^As an AI language model, [^.;]+[.;] ((?:\n|\r|.)*)", text, re.MULTILINE
    )
    if match is not None:
        text = match.group(1)
    return text


class OpenAIProvider:
    """Text generation using OpenAI ChatGPT."""

    def chat(self, system: str, messages: list[dict], *, search_mode: str = "off") -> GenerationResult:
        """Generate the next reply for a user/assistant message history."""
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            organization=os.environ["OPENAI_ORGANIZATION"],
        )
        model = model_config.get_model("text", "openai")
        if search_mode != "off":
            return search_response(client, "openai", model, system, messages, search_mode)
        api_messages = [{"role": "system", "content": system}] if system else []
        api_messages.extend(messages)
        response = client.chat.completions.create(model=model, messages=api_messages)
        reply = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = estimate_text_cost("openai", input_tokens, output_tokens, model=model)
        return GenerationResult(
            content=clean_response(reply),
            backend="openai",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )

    def generate(self, system: str, prompt: str, *, search_mode: str = "off") -> GenerationResult:
        """Single-shot generation: a one-message chat."""
        return self.chat(system, [{"role": "user", "content": prompt}], search_mode=search_mode)
