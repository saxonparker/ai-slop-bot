"""xAI Grok text generation backend."""

import os

from openai import OpenAI

import model_config
from usage import (
    GenerationResult,
    ProviderGenerationError,
    classify_xai_error,
    estimate_text_cost,
    xai_cost_from_error,
    xai_cost_from_usage,
)


class GrokProvider:
    """Text generation using xAI Grok."""

    def generate(self, system: str, prompt: str) -> GenerationResult:
        client = OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url="https://api.x.ai/v1",
        )
        model = model_config.get_model("text", "grok")
        messages = []
        if len(system) > 0:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # Preserve the old non-reasoning workload when replacing its retired ID.
        options = {"reasoning_effort": "none"} if model == "grok-4.3" else {}
        try:
            response = client.chat.completions.create(model=model, messages=messages, **options)
        except Exception as exc:
            cost_actual, cost_ticks = xai_cost_from_error(exc)
            error_type, user_message = classify_xai_error(exc)
            raise ProviderGenerationError(
                str(exc),
                backend="grok",
                model=model,
                error_type=error_type,
                user_message=user_message,
                cost_actual=cost_actual,
                cost_in_usd_ticks=cost_ticks,
            ) from exc
        reply = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = estimate_text_cost("grok", input_tokens, output_tokens, model=model)
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
