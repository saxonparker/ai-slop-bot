"""Tests for the dispatch Lambda: thread_ts propagation and HELP_TEXT."""

import json
import sys
from unittest.mock import MagicMock, patch
import urllib.parse

sys.path.append("../ai_slop_dispatch")

import ai_slop_dispatch  # noqa: E402  pylint: disable=wrong-import-position


def _slack_event(text: str, user="alice", channel_id="C123",
                 channel_name="general", thread_ts: str | None = None):
    body_params = {
        "text": text,
        "user_name": user,
        "response_url": "https://hooks.slack.example/dispatch",
        "channel_id": channel_id,
        "channel_name": channel_name,
    }
    if thread_ts is not None:
        body_params["thread_ts"] = thread_ts
    return {"body": urllib.parse.urlencode(body_params)}


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_dispatch_propagates_thread_ts_when_present(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(_slack_event("hello", thread_ts="1700000000.000001"), None)

    publish_kwargs = mock_sns.publish.call_args.kwargs
    inner = json.loads(publish_kwargs["Message"])
    payload = json.loads(inner["default"])
    assert payload["thread_ts"] == "1700000000.000001"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_dispatch_thread_ts_empty_when_top_level(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(_slack_event("hello"), None)

    publish_kwargs = mock_sns.publish.call_args.kwargs
    inner = json.loads(publish_kwargs["Message"])
    payload = json.loads(inner["default"])
    assert payload["thread_ts"] == ""


def test_help_text_mentions_conversation_flag():
    assert "-c" in ai_slop_dispatch.HELP_TEXT
    assert "conversation" in ai_slop_dispatch.HELP_TEXT.lower()


def _events_request(payload: dict):
    return {
        "path": "/slack/events",
        "body": json.dumps(payload),
    }


def test_url_verification_echoes_challenge():
    response = ai_slop_dispatch.dispatch(
        _events_request({"type": "url_verification", "challenge": "abc123"}),
        None,
    )
    assert response["statusCode"] == "200"
    body = json.loads(response["body"])
    assert body["challenge"] == "abc123"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_in_thread_publishes_event_mention_sns(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "user": "U999",
            "text": "<@UBOT> follow up question",
            "channel": "C123",
            "thread_ts": "1700000000.000001",
            "ts": "1700000000.000005",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    mock_sns.publish.assert_called_once()
    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["source"] == "event_mention"
    assert sns_msg["thread_ts"] == "1700000000.000001"
    assert sns_msg["channel_id"] == "C123"
    assert sns_msg["prompt"] == "follow up question"
    assert sns_msg["event_user_id"] == "U999"
    assert sns_msg["response_url"] == ""


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_with_pipe_form_user_id_strips_correctly(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999",
            "text": "<@UBOT|slop-bot>   tell me a joke",
            "channel": "C123", "thread_ts": "1700.0", "ts": "1700.5",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["prompt"] == "tell me a joke"


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_without_thread_ts_is_ignored(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999",
            "text": "<@UBOT> hello", "channel": "C123", "ts": "1700.0",
        },
    }
    response = ai_slop_dispatch.dispatch(_events_request(payload), None)

    assert response["statusCode"] == "200"
    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_from_bot_is_ignored(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999", "bot_id": "BSELF",
            "text": "<@UBOT> hello", "channel": "C123",
            "thread_ts": "1700.0", "ts": "1700.0",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_app_mention_with_empty_prompt_is_ignored(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns

    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention", "user": "U999",
            "text": "<@UBOT>   ", "channel": "C123",
            "thread_ts": "1700.0", "ts": "1700.0",
        },
    }
    ai_slop_dispatch.dispatch(_events_request(payload), None)

    mock_sns.publish.assert_not_called()


@patch.dict("os.environ", {"AI_SLOP_SNS_TOPIC": "arn:aws:sns:::topic"})
@patch("ai_slop_dispatch.boto3.client")
def test_slash_command_publishes_with_source_slash(mock_boto):
    mock_sns = MagicMock()
    mock_boto.return_value = mock_sns
    mock_sns.publish.return_value = {"MessageId": "abc"}

    ai_slop_dispatch.dispatch(_slack_event("hello"), None)

    inner = json.loads(mock_sns.publish.call_args.kwargs["Message"])
    sns_msg = json.loads(inner["default"])
    assert sns_msg["source"] == "slash"
