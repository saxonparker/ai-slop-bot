"""Tests for the conversations module: schema, conversion helpers, persistence."""

import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

sys.path.append(".")

import conversations  # noqa: E402  pylint: disable=wrong-import-position


# ── ID format ──────────────────────────────────────────────────────────────

def test_make_id_format():
    assert conversations.make_id("C123", "1700000000.123456") == "C123:1700000000.123456"


# ── Conversion helpers ─────────────────────────────────────────────────────

def _user_msg(prompt="hello"):
    return {
        "role": "user",
        "prompt_text": prompt,
        "display_text": prompt,
        "user": "alice",
        "ts": "2026-01-01T00:00:00Z",
        "potato": False,
        "backend": "anthropic",
    }


def _assistant_msg(content="hi back"):
    return {
        "role": "assistant",
        "content": content,
        "ts": "2026-01-01T00:00:01Z",
        "backend": "anthropic",
        "model": "claude-sonnet-4",
        "input_tokens": 5,
        "output_tokens": 3,
        "cost_estimate": 0.0001,
    }


def test_to_anthropic_uses_role_and_content():
    msgs = [_user_msg("hi"), _assistant_msg("yo"), _user_msg("again")]
    out = conversations.to_anthropic(msgs)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "again"},
    ]


def test_to_openai_chat_matches_anthropic_shape():
    msgs = [_user_msg("hi"), _assistant_msg("yo")]
    assert conversations.to_openai_chat(msgs) == conversations.to_anthropic(msgs)


def test_to_gemini_renames_assistant_to_model_and_wraps_parts():
    msgs = [_user_msg("hi"), _assistant_msg("yo")]
    out = conversations.to_gemini(msgs)
    assert out == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "yo"}]},
    ]


def test_user_message_uses_prompt_text_for_api_payload():
    # Hidden directive un-bracketed in prompt_text but absent from display_text.
    msg = {
        "role": "user",
        "prompt_text": "tell me a joke make it about dogs",
        "display_text": "tell me a joke",
        "user": "alice",
        "ts": "2026-01-01T00:00:00Z",
        "potato": False,
        "backend": "anthropic",
    }
    out = conversations.to_anthropic([msg])
    assert out[0]["content"] == "tell me a joke make it about dogs"


# ── Message builders ───────────────────────────────────────────────────────

def test_build_user_message_includes_all_fields():
    msg = conversations.build_user_message(
        prompt_text="full prompt",
        display_text="display",
        user="alice",
        backend="anthropic",
        potato=True,
    )
    assert msg["role"] == "user"
    assert msg["prompt_text"] == "full prompt"
    assert msg["display_text"] == "display"
    assert msg["user"] == "alice"
    assert msg["potato"] is True
    assert msg["backend"] == "anthropic"
    assert "ts" in msg


def test_build_assistant_message_from_generation_result():
    from usage import GenerationResult
    result = GenerationResult(
        content="hello", backend="anthropic", model="claude-sonnet-4",
        input_tokens=10, output_tokens=20, cost_estimate=0.001,
    )
    msg = conversations.build_assistant_message(result)
    assert msg["role"] == "assistant"
    assert msg["content"] == "hello"
    assert msg["backend"] == "anthropic"
    assert msg["model"] == "claude-sonnet-4"
    assert msg["input_tokens"] == 10
    assert msg["output_tokens"] == 20
    assert msg["cost_estimate"] == 0.001


# ── is_enabled ─────────────────────────────────────────────────────────────

def test_is_enabled_true_when_env_set():
    with patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"}):
        assert conversations.is_enabled() is True


def test_is_enabled_false_when_env_missing():
    with patch.dict("os.environ", {}, clear=True):
        assert conversations.is_enabled() is False


# ── Env-configurable MAX_TURNS ─────────────────────────────────────────────

def test_max_turns_reads_env_override():
    import importlib
    with patch.dict("os.environ", {"CONVERSATION_MAX_TURNS": "42"}):
        importlib.reload(conversations)
        assert conversations.MAX_TURNS == 42
    importlib.reload(conversations)
    assert conversations.MAX_TURNS == 100


# ── DynamoDB persistence (mocked table) ────────────────────────────────────

@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_get_returns_none_when_absent(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.get_item.return_value = {}

    assert conversations.get("C123:1700000000.000001") is None


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_get_returns_conversation_when_present(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.get_item.return_value = {
        "Item": {
            "conversation_id": "C123:1700.0",
            "channel_id": "C123",
            "thread_ts": "1700.0",
            "created_by": "alice",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "total_chars": 100,
            "turn_count": 1,
            "messages": [_user_msg(), _assistant_msg()],
            "schema_version": 1,
        },
    }
    conv = conversations.get("C123:1700.0")
    assert conv is not None
    assert conv.conversation_id == "C123:1700.0"
    assert conv.created_by == "alice"
    assert conv.turn_count == 1
    assert conv.total_chars == 100
    assert len(conv.messages) == 2


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_create_writes_item_with_conditional(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    conv = conversations.create(
        conversation_id="C123:1700.0",
        channel_id="C123",
        thread_ts="1700.0",
        created_by="alice",
        first_user_msg=_user_msg("hi"),
        first_assistant_msg=_assistant_msg("yo"),
    )

    assert conv.turn_count == 1
    mock_table.put_item.assert_called_once()
    call_kwargs = mock_table.put_item.call_args.kwargs
    assert call_kwargs["ConditionExpression"] == "attribute_not_exists(conversation_id)"
    item = call_kwargs["Item"]
    assert item["conversation_id"] == "C123:1700.0"
    assert item["turn_count"] == 1
    assert item["total_chars"] == len("hi") + len("yo")
    assert len(item["messages"]) == 2
    assert item["schema_version"] == conversations.SCHEMA_VERSION
    assert "system_prompt" not in item
    assert "default_backend" not in item


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_create_raises_on_collision(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.put_item.side_effect = ClientError(
        error_response={"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
        operation_name="PutItem",
    )

    with pytest.raises(conversations.ConversationAlreadyExists):
        conversations.create(
            conversation_id="C123:1700.0",
            channel_id="C123",
            thread_ts="1700.0",
            created_by="alice",
            first_user_msg=_user_msg("hi"),
            first_assistant_msg=_assistant_msg("yo"),
        )


@patch.dict("os.environ", {"CONVERSATIONS_TABLE_NAME": "ai-slop-conversations"})
@patch("conversations.boto3.resource")
def test_get_consistent_flag_propagates(mock_resource):
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table
    mock_table.get_item.return_value = {}

    conversations.get("C:1", consistent=True)
    assert mock_table.get_item.call_args.kwargs["ConsistentRead"] is True

    conversations.get("C:1")
    assert mock_table.get_item.call_args.kwargs["ConsistentRead"] is False
