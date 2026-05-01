"""xAI Grok text generation backend."""

import os

from openai import OpenAI

import conversations
from usage import GenerationResult, estimate_text_cost


class GrokProvider:
    """Text generation using xAI Grok."""

    def chat(self, system: str, messages: list[dict]) -> GenerationResult:
        """Generate a completion given a multi-turn message history."""
        client = OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url="https://api.x.ai/v1",
        )
        model = os.environ.get("TEXT_MODEL", "grok-4-1-fast-non-reasoning")
        api_msgs = []
        if len(system) > 0:
            api_msgs.append({"role": "system", "content": system})
        api_msgs.extend(conversations.to_openai_chat(messages))
        response = client.chat.completions.create(model=model, messages=api_msgs)
        reply = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = estimate_text_cost("grok", input_tokens, output_tokens)
        return GenerationResult(
            content=reply,
            backend="grok",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )

    def generate(self, system: str, prompt: str) -> GenerationResult:
        """Single-shot generation; thin wrapper around chat()."""
        return self.chat(system, [conversations.synth_user_message(prompt)])
