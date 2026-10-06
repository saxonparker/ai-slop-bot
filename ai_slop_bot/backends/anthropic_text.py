"""Anthropic Claude text generation backend."""

import os

import anthropic

import model_config
import web_search
from usage import GenerationResult, estimate_text_cost


class AnthropicProvider:
    """Text generation using Anthropic Claude."""

    def chat(self, system: str, messages: list[dict], *, search_mode: str = "off") -> GenerationResult:
        """Generate the next reply for a user/assistant message history."""
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = model_config.get_model("text", "anthropic")
        if search_mode != "off":
            return self._search(client, model, system, messages, search_mode)
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

    def generate(self, system: str, prompt: str, *, search_mode: str = "off") -> GenerationResult:
        """Single-shot generation: a one-message chat."""
        return self.chat(system, [{"role": "user", "content": prompt}], search_mode=search_mode)

    def _search(self, client, model, system, messages, mode):
        """Run hosted searches, preserving paused tool state and all billable usage."""
        client = client.with_options(max_retries=0)
        history = list(messages)
        text = ""
        citations = []
        inputs = outputs = searches = 0
        token_cost = 0.0
        failed = False
        result = GenerationResult("", "anthropic", model, 0, 0, 0.0)
        for _ in range(3):
            try:
                message = client.messages.create(
                    model=model, system=web_search.instructions(system, mode),
                    messages=history, max_tokens=web_search.MAX_OUTPUT_TOKENS,
                    thinking={"type": "disabled"},
                    tools=[{"type": "web_search_20250305", "name": "web_search",
                            "max_uses": web_search.MAX_SEARCH_CALLS - searches}],
                    tool_choice={"type": "auto"}, timeout=web_search.REQUEST_TIMEOUT,
                )
            except Exception as exc:
                if inputs or outputs or searches:
                    raise web_search.failure(result, "Web search was interrupted. Please try again.") from exc
                raise
            usage = message.usage
            fresh = web_search.count(usage.input_tokens)
            cached = web_search.count(web_search.field(usage, "cache_read_input_tokens"))
            written = web_search.count(web_search.field(usage, "cache_creation_input_tokens"))
            output = web_search.count(usage.output_tokens)
            inputs += fresh + cached + written
            outputs += output
            token_cost += estimate_text_cost("anthropic", fresh + written * 1.25 + cached * 0.1,
                                             output, model=model)
            searches += web_search.count(web_search.field(web_search.field(usage, "server_tool_use"), "web_search_requests"))
            for block in message.content:
                if block.type == "text":
                    text += block.text
                    for raw in web_search.field(block, "citations", None) or []:
                        source = web_search.citation(raw, start=len(text), end=len(text))
                        if source:
                            citations.append(source)
                elif block.type == "web_search_tool_result":
                    content = web_search.field(block, "content")
                    if web_search.field(content, "type") == "web_search_tool_result_error":
                        failed = True
            search_cost = searches * web_search.SEARCH_RATES["anthropic"]
            result = GenerationResult(text, "anthropic", model, inputs, outputs,
                                      token_cost + search_cost, search_calls=searches,
                                      search_cost_estimate=search_cost, citations=tuple(citations))
            if message.stop_reason != "pause_turn":
                if message.stop_reason == "max_tokens":
                    raise web_search.failure(result, "The answer reached its token limit. Try a narrower question.")
                return web_search.validate(result, mode, failed=failed and searches == 0)
            if searches >= web_search.MAX_SEARCH_CALLS:
                break
            # Replay the provider's content unchanged, including encrypted search state.
            history.append({"role": "assistant", "content": message.content})
        raise web_search.failure(result, "Web search reached its limit before finishing. Try a narrower question.")
