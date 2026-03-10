"""OpenAI ChatGPT text generation backend."""

import os
import re

from openai import OpenAI


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

    def generate(self, system: str, prompt: str) -> str:
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
        return clean_response(reply)
