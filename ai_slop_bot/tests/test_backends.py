"""Tests for backend .generate() methods using mocked API clients."""

import sys
from unittest.mock import MagicMock, patch

sys.path.append(".")


# ── Anthropic Text ───────────────────────────────────────────────────────────

@patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
@patch("backends.anthropic_text.anthropic.Anthropic")
def test_anthropic_generate(mock_anthropic_cls):
    from backends.anthropic_text import AnthropicProvider

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_block = MagicMock()
    mock_block.text = "Hello from Claude"
    mock_client.messages.create.return_value = MagicMock(content=[mock_block])

    provider = AnthropicProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert result == "Hello from Claude"
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
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="ok")])

    AnthropicProvider().generate("sys", "prompt")

    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == "claude-opus-4-20250514"


# ── Gemini Text ──────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"GOOGLE_API_KEY": "fake-key"})
@patch("backends.gemini_text.genai.Client")
def test_gemini_text_generate(mock_client_cls):
    from backends.gemini_text import GeminiProvider

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value = MagicMock(text="Hello from Gemini")

    provider = GeminiProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert result == "Hello from Gemini"
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
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    provider = OpenAIProvider()
    result = provider.generate("Be helpful", "What is 2+2?")

    assert result == "Hello from GPT"
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
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

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
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = OpenAIProvider().generate("sys", "prompt")

    assert result == "Here is the real answer."


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

    assert result == fake_bytes
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
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_client.models.generate_content.return_value = MagicMock(candidates=[mock_candidate])

    import pytest
    with pytest.raises(RuntimeError, match="No image was generated"):
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

    assert result == fake_bytes
    mock_client.images.generate.assert_called_once_with(
        prompt="a cat", n=1, size="1024x1024", model="dall-e-3", quality="hd"
    )
    mock_requests_get.assert_called_once_with("https://fake-url.com/image.png", timeout=10000)
