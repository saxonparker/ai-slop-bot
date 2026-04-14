"""Tests for backend .generate() methods using mocked API clients."""

import sys
from unittest.mock import MagicMock, patch

sys.path.append(".")

from usage import GenerationResult


# ── Anthropic Text ───────────────────────────────────────────────────────────

@patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
@patch("backends.anthropic_text.anthropic.Anthropic")
def test_anthropic_generate(mock_anthropic_cls):
    from backends.anthropic_text import AnthropicProvider

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_block = MagicMock()
    mock_block.text = "Hello from Claude"
    mock_message = MagicMock(content=[mock_block])
    mock_message.usage.input_tokens = 10
    mock_message.usage.output_tokens = 20
    mock_client.messages.create.return_value = mock_message

    provider = AnthropicProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert isinstance(result, GenerationResult)
    assert result.content == "Hello from Claude"
    assert result.backend == "anthropic"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.cost_estimate > 0
    mock_anthropic_cls.assert_called_once_with(api_key="fake-key")
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["system"] == "Be helpful"
    assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "What is 2+2?"}]
    assert call_kwargs.kwargs["max_tokens"] == 4096


@patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key", "TEXT_MODEL": "claude-opus-4-20250514"})
@patch("backends.anthropic_text.anthropic.Anthropic")
def test_anthropic_respects_model_override(mock_anthropic_cls):
    from backends.anthropic_text import AnthropicProvider

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_message = MagicMock(content=[MagicMock(text="ok")])
    mock_message.usage.input_tokens = 5
    mock_message.usage.output_tokens = 5
    mock_client.messages.create.return_value = mock_message

    result = AnthropicProvider().generate("sys", "prompt")

    assert result.model == "claude-opus-4-20250514"
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == "claude-opus-4-20250514"


# ── Gemini Text ──────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"GOOGLE_API_KEY": "fake-key"})
@patch("backends.gemini_text.genai.Client")
def test_gemini_text_generate(mock_client_cls):
    from backends.gemini_text import GeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_response = MagicMock(text="Hello from Gemini")
    mock_response.usage_metadata.prompt_token_count = 8
    mock_response.usage_metadata.candidates_token_count = 12
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert isinstance(result, GenerationResult)
    assert result.content == "Hello from Gemini"
    assert result.backend == "gemini"
    assert result.input_tokens == 8
    assert result.output_tokens == 12
    mock_client_cls.assert_called_once_with(api_key="fake-key")
    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs.kwargs["contents"] == "What is 2+2?"
    assert call_kwargs.kwargs["config"] == {"system_instruction": "Be helpful"}


# ── OpenAI Text ──────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"OPENAI_ORGANIZATION": "fake-org", "OPENAI_API_KEY": "fake-key"})
@patch("backends.openai_text.OpenAI")
def test_openai_text_generate(mock_openai_cls):
    from backends.openai_text import OpenAIProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello from GPT"
    mock_response = MagicMock(choices=[mock_choice])
    mock_response.usage.prompt_tokens = 15
    mock_response.usage.completion_tokens = 25
    mock_client.chat.completions.create.return_value = mock_response

    provider = OpenAIProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert isinstance(result, GenerationResult)
    assert result.content == "Hello from GPT"
    assert result.backend == "openai"
    assert result.input_tokens == 15
    assert result.output_tokens == 25
    mock_openai_cls.assert_called_once_with(api_key="fake-key", organization="fake-org")
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["messages"] == [
        {"role": "system", "content": "Be helpful"},
        {"role": "user", "content": "What is 2+2?"},
    ]


@patch.dict("os.environ", {"OPENAI_ORGANIZATION": "fake-org", "OPENAI_API_KEY": "fake-key"})
@patch("backends.openai_text.OpenAI")
def test_openai_text_no_system_when_empty(mock_openai_cls):
    from backends.openai_text import OpenAIProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_choice = MagicMock()
    mock_choice.message.content = "response"
    mock_response = MagicMock(choices=[mock_choice])
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 10
    mock_client.chat.completions.create.return_value = mock_response

    OpenAIProvider().generate("", "hello")

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "hello"}]


@patch.dict("os.environ", {"OPENAI_ORGANIZATION": "fake-org", "OPENAI_API_KEY": "fake-key"})
@patch("backends.openai_text.OpenAI")
def test_openai_text_cleans_ai_disclaimer(mock_openai_cls):
    from backends.openai_text import OpenAIProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_choice = MagicMock()
    mock_choice.message.content = "As an AI language model, I cannot do that. Here is the real answer."
    mock_response = MagicMock(choices=[mock_choice])
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 20
    mock_client.chat.completions.create.return_value = mock_response

    result = OpenAIProvider().generate("sys", "prompt")

    assert result.content == "Here is the real answer."


# ── Gemini Image ─────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"GOOGLE_API_KEY": "fake-key"})
@patch("backends.gemini_image.genai.Client")
def test_gemini_image_generate(mock_client_cls):
    from backends.gemini_image import GeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    fake_bytes = b"\x89PNG fake image data"
    mock_part = MagicMock()
    mock_part.inline_data = MagicMock(data=fake_bytes)
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_client.models.generate_content.return_value = MagicMock(candidates=[mock_candidate])

    provider = GeminiProvider()
    result = provider.generate("a sunset")

    assert isinstance(result, GenerationResult)
    assert result.content == fake_bytes
    assert result.backend == "gemini"
    assert result.cost_estimate == 0.04
    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs.kwargs["contents"] == ["a sunset"]


@patch.dict("os.environ", {"GOOGLE_API_KEY": "fake-key"})
@patch("backends.gemini_image.genai.Client")
def test_gemini_image_no_image_raises(mock_client_cls):
    from backends.gemini_image import GeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_part = MagicMock()
    mock_part.inline_data = None
    mock_part.text = "I cannot generate that image"
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_client.models.generate_content.return_value = MagicMock(candidates=[mock_candidate])

    import pytest
    with pytest.raises(RuntimeError, match="No image generated. Gemini said:"):
        GeminiProvider().generate("a sunset")


# ── OpenAI Image ─────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"OPENAI_ORGANIZATION": "fake-org", "OPENAI_API_KEY": "fake-key"})
@patch("backends.openai_image.requests.get")
@patch("backends.openai_image.OpenAI")
def test_openai_image_generate(mock_openai_cls, mock_requests_get):
    from backends.openai_image import OpenAIProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.images.generate.return_value = MagicMock(
        data=[MagicMock(url="https://fake-url.com/image.png")]
    )
    fake_bytes = b"\x89PNG fake image data"
    mock_requests_get.return_value = MagicMock(content=fake_bytes)

    provider = OpenAIProvider()
    result = provider.generate("a cat")

    assert isinstance(result, GenerationResult)
    assert result.content == fake_bytes
    assert result.backend == "openai"
    assert result.cost_estimate == 0.08
    mock_client.images.generate.assert_called_once_with(
        prompt="a cat", n=1, size="1024x1024", model="dall-e-3", quality="hd"
    )
    mock_requests_get.assert_called_once_with("https://fake-url.com/image.png", timeout=10000)


# ── Grok Text ───────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"XAI_API_KEY": "fake-key"})
@patch("backends.grok_text.OpenAI")
def test_grok_text_generate(mock_openai_cls):
    from backends.grok_text import GrokProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello from Grok"
    mock_response = MagicMock(choices=[mock_choice])
    mock_response.usage.prompt_tokens = 12
    mock_response.usage.completion_tokens = 18
    mock_client.chat.completions.create.return_value = mock_response

    provider = GrokProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert isinstance(result, GenerationResult)
    assert result.content == "Hello from Grok"
    assert result.backend == "grok"
    assert result.input_tokens == 12
    assert result.output_tokens == 18
    assert result.cost_estimate > 0
    mock_openai_cls.assert_called_once_with(api_key="fake-key", base_url="https://api.x.ai/v1")
    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["messages"] == [
        {"role": "system", "content": "Be helpful"},
        {"role": "user", "content": "What is 2+2?"},
    ]


@patch.dict("os.environ", {"XAI_API_KEY": "fake-key"})
@patch("backends.grok_text.OpenAI")
def test_grok_text_no_system_when_empty(mock_openai_cls):
    from backends.grok_text import GrokProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_choice = MagicMock()
    mock_choice.message.content = "response"
    mock_response = MagicMock(choices=[mock_choice])
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 10
    mock_client.chat.completions.create.return_value = mock_response

    GrokProvider().generate("", "hello")

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "hello"}]


# ── Grok Image ──────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"XAI_API_KEY": "fake-key"})
@patch("backends.grok_image.requests.get")
@patch("backends.grok_image.OpenAI")
def test_grok_image_generate(mock_openai_cls, mock_requests_get):
    from backends.grok_image import GrokProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.images.generate.return_value = MagicMock(
        data=[MagicMock(url="https://fake-url.com/grok-image.png")]
    )
    fake_bytes = b"\x89PNG fake grok image"
    mock_requests_get.return_value = MagicMock(content=fake_bytes)

    provider = GrokProvider()
    result = provider.generate("a cat")

    assert isinstance(result, GenerationResult)
    assert result.content == fake_bytes
    assert result.backend == "grok"
    assert result.cost_estimate == 0.02
    mock_openai_cls.assert_called_once_with(api_key="fake-key", base_url="https://api.x.ai/v1")
    call_args = mock_client.images.generate.call_args
    assert call_args.kwargs["prompt"].endswith("a cat")
    assert "Never place the user's prompt" in call_args.kwargs["prompt"]
    mock_requests_get.assert_called_once_with("https://fake-url.com/grok-image.png", timeout=10000)


# ── Grok Video ──────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"XAI_API_KEY": "fake-key"})
@patch("backends.grok_video.requests")
def test_grok_video_generate(mock_requests):
    from backends.grok_video import GrokProvider

    # Mock the POST to submit generation
    mock_submit = MagicMock()
    mock_submit.json.return_value = {"request_id": "req-123"}
    mock_submit.raise_for_status = MagicMock()

    # Mock the GET to poll status
    mock_status = MagicMock()
    mock_status.json.return_value = {
        "status": "done",
        "video": {"url": "https://vidgen.x.ai/video.mp4", "duration": 8},
        "model": "grok-imagine-video",
    }
    mock_status.raise_for_status = MagicMock()

    # Mock the GET to download video
    fake_bytes = b"\x00\x00\x00\x1cftypisom"
    mock_download = MagicMock(content=fake_bytes)

    mock_requests.post.return_value = mock_submit
    mock_requests.get.side_effect = [mock_status, mock_download]

    provider = GrokProvider()
    with patch("backends.grok_video.time.sleep"):
        result = provider.generate("a dancing cat")

    assert isinstance(result, GenerationResult)
    assert result.content == fake_bytes
    assert result.backend == "grok"
    assert result.model == "grok-imagine-video"
    assert result.cost_estimate == 8 * 0.05
    # Default duration (10) should be sent in request
    post_kwargs = mock_requests.post.call_args
    assert post_kwargs.kwargs["json"]["duration"] == 10


@patch.dict("os.environ", {"XAI_API_KEY": "fake-key"})
@patch("backends.grok_video.requests")
def test_grok_video_custom_duration(mock_requests):
    from backends.grok_video import GrokProvider

    mock_submit = MagicMock()
    mock_submit.json.return_value = {"request_id": "req-789"}
    mock_submit.raise_for_status = MagicMock()

    mock_status = MagicMock()
    mock_status.json.return_value = {
        "status": "done",
        "video": {"url": "https://vidgen.x.ai/video.mp4", "duration": 5},
        "model": "grok-imagine-video",
    }
    mock_status.raise_for_status = MagicMock()

    fake_bytes = b"\x00\x00\x00\x1cftypisom"
    mock_download = MagicMock(content=fake_bytes)

    mock_requests.post.return_value = mock_submit
    mock_requests.get.side_effect = [mock_status, mock_download]

    with patch("backends.grok_video.time.sleep"):
        result = GrokProvider().generate("a dancing cat", duration=5)

    assert result.cost_estimate == 5 * 0.05
    post_kwargs = mock_requests.post.call_args
    assert post_kwargs.kwargs["json"]["duration"] == 5


@patch.dict("os.environ", {"XAI_API_KEY": "fake-key"})
@patch("backends.grok_video.requests")
def test_grok_video_failed_raises(mock_requests):
    from backends.grok_video import GrokProvider
    import pytest

    mock_submit = MagicMock()
    mock_submit.json.return_value = {"request_id": "req-456"}
    mock_submit.raise_for_status = MagicMock()

    mock_status = MagicMock()
    mock_status.json.return_value = {"status": "failed"}
    mock_status.raise_for_status = MagicMock()

    mock_requests.post.return_value = mock_submit
    mock_requests.get.return_value = mock_status

    with patch("backends.grok_video.time.sleep"):
        with pytest.raises(RuntimeError, match="Video generation failed"):
            GrokProvider().generate("a dancing cat")
