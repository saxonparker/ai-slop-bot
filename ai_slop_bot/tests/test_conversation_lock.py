"""Tests for conversation lock acquire/release semantics."""

import sys
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

sys.path.append(".")

import conversations  # noqa: E402  pylint: disable=wrong-import-position


def _conditional_check_failed():
    return ClientError(
        error_response={"Error": {"Code": "ConditionalCheckFailedException", "Message": "boom"}},
        operation_name="UpdateItem",
    )


# ── acquire_lock ───────────────────────────────────────────────────────────

@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_acquire_lock_succeeds_when_unlocked(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    assert conversations.acquire_lock("C:1", "req-1", now=1000) is True

    mock_table.update_item.assert_called_once()
    call_kwargs = mock_table.update_item.call_args.kwargs
    assert "lock_holder = :req" in call_kwargs["UpdateExpression"]
    assert call_kwargs["ExpressionAttributeValues"][":req"] == "req-1"
    assert call_kwargs["ExpressionAttributeValues"][":exp"] == 1000 + conversations.LOCK_TTL_SECONDS
    assert call_kwargs["ExpressionAttributeValues"][":now"] == 1000


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_acquire_lock_returns_false_on_conditional_failure(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.update_item.side_effect = _conditional_check_failed()

    assert conversations.acquire_lock("C:1", "req-1") is False


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_acquire_lock_propagates_other_errors(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.update_item.side_effect = ClientError(
        error_response={"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        operation_name="UpdateItem",
    )

    import pytest
    with pytest.raises(ClientError):
        conversations.acquire_lock("C:1", "req-1")


# ── release_lock ───────────────────────────────────────────────────────────

@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_release_lock_swallows_conditional_failure(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.update_item.side_effect = _conditional_check_failed()

    # Should not raise.
    conversations.release_lock("C:1", "req-1")


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_release_lock_uses_owner_condition(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    conversations.release_lock("C:1", "req-1")

    call_kwargs = mock_table.update_item.call_args.kwargs
    assert call_kwargs["ConditionExpression"] == "lock_holder = :req"
    assert call_kwargs["ExpressionAttributeValues"][":req"] == "req-1"


# ── append_turn ────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_append_turn_succeeds(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    user_msg = {"role": "user", "prompt_text": "x"}
    assistant_msg = {"role": "assistant", "content": "y"}

    appended = conversations.append_turn("C:1", user_msg, assistant_msg, 2, 1)
    assert appended is True

    call_kwargs = mock_table.update_item.call_args.kwargs
    assert "list_append" in call_kwargs["UpdateExpression"]
    assert call_kwargs["ConditionExpression"] == "turn_count = :expected"
    assert call_kwargs["ExpressionAttributeValues"][":expected"] == 1
    assert call_kwargs["ExpressionAttributeValues"][":added"] == 2


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_append_turn_returns_false_on_phantom(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.update_item.side_effect = _conditional_check_failed()

    appended = conversations.append_turn(
        "C:1", {"role": "user", "prompt_text": "x"},
        {"role": "assistant", "content": "y"}, 2, 1,
    )
    assert appended is False
