"""Conversation persistence and lock semantics for multi-turn /slop-bot text chat.

A conversation is keyed on the Slack thread it lives in (channel_id + thread_ts).
The full transcript is stored in a single DynamoDB item and replayed to the chosen
text backend on every turn.
"""

import os
import time
import typing
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


CONVERSATION_MAX_CHARS = int(os.environ.get("CONVERSATION_MAX_CHARS", "200000"))
# Reserved headroom for the assistant response on each turn. The pre-call cap
# check counts this against total_chars so a turn near the limit can't push
# the persisted transcript past CONVERSATION_MAX_CHARS.
ASSISTANT_RESERVE_CHARS = int(os.environ.get("ASSISTANT_RESERVE_CHARS", "16000"))
SOFT_WARN_FRACTION = 0.8
LOCK_TTL_SECONDS = 360
LOCK_RETRY_SLEEP_SECONDS = 2
MAX_TURNS = int(os.environ.get("CONVERSATION_MAX_TURNS", "100"))
IN_HANDLER_ABORT_SECONDS = 270
SCHEMA_VERSION = 1


class ConversationAlreadyExists(Exception):
    """Raised by create() when a row already exists at the target conversation_id."""


class Conversation(typing.NamedTuple):
    """In-memory view of a conversation row."""
    conversation_id: str
    channel_id: str
    thread_ts: str
    created_by: str
    created_at: str
    updated_at: str
    total_chars: int
    turn_count: int
    messages: list[dict]
    schema_version: int


def is_enabled() -> bool:
    """True iff the conversations feature is wired up in this environment."""
    return bool(os.environ.get("CONVERSATIONS_TABLE_NAME"))


def make_id(channel_id: str, thread_ts: str) -> str:
    """Compose the conversation_id from channel_id and thread_ts."""
    return f"{channel_id}:{thread_ts}"


def get(conversation_id: str, *, consistent: bool = False) -> Conversation | None:
    """Fetch a conversation by id, or None if absent.

    Pass consistent=True after acquiring the per-conversation lock so the read
    reflects the latest write — eventually-consistent reads can otherwise
    return a pre-write snapshot and trip the turn_count guard in append_turn.
    """
    response = _table().get_item(
        Key={"conversation_id": conversation_id},
        ConsistentRead=consistent,
    )
    item = response.get("Item")
    if item is None:
        return None
    return _from_item(item)


def create(
    *,
    conversation_id: str,
    channel_id: str,
    thread_ts: str,
    created_by: str,
    first_user_msg: dict,
    first_assistant_msg: dict,
) -> Conversation:
    """Create a new conversation row with turn 1's user + assistant messages.

    Raises ConversationAlreadyExists on collision so the caller can surface
    the race to the user instead of silently dropping the losing turn.
    """
    now = _now_iso()
    added_chars = (
        len(first_user_msg.get("prompt_text", ""))
        + len(first_assistant_msg.get("content", ""))
    )
    item = {
        "conversation_id": conversation_id,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "total_chars": added_chars,
        "turn_count": 1,
        "messages": [first_user_msg, _decimalize_cost(first_assistant_msg)],
        "schema_version": SCHEMA_VERSION,
    }
    try:
        _table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(conversation_id)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            print(f"CONVERSATION CREATE COLLISION: {conversation_id}")
            raise ConversationAlreadyExists(conversation_id) from exc
        raise
    return _from_item(item)


def acquire_lock(conversation_id: str, request_id: str, now: int | None = None) -> bool:
    """Atomically acquire the per-conversation lock. Returns True on success.

    Succeeds when the row exists AND (no holder | stale TTL | same holder).
    Returns False on ConditionalCheckFailedException (someone else holds a fresh lock).
    """
    if now is None:
        now = _now_epoch()
    expires = now + LOCK_TTL_SECONDS
    try:
        _table().update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression="SET lock_holder = :req, lock_expires_at = :exp",
            ConditionExpression=(
                "attribute_exists(conversation_id) AND ("
                "attribute_not_exists(lock_holder) "
                "OR lock_expires_at < :now "
                "OR lock_holder = :req)"
            ),
            ExpressionAttributeValues={
                ":req": request_id,
                ":exp": expires,
                ":now": now,
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release_lock(conversation_id: str, request_id: str) -> None:
    """Release the lock iff we still own it. Conditional failure is swallowed."""
    try:
        _table().update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression="REMOVE lock_holder, lock_expires_at",
            ConditionExpression="lock_holder = :req",
            ExpressionAttributeValues={":req": request_id},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            print(f"RELEASE LOCK ERROR: {exc}")


def append_turn(
    conversation_id: str,
    user_msg: dict,
    assistant_msg: dict,
    added_chars: int,
    expected_turn_count: int,
) -> bool:
    """Atomically append a user+assistant pair. Returns False if turn_count moved.

    The turn_count optimistic-concurrency check guards against a stale-lock
    preemption: if another turn slipped in between our acquire and our append,
    we drop our writes rather than corrupt history.
    """
    try:
        _table().update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression=(
                "SET messages = list_append(messages, :new_msgs), "
                "total_chars = total_chars + :added, "
                "turn_count = turn_count + :one, "
                "updated_at = :ts"
            ),
            ConditionExpression="turn_count = :expected",
            ExpressionAttributeValues={
                ":new_msgs": [user_msg, _decimalize_cost(assistant_msg)],
                ":added": added_chars,
                ":one": 1,
                ":ts": _now_iso(),
                ":expected": expected_turn_count,
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def to_anthropic(messages: list[dict]) -> list[dict]:
    """Canonical messages → Anthropic shape (identity in role/content)."""
    return [{"role": m["role"], "content": _api_text(m)} for m in messages]


def to_openai_chat(messages: list[dict]) -> list[dict]:
    """Canonical messages → OpenAI/Grok shape (identity in role/content)."""
    return [{"role": m["role"], "content": _api_text(m)} for m in messages]


def to_gemini(messages: list[dict]) -> list[dict]:
    """Canonical messages → Gemini contents shape ('model' role, parts wrapper)."""
    return [
        {
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": _api_text(m)}],
        }
        for m in messages
    ]


def build_user_message(
    *,
    prompt_text: str,
    display_text: str,
    user: str,
    backend: str,
    potato: bool,
) -> dict:
    """Construct the canonical user-role message dict."""
    return {
        "role": "user",
        "prompt_text": prompt_text,
        "display_text": display_text,
        "user": user,
        "ts": _now_iso(),
        "potato": potato,
        "backend": backend,
    }


def synth_user_message(prompt_text: str) -> dict:
    """Minimal user-role dict for single-shot generate() calls.

    Backends use to_*() shape converters that read `prompt_text` from user
    messages — this helper is the single place that knows that contract, so
    a future rename propagates from one spot.
    """
    return {"role": "user", "prompt_text": prompt_text}


def build_assistant_message(result) -> dict:
    """Construct the canonical assistant-role message dict from a GenerationResult."""
    return {
        "role": "assistant",
        "content": result.content,
        "ts": _now_iso(),
        "backend": result.backend,
        "model": result.model,
        "input_tokens": int(result.input_tokens),
        "output_tokens": int(result.output_tokens),
        "cost_estimate": float(result.cost_estimate),
    }


def _table():
    name = os.environ.get("CONVERSATIONS_TABLE_NAME")
    if not name:
        raise RuntimeError(
            "CONVERSATIONS_TABLE_NAME is not set; conversations are disabled."
        )
    return boto3.resource("dynamodb").Table(name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _from_item(item: dict) -> Conversation:
    return Conversation(
        conversation_id=item["conversation_id"],
        channel_id=item.get("channel_id", ""),
        thread_ts=item.get("thread_ts", ""),
        created_by=item.get("created_by", ""),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
        total_chars=int(item.get("total_chars", 0)),
        turn_count=int(item.get("turn_count", 0)),
        messages=list(item.get("messages", [])),
        schema_version=int(item.get("schema_version", 1)),
    )


def _api_text(msg: dict) -> str:
    if msg["role"] == "user":
        return msg.get("prompt_text", "")
    return msg.get("content", "")


def _decimalize_cost(assistant_msg: dict) -> dict:
    """Return a copy of an assistant message with cost_estimate as Decimal.

    cost_estimate is the only float on our write path; DynamoDB rejects floats.
    """
    cost = assistant_msg.get("cost_estimate")
    if not isinstance(cost, float):
        return assistant_msg
    return {**assistant_msg, "cost_estimate": Decimal(str(round(cost, 6)))}
