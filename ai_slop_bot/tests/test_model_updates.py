"""Regression coverage for model migrations and provider response contracts."""

import base64
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(".")

import ai_slop_bot
import model_config
import usage
from media_refs import ResolvedImage


@pytest.fixture(autouse=True)
def model_environment(monkeypatch):
    for key in ("TEXT_MODEL", "IMAGE_MODEL", "VIDEO_MODEL", "OPENAI_IMAGE_MODEL", "OPENAI_IMAGE_EDIT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_ORGANIZATION", "fake-org")


@pytest.mark.parametrize("mode,backend,env,value,edit", [
    ("text", "gemini", "TEXT_MODEL", "custom-text", False),
    ("image", "grok", "IMAGE_MODEL", "custom-image", False),
    ("video", "grok", "VIDEO_MODEL", "custom-video", False),
    ("image", "openai", "OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst", False),
    ("image", "openai", "OPENAI_IMAGE_EDIT_MODEL", "gpt-image-2.5-flare", True),
])
def test_model_overrides_and_failure_labels(monkeypatch, mode, backend, env, value, edit):
    monkeypatch.setenv(env, value)
    assert model_config.get_model(mode, backend, image_edit=edit) == value
    assert ai_slop_bot._model_for_request(mode, backend, image_edit=edit) == value


def test_openai_overrides_do_not_change_other_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
    assert model_config.get_model("image", "grok") == "grok-imagine-image-2.0"
    monkeypatch.setenv("IMAGE_MODEL", "explicit-global-override")
    assert model_config.get_model("image", "openai") == "explicit-global-override"
    assert model_config.get_model("image", "openai", image_edit=True) == "gpt-image-2.5-sunburst"


def image_response():
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(b"image-bytes").decode(), url=None)],
        usage=SimpleNamespace(
            input_tokens=120, output_tokens=1000,
            input_tokens_details=SimpleNamespace(text_tokens=20, image_tokens=100),
        ),
    )


@pytest.mark.parametrize("model", [
    "gpt-image-2.5-flare", "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare-2026-09-08", "gpt-image-2.5-sunburst-2026-09-08",
])
@patch("backends.openai_image.requests.get")
@patch("backends.openai_image.OpenAI")
def test_gpt_image_generation_uses_base64_and_token_cost(mock_client_cls, mock_get, monkeypatch, model):
    from backends.openai_image import OpenAIProvider

    monkeypatch.setenv("OPENAI_IMAGE_MODEL", model)
    client = mock_client_cls.return_value
    client.images.generate.return_value = image_response()

    result = OpenAIProvider().generate("a cat")

    client.images.generate.assert_called_once_with(
        prompt="a cat", n=1, size="1024x1024", model=model, quality="medium",
    )
    mock_get.assert_not_called()
    assert result.content == b"image-bytes"
    assert result.model == model
    assert result.input_tokens == 120
    assert result.output_tokens == 1000
    assert result.cost_estimate == pytest.approx(0.0309)
    assert result.cost_actual is None


@patch("backends.openai_image.OpenAI")
def test_openai_edit_preserves_multiple_reference_files_and_override(mock_client_cls, monkeypatch):
    from backends.openai_image import OpenAIProvider

    monkeypatch.setenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image-2.5-flare")
    client = mock_client_cls.return_value
    client.images.edit.return_value = image_response()

    result = OpenAIProvider().generate("combine", references=[
        ResolvedImage(data=b"first", mime_type="image/png"),
        ResolvedImage(data=b"second", mime_type="image/jpeg"),
    ])

    params = client.images.edit.call_args.kwargs
    assert params["model"] == "gpt-image-2.5-flare"
    assert params["quality"] == "medium"
    assert [file.name for file in params["image"]] == ["reference-0.png", "reference-1.jpg"]
    assert [file.getvalue() for file in params["image"]] == [b"first", b"second"]
    assert result.content == b"image-bytes"
    assert result.cost_estimate == pytest.approx(0.0309)
    client.images.generate.assert_not_called()


@patch("backends.openai_image.OpenAI")
def test_legacy_openai_override_keeps_legacy_parameters(mock_client_cls, monkeypatch):
    from backends.openai_image import OpenAIProvider

    monkeypatch.setenv("IMAGE_MODEL", "dall-e-3")
    client = mock_client_cls.return_value
    client.images.generate.return_value = image_response()

    result = OpenAIProvider().generate("a cat")

    assert client.images.generate.call_args.kwargs["quality"] == "hd"
    assert result.model == "dall-e-3"
    assert result.cost_estimate == 0.08


@patch("backends.openai_image.OpenAI")
def test_openai_empty_image_result_is_an_error(mock_client_cls):
    from backends.openai_image import OpenAIProvider

    mock_client_cls.return_value.images.generate.return_value = SimpleNamespace(data=[])
    with pytest.raises(RuntimeError, match="OpenAI returned no image"):
        OpenAIProvider().generate("a cat")


@patch("backends.anthropic_text.anthropic.Anthropic")
def test_sonnet_preserves_non_thinking_behavior_and_reads_text_blocks(mock_client_cls, monkeypatch):
    from backends.anthropic_text import AnthropicProvider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    client = mock_client_cls.return_value
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text="hello"),
                 SimpleNamespace(type="text", text=" world")],
        usage=SimpleNamespace(input_tokens=100, output_tokens=200),
    )

    result = AnthropicProvider().generate("system", "prompt")

    params = client.messages.create.call_args.kwargs
    assert params["model"] == "claude-sonnet-5"
    assert params["thinking"] == {"type": "disabled"}
    assert result.content == "hello world"
    assert result.cost_estimate == pytest.approx(0.0022)


@patch("backends.gemini_text.genai.Client")
def test_gemini_counts_billable_thinking_tokens(mock_client_cls, monkeypatch):
    from backends.gemini_text import GeminiProvider

    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    client = mock_client_cls.return_value
    client.models.generate_content.return_value = SimpleNamespace(
        text="hello", usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=20, thoughts_token_count=80,
        ),
    )

    with patch("usage.datetime") as clock:
        clock.now.return_value = datetime(2026, 9, 21, tzinfo=timezone.utc)
        result = GeminiProvider().generate("system", "prompt")

    assert result.model == "gemini-3.8-flash"
    assert result.output_tokens == 100
    assert result.cost_estimate == pytest.approx(0.00045)


@patch("backends.grok_text.OpenAI")
def test_grok_replacement_keeps_reasoning_disabled(mock_client_cls, monkeypatch):
    from backends.grok_text import GrokProvider

    monkeypatch.setenv("XAI_API_KEY", "fake-key")
    client = mock_client_cls.return_value
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200),
    )

    result = GrokProvider().generate("system", "prompt")

    assert client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "none"
    assert result.model == "grok-4.3"
    assert result.cost_estimate == pytest.approx(0.000625)


@pytest.mark.parametrize("model,expected", [
    ("gpt-5.6-sol", 24), ("gpt-5.5", 35), ("gpt-5.6-luna", 1.4),
    ("claude-sonnet-5", 12), ("claude-sonnet-4-6", 18),
    ("grok-4.3", 3.75), ("grok-4-1-fast-non-reasoning", 3.75),
])
def test_model_specific_rates(model, expected):
    # Keep Sol below its long-context threshold by pricing 100K of each token type.
    assert usage.estimate_text_cost("openai", 100_000, 100_000, model=model) == pytest.approx(expected / 10)


@pytest.mark.parametrize("year,expected", [(2026, 4.5), (2027, 9.0)])
def test_gemini_published_price_transition(year, expected):
    with patch("usage.datetime") as clock:
        clock.now.return_value = datetime(year, 1, 1, tzinfo=timezone.utc)
        cost = usage.estimate_text_cost("gemini", 1_000_000, 1_000_000, model="gemini-3.8-flash")
    assert cost == expected


def test_sol_long_context_pricing():
    cost = usage.estimate_text_cost("openai", 300_000, 1000, model="gpt-5.6-sol")
    assert cost == pytest.approx(2.43)


@pytest.mark.parametrize("api_usage", [None, SimpleNamespace(output_tokens=1000)])
def test_image_cost_falls_back_when_usage_is_missing(api_usage):
    assert usage.estimate_openai_image_cost("gpt-image-2.5-flare", api_usage) == 0.08


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("480", "480p"), ("720", "720p"), ("1080", "1080p"),
        ("480p", "480p"), ("720P", "720p"), (" 1080p ", "1080p"),
    ],
)
def test_normalize_resolution_canonicalizes_supported_sizes(value, expected):
    assert model_config.normalize_resolution(value) == expected


@pytest.mark.parametrize("value", [None, "", "1440", "4k", "720px", "p", "-720"])
def test_normalize_resolution_rejects_unsupported_sizes(value):
    assert model_config.normalize_resolution(value) is None


def test_video_resolutions_are_ordered_low_to_high():
    # grok_video clamps by index, so the ordering is load-bearing.
    assert model_config.VIDEO_RESOLUTIONS == ("480p", "720p", "1080p")
    assert model_config.DEFAULT_VIDEO_RESOLUTION in model_config.VIDEO_RESOLUTIONS


@pytest.mark.parametrize(
    ("resolution", "rate"),
    [("480p", 0.08), ("720p", 0.14), ("1080p", 0.25)],
)
def test_grok_video_rate_scales_with_resolution(resolution, rate):
    assert usage.video_cost_per_second("grok", resolution) == rate


def test_grok_video_rate_defaults_to_the_default_resolution():
    # An unknown or missing resolution must not silently bill at the cheap tier.
    default_rate = usage.GROK_VIDEO_COST_PER_SECOND[model_config.DEFAULT_VIDEO_RESOLUTION]
    assert usage.video_cost_per_second("grok") == default_rate
    assert usage.video_cost_per_second("grok", "4k") == default_rate


def test_every_video_resolution_has_a_rate():
    assert set(usage.GROK_VIDEO_COST_PER_SECOND) == set(model_config.VIDEO_RESOLUTIONS)


@pytest.mark.parametrize(
    ("requested", "has_references", "expected"),
    [
        (None, False, "1080p"),
        ("480p", False, "480p"),
        ("1080p", True, "720p"),
        ("480p", True, "480p"),
        (None, True, "720p"),
    ],
)
def test_resolve_video_resolution_applies_the_reference_cap(
    requested, has_references, expected,
):
    resolved = model_config.resolve_video_resolution(
        requested, has_references=has_references,
    )
    assert resolved == expected


@pytest.mark.parametrize(
    ("resolution", "rate"),
    [("480p", 0.08), ("720p", 0.14), ("1080p", 0.25)],
)
def test_failure_estimate_uses_the_requested_resolution(resolution, rate):
    estimate = ai_slop_bot._failure_cost_estimate(
        "video", "grok", duration=10, resolution=resolution,
    )
    assert estimate == pytest.approx(10 * rate)


def test_failure_estimate_applies_the_reference_clamp():
    estimate = ai_slop_bot._failure_cost_estimate(
        "video", "grok", duration=10, resolution="1080p", has_references=True,
    )
    assert estimate == pytest.approx(10 * 0.14)


@pytest.mark.parametrize("video_op", ["edit", "extend"])
def test_failure_estimate_for_edit_and_extend_uses_the_720p_cap(video_op):
    estimate = ai_slop_bot._failure_cost_estimate(
        "video", "grok", duration=10, video_op=video_op,
    )
    assert estimate == pytest.approx(10 * 0.14)


def test_failure_estimate_matches_the_backend_estimate():
    # The pre-flight estimate and the backend's own estimate must agree, or a
    # failed request is recorded at a different price than a successful one.
    from backends.grok_video import _resolution_for

    rendered = _resolution_for([], [], "720p")
    assert ai_slop_bot._failure_cost_estimate(
        "video", "grok", duration=10, resolution="720p",
    ) == pytest.approx(10 * usage.video_cost_per_second("grok", rendered))
