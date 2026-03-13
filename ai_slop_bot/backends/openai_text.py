"""OpenAI ChatGPT text generation backend."""

import os
import re

from openai import OpenAI
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

    def generate(self, system: str, prompt: str) -> GenerationResult:
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            organization=os.environ["OPENAI_ORGANIZATION"],
        )
        model = os.environ.get("TEXT_MODEL", "gpt-5")
        messages = []
        if len(system) > 0:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(model=model, messages=messages)
        reply = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = estimate_text_cost("openai", input_tokens, output_tokens)
        return GenerationResult(
            content=clean_response(reply),
            backend="openai",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )
