"""Search behavior and billing exercised through real SDK HTTP serialization."""

import json
from unittest.mock import patch

import anthropic
import openai
import pytest

try:
    import httpx2 as httpx
except ImportError:
    import httpx

import parsing
import providers
import slack
import usage
import web_search
from backends.anthropic_text import AnthropicProvider
from backends.gemini_text import GeminiProvider
from backends.grok_text import GrokProvider
from backends.openai_text import OpenAIProvider
from tests.test_balance_enforcement import bot, invoke
from tests.test_handler_conversation import conv, continue_turn, stored
from usage import Citation, GenerationResult, ProviderGenerationError


@pytest.mark.parametrize("prompt,mode", [
    ("hello", "auto"), ("-s latest news", "required"), ("news --search", "required"),
    ("-t hello", "off"), ("--no-search hello", "off"), ("–s news", "required"),
    ("—no-search hello", "off"), ("-e hello", "off"), ("-bufo hello", "off"),
    ("-s -s hello", "required"),
])
def test_search_modes(prompt, mode):
    parsed = parsing.parse_command(prompt)
    assert parsed.search_mode == mode
    assert parsed.search_error is None
    assert "-s" not in parsed.prompt_text
    assert "-t" not in parsed.prompt_text


@pytest.mark.parametrize("prompt", ["-s -t hello", "-t -s hello", "-i -s cat", "-v -t cat", "-e -s news", "-bufo -s news"])
def test_conflicting_modes_never_call_a_provider(bot, prompt):
    invoke(prompt)
    bot.providers.get_text_provider.assert_not_called()
    bot.providers.get_image_provider.assert_not_called()
    bot.providers.get_video_provider.assert_not_called()
    bot.slack.post_ephemeral.assert_called_once()
    bot.balance.assert_not_called()


@pytest.mark.parametrize("flag,mode", [("", "auto"), ("-s", "required"), ("-t", "off"), ("-e", "off")])
def test_handler_passes_and_stores_search_mode(bot, conv, flag, mode):
    invoke(f"{flag} question")
    assert bot.providers.get_text_provider.return_value.generate.call_args.kwargs["search_mode"] == mode
    assert conv.create.call_args.kwargs["flags"]["search_mode"] == mode


@pytest.mark.parametrize("mode", ["auto", "required", "off"])
def test_continue_preserves_search_mode(bot, conv, mode):
    conv.get.return_value = stored(backend="anthropic", search_mode=mode)
    continue_turn("and tomorrow?")
    assert bot.providers.get_text_provider.return_value.chat.call_args.kwargs["search_mode"] == mode


def test_legacy_conversation_keeps_no_search_and_configured_default(bot, conv):
    conv.get.return_value = stored()
    continue_turn("more")
    bot.providers.get_text_provider.assert_called_once_with(None)
    assert bot.providers.get_text_provider.return_value.chat.call_args.kwargs["search_mode"] == "off"


def test_payment_reminder_never_searches(bot):
    bot.balance.return_value = -5.0
    invoke("-s latest news")
    assert bot.providers.get_text_provider.return_value.generate.call_args.kwargs["search_mode"] == "off"


def test_default_provider_can_search(monkeypatch):
    monkeypatch.delenv("TEXT_BACKEND", raising=False)
    assert isinstance(providers.get_text_provider(), OpenAIProvider)


@pytest.mark.parametrize("mode", ["auto", "required"])
def test_gemini_requires_explicit_no_search(mode):
    with patch("backends.gemini_text.genai.Client") as client:
        with pytest.raises(ValueError, match="-b gemini -t"):
            GeminiProvider().generate("", "hello", search_mode=mode)
        client.assert_not_called()


def sdk_client(backend, replies):
    """Use real SDK request/response handling without any network access."""
    requests = []
    replies = iter(replies)

    def handler(request):
        requests.append(json.loads(request.content))
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json=reply)

    kwargs = {"api_key": "test", "http_client": httpx.Client(transport=httpx.MockTransport(handler))}
    client = anthropic.Anthropic(**kwargs) if backend == "anthropic" else openai.OpenAI(**kwargs)
    return client, requests


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    for key in ("OPENAI_API_KEY", "OPENAI_ORGANIZATION", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.delenv("TEXT_MODEL", raising=False)


def response_payload(*, calls=None, text="Found it.", annotations=None, status="completed", **usage_fields):
    return {
        "id": "resp_1", "object": "response", "created_at": 1, "model": "test",
        "status": status,
        "output": (calls or []) + [{"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                                  "content": [{"type": "output_text", "text": text, "annotations": annotations or []}]}],
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, **usage_fields},
    }


def search_call(action="search", status="completed", identifier="search_1"):
    return {"id": identifier, "type": "web_search_call", "status": status,
            "action": {"type": action, "query": "latest news", "url": "https://example.com"}}


def run_responses(backend, reply, mode="required"):
    client, requests = sdk_client(backend, [reply])
    provider = OpenAIProvider() if backend == "openai" else GrokProvider()
    with client, patch(f"backends.{backend}_text.OpenAI", return_value=client):
        return provider.generate("Be helpful", "latest news", search_mode=mode), requests


def test_openai_counts_search_actions_and_renders_annotation():
    text = "Found it. citeturn0search0"
    marker = text.index("")
    result, requests = run_responses("openai", response_payload(
        text=text, calls=[search_call(), search_call("open_page", identifier="browse"), search_call("find_in_page", identifier="find")],
        annotations=[{"type": "url_citation", "url": "https://example.com", "title": "Example",
                      "start_index": marker, "end_index": len(text)}],
    ))
    assert requests[0]["tools"] == [{"type": "web_search"}]
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["max_tool_calls"] == 3
    assert requests[0]["store"] is False
    assert result.search_calls == 1  # Opening/reading pages is not another search fee.
    assert result.search_cost_estimate == 0.01
    assert result.cost_estimate == pytest.approx(0.0108)
    rendered = web_search.render(result, for_slack=True)
    assert "" not in rendered
    assert "<https://example.com|[1]>" in rendered


@pytest.mark.parametrize("backend", ["openai", "grok"])
def test_auto_can_skip_search_without_search_fee(backend):
    result, requests = run_responses(backend, response_payload(), mode="auto")
    assert requests[0]["tool_choice"] == "auto"
    assert result.search_calls == 0
    assert result.search_cost_estimate == 0


@pytest.mark.parametrize("backend", ["openai", "grok"])
def test_required_search_never_returns_an_unsearched_answer(backend):
    with pytest.raises(ProviderGenerationError, match="Web search did not complete") as error:
        run_responses(backend, response_payload())
    assert error.value.input_tokens == 100
    assert error.value.cost_estimate > 0


def test_grok_prefers_reported_tool_usage_and_inclusive_actual_cost():
    result, requests = run_responses("grok", response_payload(
        calls=[search_call(), search_call(status="failed", identifier="failed")],
        server_side_tool_usage_details={"web_search_calls": 1}, cost_in_usd_ticks=70_000_000,
    ))
    assert result.search_calls == 1
    assert result.search_cost_estimate == 0.005
    assert result.cost_actual == 0.007
    assert usage.effective_cost(result._asdict()) == 0.007
    assert requests[0]["max_turns"] == 3


def test_incomplete_response_keeps_billed_search_and_tokens():
    with pytest.raises(ProviderGenerationError) as error:
        run_responses("grok", response_payload(calls=[search_call()], status="incomplete", cost_in_usd_ticks=70_000_000))
    assert error.value.cost_actual == 0.007
    assert error.value.search_calls == 1
    assert error.value.input_tokens == 100


def claude_payload(*, searches=1, stop="end_turn", content=None, **usage_fields):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
        "stop_reason": stop, "stop_sequence": None,
        "content": content if content is not None else [{"type": "text", "text": "Found it.", "citations": [
            {"type": "web_search_result_location", "url": "https://example.com", "title": "Example",
             "encrypted_index": "opaque", "cited_text": "The fact"}]}],
        "usage": {"input_tokens": 100, "output_tokens": 20,
                  "server_tool_use": {"web_search_requests": searches}, **usage_fields},
    }


def run_claude(replies, mode="required"):
    client, requests = sdk_client("anthropic", replies)
    with client, patch("backends.anthropic_text.anthropic.Anthropic", return_value=client):
        return AnthropicProvider().generate("Be helpful", "latest news", search_mode=mode), requests


def test_claude_search_counts_usage_and_preserves_citations():
    result, requests = run_claude([claude_payload()])
    assert result.search_calls == 1
    assert result.cost_estimate == pytest.approx(0.0104)
    assert result.citations[0].url == "https://example.com"
    assert "<https://example.com|[1]>" in web_search.render(result, for_slack=True)
    assert requests[0]["tools"][0]["max_uses"] == 3


def test_claude_pause_preserves_state_and_aggregates_cost():
    paused = claude_payload(stop="pause_turn", content=[
        {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "news"}},
        {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": [
            {"type": "web_search_result", "url": "https://example.com", "title": "Example", "encrypted_content": "opaque"}]},
    ])
    result, requests = run_claude([paused, claude_payload()])
    assert requests[1]["messages"][-1]["content"] == paused["content"]
    assert requests[1]["tools"][0]["max_uses"] == 2
    assert result.search_calls == 2
    assert result.input_tokens == 200
    assert result.cost_estimate == pytest.approx(0.0208)


def test_claude_interruption_preserves_earlier_cost():
    with pytest.raises(ProviderGenerationError) as error:
        run_claude([claude_payload(stop="pause_turn"), httpx.ReadTimeout("timeout")])
    assert error.value.search_calls == 1
    assert error.value.cost_estimate == pytest.approx(0.0104)


def test_claude_search_limit_is_shared_across_pauses():
    with pytest.raises(ProviderGenerationError, match="reached its limit") as error:
        run_claude([claude_payload(searches=3, stop="pause_turn")])
    assert error.value.search_calls == 3
    assert error.value.search_cost_estimate == 0.03


def test_claude_cache_usage_is_included_at_correct_rates():
    result, _ = run_claude([claude_payload(cache_read_input_tokens=1000, cache_creation_input_tokens=100)])
    assert result.input_tokens == 1200
    assert result.cost_estimate == pytest.approx(0.01085)


def test_claude_required_search_failure_is_not_a_successful_answer():
    with pytest.raises(ProviderGenerationError, match="did not complete"):
        run_claude([claude_payload(searches=0, content=[{"type": "text", "text": "I cannot search."}])])


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://a|!channel", "https://a\n", "<https://a>", "https://[invalid"])
def test_untrusted_citation_urls_are_rejected(url):
    assert web_search.citation({"url": url, "title": "bad"}) is None


def cited_result():
    text = "🌎 A fact. <!channel>"
    return GenerationResult(text, "anthropic", "claude-sonnet-5", 100, 20, 0.0104,
                            search_calls=1, search_cost_estimate=0.01,
                            citations=(Citation("https://example.com?a=1&b=2", "Title <bad>|x", 0, 9),))


def test_citations_escape_slack_markup_and_preserve_unicode_offsets():
    rendered = web_search.render(cited_result(), for_slack=True)
    assert rendered.startswith("🌎 A fact. <https://example.com?a=1&amp;b=2|[1]>")
    assert "<!channel>" not in rendered
    assert "&lt;!channel&gt;" in rendered
    assert "Title &lt;bad&gt; x" in rendered


def test_slack_and_history_get_source_links_without_cost_footer(bot, conv):
    result = cited_result()
    bot.providers.get_text_provider.return_value.generate.return_value = result
    invoke("-s latest news")
    call = bot.slack.post_text_response.call_args
    assert call.kwargs["linked_sources"] is True
    assert "https://example.com" in call.args[3]
    assert "https://example.com" in conv.create.call_args.kwargs["assistant_msg"]["content"]
    assert "$" not in call.args[3]
    assert "cost" not in call.args[3].lower()
    bot.record.assert_called_once_with("bob", result)


def test_slack_enables_clickable_source_links():
    with patch("slack.requests.post") as post:
        slack.post_text_response("https://hooks/x", "bob", "news", web_search.render(cited_result(), for_slack=True), linked_sources=True)
    payload = json.loads(post.call_args.kwargs["data"])
    assert payload["attachments"][0]["mrkdwn_in"] == ["text"]
    assert "cost" not in json.dumps(payload).lower()


def test_search_usage_is_saved_and_total_includes_it_once():
    result = cited_result()
    with patch("usage._get_table") as table:
        usage.record_usage("bob", result)
    record = table.return_value.put_item.call_args.kwargs["Item"]
    assert record["search_calls"] == 1
    assert float(record["search_cost_estimate"]) == 0.01
    assert usage.effective_cost(record) == pytest.approx(0.0104)


def test_failed_search_saves_usage_and_actual_cost():
    result = cited_result()._replace(cost_actual=0.008)
    error = web_search.failure(result, "Search interrupted")
    with patch("usage._get_table") as table:
        usage.record_failed_request("bob", mode="text", backend="anthropic", exc=error)
    record = table.return_value.put_item.call_args.kwargs["Item"]
    assert record["search_calls"] == 1
    assert record["input_tokens"] == 100
    assert usage.effective_cost(record) == 0.008
