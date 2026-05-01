"""Orchestration tests for ai_slop_bot._handle_continuation_turn and
_handle_first_turn. Covers happy path, phantom-turn (turn_count moved),
lock-contention, hard-cap rejection, top-level create-failure warning,
the -c-on-existing notice, and resolved-backend persistence. The
conversations / providers / slack / usage modules are mocked at import
boundaries.
"""

import dataclasses
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(".")

import ai_slop_bot  # noqa: E402  pylint: disable=wrong-import-position
import conversations  # noqa: E402  pylint: disable=wrong-import-position
from parsing import ParsedCommand  # noqa: E402  pylint: disable=wrong-import-position


def _parsed(prompt="follow up"):
    return ParsedCommand(mode="text", display_text=prompt, prompt_text=prompt)


def _conv(turn_count=1, total_chars=100):
    return conversations.Conversation(
        conversation_id="C:1700.0", channel_id="C", thread_ts="1700.0",
        created_by="alice", created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        total_chars=total_chars, turn_count=turn_count,
        messages=[{"role": "user", "prompt_text": "hi"},
                  {"role": "assistant", "content": "hello"}],
        schema_version=1,
    )


def _result(content="follow-up reply"):
    from usage import GenerationResult
    return GenerationResult(
        content=content, backend="anthropic", model="claude-sonnet-4",
        input_tokens=20, output_tokens=10, cost_estimate=0.001,
    )


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn", return_value=True)
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
def test_continuation_happy_path(mock_acquire, mock_get, mock_append, mock_release,
                                  _mock_prompts, mock_get_provider, mock_slack,
                                  mock_record):
    existing = _conv()
    mock_get.return_value = existing
    provider = MagicMock()
    provider.chat.return_value = _result()
    mock_get_provider.return_value = provider

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed(), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=time.time(),
    )

    mock_acquire.assert_called_once_with("C:1700.0", "req-A")
    mock_get.assert_called_once_with("C:1700.0", consistent=True)
    provider.chat.assert_called_once()
    mock_append.assert_called_once()
    args = mock_append.call_args.args
    assert args[0] == "C:1700.0"
    assert args[3] == len("follow up") + len("follow-up reply")
    assert args[4] == 1
    mock_slack.post_text_response_in_thread.assert_called_once()
    assert mock_slack.post_error.call_count == 0
    mock_record.assert_called_once()
    mock_release.assert_called_once_with("C:1700.0", "req-A")


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn", return_value=False)
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
def test_continuation_phantom_turn_dropped(mock_acquire, mock_get, mock_append,
                                            mock_release, _mock_prompts,
                                            mock_get_provider, mock_slack, mock_record):
    existing = _conv()
    mock_get.return_value = existing
    provider = MagicMock()
    provider.chat.return_value = _result()
    mock_get_provider.return_value = provider

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed(), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=time.time(),
    )

    mock_append.assert_called_once()
    mock_slack.post_error.assert_called_once()
    err_msg = mock_slack.post_error.call_args.args[1]
    assert "modified by another in-flight turn" in err_msg
    mock_slack.post_text_response_in_thread.assert_not_called()
    mock_record.assert_not_called()
    mock_release.assert_called_once_with("C:1700.0", "req-A")


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn")
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=False)
@patch("ai_slop_bot.time.sleep")
def test_continuation_lock_contention_posts_error(mock_sleep, mock_acquire, mock_get,
                                                   mock_append, mock_release,
                                                   mock_get_provider, mock_slack,
                                                   mock_record):
    existing = _conv()

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed(), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=time.time(),
    )

    assert mock_acquire.call_count == 2
    mock_sleep.assert_called_once_with(conversations.LOCK_RETRY_SLEEP_SECONDS)
    mock_get.assert_not_called()
    mock_get_provider.assert_not_called()
    mock_append.assert_not_called()
    mock_slack.post_ephemeral.assert_called_once()
    msg = mock_slack.post_ephemeral.call_args.args[1]
    assert "in flight" in msg
    mock_release.assert_not_called()
    mock_record.assert_not_called()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn")
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
def test_continuation_rejects_when_reserve_would_overflow(mock_acquire, mock_get,
                                                          mock_append, mock_release,
                                                          mock_get_provider, mock_slack,
                                                          mock_record):
    # total_chars + new_chars + ASSISTANT_RESERVE_CHARS exceeds the cap.
    headroom = conversations.CONVERSATION_MAX_CHARS - conversations.ASSISTANT_RESERVE_CHARS
    existing = _conv(turn_count=2, total_chars=headroom)
    mock_get.return_value = existing

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed("x"), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=time.time(),
    )

    mock_get_provider.assert_not_called()
    mock_append.assert_not_called()
    mock_slack.post_error.assert_called_once()
    msg = mock_slack.post_error.call_args.args[1]
    assert "reached its limit" in msg
    mock_release.assert_called_once()
    mock_record.assert_not_called()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn")
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
def test_continuation_rejects_when_max_turns_reached(mock_acquire, mock_get, mock_append,
                                                     mock_release, mock_get_provider,
                                                     mock_slack, mock_record):
    existing = _conv(turn_count=conversations.MAX_TURNS, total_chars=10)
    mock_get.return_value = existing

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed(), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=time.time(),
    )

    mock_get_provider.assert_not_called()
    mock_append.assert_not_called()
    mock_slack.post_error.assert_called_once()
    mock_release.assert_called_once()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn", return_value=True)
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
def test_continuation_aborts_before_model_call_when_near_lambda_timeout(
    mock_acquire, mock_get, mock_append, mock_release, _mock_prompts,
    mock_get_provider, mock_slack, mock_record,
):
    existing = _conv()
    mock_get.return_value = existing
    # Pretend the lambda started long ago, well past the abort threshold.
    fake_start = time.time() - (conversations.IN_HANDLER_ABORT_SECONDS + 30)

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed(), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=fake_start,
    )

    mock_get_provider.assert_not_called()
    mock_append.assert_not_called()
    mock_slack.post_error.assert_called_once()
    msg = mock_slack.post_error.call_args.args[1]
    assert "Lambda timeout" in msg
    mock_release.assert_called_once()
    mock_record.assert_not_called()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.create")
def test_first_turn_top_level_warns_on_create_failure(mock_create, _mock_prompts,
                                                      mock_get_provider, mock_slack,
                                                      mock_record):
    provider = MagicMock()
    provider.chat.return_value = _result()
    mock_get_provider.return_value = provider
    mock_slack.post_text_chat_postmessage.return_value = "1700.0"
    mock_create.side_effect = RuntimeError("dynamodb down")

    with pytest.raises(RuntimeError, match="dynamodb down"):
        ai_slop_bot._handle_first_turn(
            parsed=_parsed("hello"), user="alice", channel_id="C",
            response_url="https://hooks/x", thread_ts=None,
            lambda_start=time.time(),
        )

    mock_slack.post_text_chat_postmessage.assert_called_once()
    mock_slack.post_thread_notice.assert_called_once()
    notice_kwargs = mock_slack.post_thread_notice.call_args.kwargs
    assert notice_kwargs["thread_ts"] == "1700.0"
    assert "Could not start conversation tracking" in notice_kwargs["text"]
    mock_record.assert_called_once()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.create")
def test_first_turn_in_thread_persists_resolved_backend(mock_create, _mock_prompts,
                                                        mock_get_provider, mock_slack,
                                                        mock_record):
    provider = MagicMock()
    provider.chat.return_value = _result()  # backend="anthropic"
    mock_get_provider.return_value = provider

    parsed_no_override = _parsed("hello")
    assert parsed_no_override.backend_override is None

    ai_slop_bot._handle_first_turn(
        parsed=parsed_no_override, user="alice", channel_id="C",
        response_url="https://hooks/x", thread_ts="1700.0",
        lambda_start=time.time(),
    )

    create_kwargs = mock_create.call_args.kwargs
    assert create_kwargs["first_user_msg"]["backend"] == "anthropic"
    mock_record.assert_called_once()


@patch("ai_slop_bot.usage.record_usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn", return_value=True)
@patch("ai_slop_bot.conversations.get")
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
def test_continuation_persists_resolved_backend(mock_acquire, mock_get, mock_append,
                                                 mock_release, _mock_prompts,
                                                 mock_get_provider, mock_slack,
                                                 mock_record):
    existing = _conv()
    mock_get.return_value = existing
    provider = MagicMock()
    provider.chat.return_value = _result()  # backend="anthropic"
    mock_get_provider.return_value = provider

    ai_slop_bot._handle_continuation_turn(
        parsed=_parsed(), user="bob", response_url="https://hooks/x",
        thread_ts="1700.0", existing_conv=existing,
        request_id="req-A", lambda_start=time.time(),
    )

    user_msg = mock_append.call_args.args[1]
    assert user_msg["backend"] == "anthropic"


@patch("ai_slop_bot.usage")
@patch("ai_slop_bot.slack")
@patch("ai_slop_bot.providers.get_text_provider")
@patch("ai_slop_bot.prompts.get_system_message", return_value="be helpful")
@patch("ai_slop_bot.conversations.release_lock")
@patch("ai_slop_bot.conversations.append_turn", return_value=True)
@patch("ai_slop_bot.conversations.acquire_lock", return_value=True)
@patch("ai_slop_bot.conversations.is_enabled", return_value=True)
def test_dispatch_posts_notice_when_minus_c_in_existing_conversation(
    _mock_enabled, _mock_acquire, _mock_append, _mock_release,
    _mock_prompts, mock_get_provider, mock_slack, _mock_usage,
):
    existing = _conv()
    provider = MagicMock()
    provider.chat.return_value = _result()
    mock_get_provider.return_value = provider

    parsed_with_c = dataclasses.replace(_parsed("follow up"), conversation=True)
    sns_message = {
        "response_url": "https://hooks/x", "channel_id": "C",
        "channel_name": "general", "thread_ts": "1700.0",
        "prompt": "-c follow up", "user": "bob",
    }
    import json
    event = {"Records": [{"Sns": {"Message": json.dumps(sns_message)}}]}

    with patch("ai_slop_bot.parsing.parse_command", return_value=parsed_with_c), \
         patch("ai_slop_bot.conversations.get", return_value=existing):
        ai_slop_bot.ai_slop_bot(event, MagicMock(aws_request_id="req-A"))

    notice_calls = [c for c in mock_slack.post_ephemeral.call_args_list
                    if "already a conversation" in (c.args[1] if len(c.args) > 1
                                                    else c.kwargs.get("text", ""))]
    assert len(notice_calls) == 1
