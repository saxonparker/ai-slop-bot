"""Orchestration tests for the ai_slop_bot Lambda handler: media references,
video edit/extend validation, resolution flags, provider failure recording,
and user-facing error descriptions. The providers / slack / usage modules are
mocked at import boundaries.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(".")

import ai_slop_bot  # noqa: E402  pylint: disable=wrong-import-position
from media_refs import ReferenceImage, ResolvedImage, ResolvedVideo  # noqa: E402  pylint: disable=wrong-import-position
from parsing import ParsedCommand  # noqa: E402  pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def sufficient_balance():
    """Keep orchestration tests independent of live billing data."""
    with patch("ai_slop_bot.budget.get_balance", return_value=0.0):
        yield


def _result(content="follow-up reply"):
    from usage import GenerationResult
    return GenerationResult(
        content=content, backend="anthropic", model="claude-sonnet-4",
        input_tokens=20, output_tokens=10, cost_estimate=0.001,
    )


@patch("ai_slop_bot.usage.record_failed_request")
@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_image_provider")
@patch("ai_slop_bot.prompts.sanitize_prompt", side_effect=lambda p, *_, **__: p)
def test_image_provider_failure_records_failed_request(
    _mock_sanitize, mock_get_provider, mock_slack, mock_record, mock_failed,
):
    provider = MagicMock()
    provider.generate.side_effect = RuntimeError("blocked by moderation")
    mock_get_provider.return_value = provider
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "",
        "prompt": "-i make art", "user": "alice",
        "source": "slash",
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-A"))

    mock_record.assert_not_called()
    mock_failed.assert_called_once()
    args = mock_failed.call_args.args
    kwargs = mock_failed.call_args.kwargs
    assert args[0] == "alice"
    assert kwargs["mode"] == "image"
    assert kwargs["backend"] == "grok"
    assert kwargs["error_type"] == "moderation"
    assert kwargs["cost_estimate"] == 0.04
    mock_slack.post_error.assert_called_once()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_image_provider")
@patch("ai_slop_bot.image_upload.upload_to_s3", return_value="https://img/url")
@patch("ai_slop_bot.prompts.sanitize_prompt", side_effect=lambda p, *_, **__: p)
@patch("ai_slop_bot.media_refs.resolve_reference_images")
def test_image_payload_references_are_resolved_and_passed_to_provider(
    mock_resolve, _mock_sanitize, _mock_upload, mock_get_provider, _mock_slack,
    _mock_record,
):
    provider = MagicMock()
    provider.generate.return_value = _result(content=b"img-bytes")
    mock_get_provider.return_value = provider
    resolved = [ResolvedImage(data=b"ref", mime_type="image/jpeg")]
    mock_resolve.return_value = resolved
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "",
        "prompt": "-i make art", "user": "alice",
        "source": "slash",
        "reference_images": [{"source": "slack_file", "value": "F123", "role": "edit"}],
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-A"))

    provider.generate.assert_called_once_with("make art", references=resolved)


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_video_provider")
@patch("ai_slop_bot.image_upload.upload_to_s3", return_value="https://vid/url")
@patch("ai_slop_bot.prompts.sanitize_prompt", side_effect=lambda p, *_, **__: p)
@patch("ai_slop_bot.media_refs.resolve_reference_images", return_value=[])
@patch("ai_slop_bot.media_refs.resolve_reference_image")
def test_video_start_image_is_resolved_and_passed_to_provider(
    mock_resolve_one, _mock_resolve_many, _mock_sanitize, _mock_upload,
    mock_get_provider, _mock_slack, _mock_record,
):
    provider = MagicMock()
    provider.generate.return_value = _result(content=b"video-bytes")
    mock_get_provider.return_value = provider
    resolved = ResolvedImage(data=b"ref", mime_type="image/jpeg")
    mock_resolve_one.return_value = resolved
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "",
        "prompt": "-v 10 make it move", "user": "alice",
        "source": "slash",
        "reference_images": [{"source": "slack_file", "value": "F123", "role": "start"}],
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-A"))

    provider.generate.assert_called_once_with(
        "make it move",
        duration=10,
        source_image=resolved,
        references=[],
        voices=[],
        video_op=None,
        video_url=None,
        resolution=None,
    )


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_video_provider")
@patch("ai_slop_bot.image_upload.upload_to_s3", return_value="https://vid/url")
@patch("ai_slop_bot.prompts.sanitize_prompt", side_effect=lambda p, *_, **__: p)
@patch("ai_slop_bot.media_refs.resolve_reference_images")
@patch("ai_slop_bot.media_refs.resolve_reference_image")
def test_video_edit_is_passed_to_provider_without_image_refs(
    mock_resolve_one, mock_resolve_many, _mock_sanitize, mock_upload,
    mock_get_provider, _mock_slack, _mock_record,
):
    provider = MagicMock()
    provider.generate.return_value = _result(content=b"video-bytes")
    mock_get_provider.return_value = provider
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "",
        "prompt": "-v --edit-video https://example.com/source.mp4 make it rain",
        "user": "alice", "source": "slash",
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    with patch.dict("ai_slop_bot.os.environ", {"VIDEO_BACKEND": "grok"}):
        ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-A"))

    mock_resolve_one.assert_not_called()
    mock_resolve_many.assert_not_called()
    provider.generate.assert_called_once_with(
        "make it rain",
        duration=None,
        source_image=None,
        references=[],
        voices=[],
        video_op="edit",
        video_url="https://example.com/source.mp4",
        resolution=None,
    )
    assert mock_upload.call_args.kwargs["extension"] == "mp4"


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_video_provider")
@patch(
    "ai_slop_bot.image_upload.upload_to_s3",
    side_effect=["https://cdn.example/source.mp4", "https://cdn.example/generated.mp4"],
)
@patch("ai_slop_bot.prompts.sanitize_prompt", side_effect=lambda p, *_, **__: p)
@patch("ai_slop_bot.media_refs.resolve_reference_images")
@patch("ai_slop_bot.media_refs.resolve_reference_image")
@patch("ai_slop_bot.media_refs.resolve_reference_video")
def test_uploaded_source_video_is_uploaded_and_passed_to_provider(
    mock_resolve_video, mock_resolve_one, mock_resolve_many, _mock_sanitize,
    mock_upload, mock_get_provider, _mock_slack, _mock_record,
):
    provider = MagicMock()
    provider.generate.return_value = _result(content=b"video-bytes")
    mock_get_provider.return_value = provider
    mock_resolve_video.return_value = ResolvedVideo(
        data=b"source-video",
        mime_type="video/mp4",
        extension="mp4",
        role="edit",
        source="slack_file",
        file_id="FV123",
    )
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "general",
        "prompt": "-v 12 -b grok make it rain",
        "user": "alice", "source": "slash",
        "source_video": {
            "source": "slack_file",
            "value": "FV123",
            "role": "edit",
            "mime_type": "video/mp4",
            "filename": "source.mp4",
        },
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-A"))

    mock_resolve_one.assert_not_called()
    mock_resolve_many.assert_not_called()
    mock_resolve_video.assert_called_once()
    first_upload = mock_upload.call_args_list[0]
    assert first_upload.args[:2] == ("make it rain", b"source-video")
    assert first_upload.kwargs["extension"] == "mp4"
    assert first_upload.kwargs["model"] == "source-video"
    assert first_upload.kwargs["s3_prefix"] == ai_slop_bot.image_upload.SOURCE_VIDEO_PREFIX
    assert first_upload.kwargs["add_to_manifest"] is False
    provider.generate.assert_called_once_with(
        "make it rain",
        duration=12,
        source_image=None,
        references=[],
        voices=[],
        video_op="edit",
        video_url="https://cdn.example/source.mp4",
        resolution=None,
    )
    assert mock_upload.call_args_list[1].kwargs["extension"] == "mp4"


def test_video_edit_rejects_image_references():
    parsed = ParsedCommand(
        mode="video",
        video_op="edit",
        video_source_url="https://example.com/source.mp4",
    )
    reference = ReferenceImage(
        source="url",
        value="https://example.com/frame.jpg",
        role="reference",
    )

    error = ai_slop_bot._validate_media_references(parsed, None, [reference])

    assert error == "--edit-video/--extend-video cannot be combined with --start, --ref, or --edit."


@pytest.mark.parametrize(
    ("video_op", "source_ref", "reference_refs"),
    [
        (
            "edit",
            ReferenceImage(source="url", value="https://example.com/start.jpg", role="start"),
            [],
        ),
        (
            "edit",
            None,
            [ReferenceImage(source="url", value="https://example.com/ref.jpg", role="reference")],
        ),
        (
            "extend",
            ReferenceImage(source="url", value="https://example.com/start.jpg", role="start"),
            [],
        ),
        (
            "extend",
            None,
            [ReferenceImage(source="url", value="https://example.com/ref.jpg", role="reference")],
        ),
    ],
)
def test_video_edit_extend_rejects_start_and_ref_references(
    video_op, source_ref, reference_refs,
):
    parsed = ParsedCommand(
        mode="video",
        video_op=video_op,
        video_source_url="https://example.com/source.mp4",
        backend_override="grok",
    )

    error = ai_slop_bot._validate_media_references(parsed, source_ref, reference_refs)

    assert error == "--edit-video/--extend-video cannot be combined with --start, --ref, or --edit."


@pytest.mark.parametrize("video_op", ["edit", "extend"])
def test_video_edit_extend_rejects_non_grok_backend_override(video_op):
    parsed = ParsedCommand(
        mode="video",
        video_op=video_op,
        video_source_url="https://example.com/source.mp4",
        backend_override="gemini",
    )

    error = ai_slop_bot._validate_media_references(parsed, None, [])

    assert error == "Video edit/extend is only supported on the grok backend; use -b grok."


@pytest.mark.parametrize("video_op", ["edit", "extend"])
def test_video_edit_extend_accepts_grok_backend_override(monkeypatch, video_op):
    monkeypatch.setenv("VIDEO_BACKEND", "gemini")
    parsed = ParsedCommand(
        mode="video",
        video_op=video_op,
        video_source_url="https://example.com/source.mp4",
        backend_override="grok",
    )

    error = ai_slop_bot._validate_media_references(parsed, None, [])

    assert error is None


def test_video_extend_rejects_non_grok_backend(monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "gemini")
    parsed = ParsedCommand(
        mode="video",
        video_op="extend",
        video_source_url="https://example.com/source.mp4",
    )

    error = ai_slop_bot._validate_media_references(parsed, None, [])

    assert error == "Video edit/extend is only supported on the grok backend; use -b grok."


# ── _describe_error_for_user ─────────────────────────────────────────────────

def test_describe_error_uses_provider_user_message():
    from usage import ProviderGenerationError

    exc = ProviderGenerationError(
        "raw provider text", backend="grok", error_type="moderation",
        user_message="Grok declined to generate this — flagged by content moderation.",
    )
    assert ai_slop_bot._describe_error_for_user(exc) == (
        "Grok declined to generate this — flagged by content moderation."
    )


def test_describe_error_falls_back_to_raw_message():
    exc = RuntimeError("some unrelated bug")
    assert ai_slop_bot._describe_error_for_user(exc) == "some unrelated bug"


def test_describe_error_appends_billed_cost():
    from usage import ProviderGenerationError

    exc = ProviderGenerationError(
        "raw provider text", backend="grok", error_type="moderation",
        user_message="Grok declined to generate this.",
        cost_actual=0.05,
    )
    assert ai_slop_bot._describe_error_for_user(exc) == (
        "Grok declined to generate this. Cost: $0.05"
    )


def test_describe_error_omits_cost_when_unbilled():
    from usage import ProviderGenerationError

    exc = ProviderGenerationError(
        "raw provider text", backend="grok", error_type="invalid_request",
        user_message="Grok rejected the request as malformed.",
    )
    assert ai_slop_bot._describe_error_for_user(exc) == (
        "Grok rejected the request as malformed."
    )


def test_resolution_passes_validation_for_grok_video():
    parsed = ParsedCommand(mode="video", video_resolution="720p")
    assert ai_slop_bot._validate_video_resolution(parsed) is None


def test_resolution_validation_surfaces_the_parse_error():
    parsed = ParsedCommand(mode="video", resolution_error="bad resolution")
    assert ai_slop_bot._validate_video_resolution(parsed) == "bad resolution"


@pytest.mark.parametrize("mode", ["text", "image"])
def test_resolution_is_rejected_outside_video_mode(mode):
    parsed = ParsedCommand(mode=mode, video_resolution="720p")
    assert ai_slop_bot._validate_video_resolution(parsed) == "-r can only be used with -v."


@pytest.mark.parametrize("video_op", ["edit", "extend"])
def test_resolution_is_rejected_for_video_edit_and_extend(video_op):
    parsed = ParsedCommand(mode="video", video_resolution="720p", video_op=video_op)
    assert ai_slop_bot._validate_video_resolution(parsed) == (
        "-r cannot be combined with --edit-video or --extend-video."
    )


def test_resolution_is_rejected_on_the_gemini_backend():
    parsed = ParsedCommand(mode="video", video_resolution="720p", backend_override="gemini")
    assert ai_slop_bot._validate_video_resolution(parsed) == (
        "-r is only supported on the grok backend; use -b grok."
    )


@patch.dict("os.environ", {"VIDEO_BACKEND": "gemini"})
def test_resolution_is_rejected_when_gemini_is_the_env_default():
    parsed = ParsedCommand(mode="video", video_resolution="720p")
    assert ai_slop_bot._validate_video_resolution(parsed) == (
        "-r is only supported on the grok backend; use -b grok."
    )


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_video_provider")
@patch("ai_slop_bot.image_upload.upload_to_s3", return_value="https://vid/url")
@patch("ai_slop_bot.prompts.sanitize_prompt", side_effect=lambda p, *_, **__: p)
@patch("ai_slop_bot.media_refs.resolve_reference_images", return_value=[])
def test_video_resolution_flag_reaches_the_provider(
    _mock_resolve_many, _mock_sanitize, _mock_upload,
    mock_get_provider, mock_slack, _mock_record,
):
    provider = MagicMock()
    provider.generate.return_value = _result(content=b"video-bytes")
    mock_get_provider.return_value = provider
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "",
        "prompt": "-v -r 720 a corgi surfing", "user": "alice",
        "source": "slash",
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-R"))

    assert provider.generate.call_args.kwargs["resolution"] == "720p"
    assert mock_slack.post_video_response.called


@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_video_provider")
def test_bad_video_resolution_never_reaches_the_provider(mock_get_provider, mock_slack):
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C", "channel_name": "",
        "prompt": "-v -r 1440 a corgi surfing", "user": "alice",
        "source": "slash",
    }
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-R2"))

    mock_get_provider.assert_not_called()
    assert "480, 720, or 1080" in mock_slack.post_ephemeral.call_args.args[1]
