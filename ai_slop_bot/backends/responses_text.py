"""Hosted web-search requests shared by OpenAI and xAI's Responses API."""

import web_search
from usage import GenerationResult, estimate_text_cost, xai_cost_from_usage


def search_response(client, backend, model, system, messages, mode):
    """Return cited text and account for successful searches, not source URLs."""
    client = client.with_options(max_retries=0)
    api_messages = [{"role": "system", "content": web_search.instructions(system, mode)}]
    api_messages.extend(messages)
    options = {
        "model": model,
        "input": api_messages,
        "tools": [{"type": "web_search"}],
        "tool_choice": "required" if mode == "required" else "auto",
        "max_output_tokens": web_search.MAX_OUTPUT_TOKENS,
        "timeout": web_search.REQUEST_TIMEOUT,
        "store": False,
    }
    if backend == "openai":
        options["max_tool_calls"] = web_search.MAX_SEARCH_CALLS
    else:
        # xAI limits agent turns, each of which can include multiple tool calls.
        options["extra_body"] = {"max_turns": 3}
        if model == "grok-4.3":
            options["reasoning"] = {"effort": "low"}
    response = client.responses.create(**options)
    usage = response.usage
    inputs = web_search.count(web_search.field(usage, "input_tokens"))
    outputs = web_search.count(web_search.field(usage, "output_tokens"))
    searches = 0
    attempted = 0
    succeeded = 0
    text = ""
    citations = []
    for item in response.output:
        if item.type == "web_search_call":
            attempted += 1
            succeeded += int(item.status == "completed")
            action = web_search.field(web_search.field(item, "action"), "type")
            if item.status == "completed" and (backend == "grok" or action == "search"):
                searches += 1
        elif item.type == "message":
            for part in item.content:
                if part.type != "output_text":
                    continue
                offset = len(text)
                text += part.text
                for raw in web_search.field(part, "annotations", None) or []:
                    if web_search.field(raw, "type") != "url_citation":
                        continue
                    source = web_search.citation(raw)
                    if source:
                        if source.end_index >= 0:
                            source = source._replace(start_index=source.start_index + offset,
                                                     end_index=source.end_index + offset)
                        citations.append(source)
    if backend == "grok":
        details = web_search.field(usage, "server_side_tool_usage_details")
        reported = web_search.field(details, "web_search_calls")
        if type(reported) is int:
            searches = max(0, reported)
    search_cost = searches * web_search.SEARCH_RATES[backend]
    # Usage input/output includes the model's search context and reasoning.
    # xAI's reported total already covers every tool; never add fees to it.
    actual, ticks = xai_cost_from_usage(usage) if backend == "grok" else (None, None)
    result = GenerationResult(
        text, backend, model, inputs, outputs,
        estimate_text_cost(backend, inputs, outputs, model=model) + search_cost,
        cost_actual=actual, cost_in_usd_ticks=ticks,
        search_calls=searches, search_cost_estimate=search_cost, citations=tuple(citations),
    )
    if response.status != "completed":
        raise web_search.failure(result, "The answer did not finish. Try a narrower question.")
    return web_search.validate(result, mode, failed=attempted > 0 and succeeded == 0,
                               searched=succeeded > 0)
