"""Continue-button conversations: one DynamoDB item per transcript.

Every plain text reply stores its first exchange here and carries a Continue
button whose value is the conversation id. A continuation replays the stored
messages to the model and appends the new pair with a conditional update on
turn_count, so two simultaneous turns never interleave and no lock is needed.
"""

import os
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

MAX_TURNS = 20          # user/assistant pairs per conversation
MAX_CHARS = 60_000      # total stored content characters
TTL_DAYS = 30


def is_enabled():
    """Keep existing deployments functional until the table is configured."""
    return bool(os.environ.get("CONVERSATIONS_TABLE_NAME"))


def _table():
    return boto3.resource("dynamodb").Table(os.environ["CONVERSATIONS_TABLE_NAME"])


def new_id() -> str:
    """Opaque id carried in the Continue button's value."""
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at() -> int:
    return int(time.time()) + TTL_DAYS * 86400


def user_message(content: str, user: str) -> dict:
    """Transcript entry for a user turn; `user` records who paid for it."""
    return {"role": "user", "content": content, "user": user}


def assistant_message(content: str) -> dict:
    return {"role": "assistant", "content": content}


def create(*, conversation_id: str, channel_id: str, created_by: str, flags: dict,
           user_msg: dict, assistant_msg: dict):
    """Store the first exchange. `flags` are the first turn's backend/potato/emoji choices."""
    now = _now()
    _table().put_item(
        Item={
            "conversation_id": conversation_id,
            "channel_id": channel_id,
            "created_by": created_by,
            "flags": {
                "backend": flags.get("backend") or "",
                "potato": bool(flags.get("potato")),
                "emoji": bool(flags.get("emoji")),
            },
            "turn_count": 1,
            "messages": [user_msg, assistant_msg],
            "created_at": now,
            "updated_at": now,
            "expires_at": _expires_at(),
        },
        ConditionExpression="attribute_not_exists(conversation_id)",
    )


def get(conversation_id: str) -> dict | None:
    """Fetch a transcript with a consistent read; None when missing or expired."""
    item = _table().get_item(
        Key={"conversation_id": conversation_id}, ConsistentRead=True,
    ).get("Item")
    if item is None:
        return None
    item["turn_count"] = int(item.get("turn_count", 0))
    item["messages"] = list(item.get("messages") or [])
    item["flags"] = dict(item.get("flags") or {})
    return item


def append_turn(conversation_id: str, user_msg: dict, assistant_msg: dict,
                expected_turn_count: int) -> bool:
    """Append one exchange; False when another turn landed first (caller should retry)."""
    try:
        _table().update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression=(
                "SET messages = list_append(messages, :pair), turn_count = turn_count + :one, "
                "updated_at = :now, expires_at = :exp"
            ),
            ConditionExpression="turn_count = :expected",
            ExpressionAttributeValues={
                ":pair": [user_msg, assistant_msg],
                ":one": 1,
                ":now": _now(),
                ":exp": _expires_at(),
                ":expected": expected_turn_count,
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    return True


def transcript_chars(messages: list[dict]) -> int:
    return sum(len(message.get("content") or "") for message in messages)


def is_full(turn_count: int, messages: list[dict]) -> bool:
    """True once the turn or character cap is reached; such replies get no Continue button."""
    return turn_count >= MAX_TURNS or transcript_chars(messages) >= MAX_CHARS
