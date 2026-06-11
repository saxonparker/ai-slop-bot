"""xAI Grok text generation backend."""

import os

from openai import OpenAI

import conversations
from usage import (
    GenerationResult,
    ProviderGenerationError,
    estimate_text_cost,
    xai_cost_from_error,
    xai_cost_from_usage,
)


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
        try:
            response = client.chat.completions.create(model=model, messages=api_msgs)
        except Exception as exc:
            cost_actual, cost_ticks = xai_cost_from_error(exc)
            raise ProviderGenerationError(
                str(exc),
                backend="grok",
                model=model,
                error_type=_classify_error(exc),
                cost_actual=cost_actual,
                cost_in_usd_ticks=cost_ticks,
            ) from exc
        reply = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = estimate_text_cost("grok", input_tokens, output_tokens)
        cost_actual, cost_ticks = xai_cost_from_usage(response.usage)
        return GenerationResult(
            content=reply,
            backend="grok",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
            cost_actual=cost_actual,
            cost_in_usd_ticks=cost_ticks,
        )

    def generate(self, system: str, prompt: str) -> GenerationResult:
        """Single-shot generation; thin wrapper around chat()."""
        return self.chat(system, [conversations.synth_user_message(prompt)])


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "moderation" in text or "safety" in text or "policy" in text:
        return "moderation"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "provider_error"
