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
