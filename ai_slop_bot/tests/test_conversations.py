"""Transcripts persist per conversation id with a conditional append and no locks."""

from decimal import Decimal
from pathlib import Path
import sys
import time
from unittest.mock import patch

from botocore.exceptions import ClientError
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conversations  # noqa: E402  pylint: disable=wrong-import-position


USER = {"role": "user", "content": "more cats", "user": "bob"}
ASSISTANT = {"role": "assistant", "content": "even more cats"}


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_TABLE_NAME", "test-conversations")
    with patch("conversations.boto3.resource") as resource:
        yield resource.return_value.Table.return_value


def _client_error(code):
    return ClientError({"Error": {"Code": code}}, "UpdateItem")


def test_disabled_until_table_is_configured(monkeypatch):
    monkeypatch.delenv("CONVERSATIONS_TABLE_NAME", raising=False)
    assert conversations.is_enabled() is False
    monkeypatch.setenv("CONVERSATIONS_TABLE_NAME", "t")
    assert conversations.is_enabled() is True


def test_create_stores_first_exchange_flags_and_ttl(table):
    conversations.create(
        conversation_id="abc", channel_id="C1", created_by="alice",
        flags={"backend": "grok", "potato": True, "emoji": None},
        user_msg=conversations.user_message("hi", "alice"),
        assistant_msg=conversations.assistant_message("hello"),
    )

    kwargs = table.put_item.call_args.kwargs
    assert kwargs["ConditionExpression"] == "attribute_not_exists(conversation_id)"
    item = kwargs["Item"]
    assert item["conversation_id"] == "abc"
    assert item["channel_id"] == "C1"
    assert item["created_by"] == "alice"
    assert item["turn_count"] == 1
    assert item["flags"] == {"backend": "grok", "potato": True, "emoji": False}
    assert item["messages"] == [
        {"role": "user", "content": "hi", "user": "alice"},
        {"role": "assistant", "content": "hello"},
    ]
    assert item["expires_at"] > time.time() + (conversations.TTL_DAYS - 1) * 86400


def test_get_returns_none_when_missing(table):
    table.get_item.return_value = {}
    assert conversations.get("nope") is None
    table.get_item.assert_called_once_with(Key={"conversation_id": "nope"}, ConsistentRead=True)


def test_get_normalizes_dynamodb_types(table):
    table.get_item.return_value = {"Item": {
        "conversation_id": "abc", "turn_count": Decimal("3"), "messages": [USER], "flags": {"potato": True},
    }}
    item = conversations.get("abc")
    assert item["turn_count"] == 3 and isinstance(item["turn_count"], int)
    assert item["messages"] == [USER]
    assert item["flags"] == {"potato": True}


def test_append_turn_is_conditional_on_turn_count(table):
    assert conversations.append_turn("abc", USER, ASSISTANT, 2) is True

    kwargs = table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"conversation_id": "abc"}
    assert kwargs["ConditionExpression"] == "turn_count = :expected"
    assert "list_append(messages, :pair)" in kwargs["UpdateExpression"]
    assert "turn_count = turn_count + :one" in kwargs["UpdateExpression"]
    values = kwargs["ExpressionAttributeValues"]
    assert values[":pair"] == [USER, ASSISTANT]
    assert values[":expected"] == 2
    assert values[":one"] == 1


def test_append_turn_reports_a_lost_race(table):
    table.update_item.side_effect = _client_error("ConditionalCheckFailedException")
    assert conversations.append_turn("abc", USER, ASSISTANT, 2) is False


def test_append_turn_raises_other_errors(table):
    table.update_item.side_effect = _client_error("ProvisionedThroughputExceededException")
    with pytest.raises(ClientError):
        conversations.append_turn("abc", USER, ASSISTANT, 2)


def test_is_full_at_turn_or_character_limit():
    assert conversations.is_full(1, [USER, ASSISTANT]) is False
    assert conversations.is_full(conversations.MAX_TURNS, []) is True
    big = {"role": "assistant", "content": "x" * conversations.MAX_CHARS}
    assert conversations.is_full(1, [big]) is True
