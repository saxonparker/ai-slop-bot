"""Usage tracking: record per-request costs in DynamoDB, query and format summaries."""

import os
import typing
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


class GenerationResult(typing.NamedTuple):
    """Result from a text or image generation backend."""
    content: str | bytes
    backend: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_estimate: float


COST_PER_MILLION_TOKENS = {
    "anthropic": {"input": 3.00, "output": 15.00},
    "openai_text": {"input": 5.00, "output": 15.00},
    "gemini_text": {"input": 0.50, "output": 3.00},
    "grok_text": {"input": 0.20, "output": 0.50},
}

COST_PER_VIDEO = {
    "grok": 0.05,  # per second of video
}

COST_PER_IMAGE = {
    "gemini": 0.04,
    "openai": 0.08,
    "grok": 0.02,
}


def estimate_text_cost(backend: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost for a text generation request."""
    key = f"{backend}_text" if backend in ("openai", "gemini", "grok") else backend
    rates = COST_PER_MILLION_TOKENS.get(key, {"input": 0.0, "output": 0.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _get_table():
    """Get DynamoDB table resource."""
    table_name = os.environ.get("USAGE_TABLE_NAME", "ai-slop-usage")
    return boto3.resource("dynamodb").Table(table_name)


VIDEO_MODELS = {"grok-imagine-video"}


def record_usage(user: str, result: GenerationResult):
    """Write a usage record to DynamoDB. Failures are logged but never propagated."""
    try:
        if result.model in VIDEO_MODELS:
            mode = "video"
        elif isinstance(result.content, bytes):
            mode = "image"
        else:
            mode = "text"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _get_table().put_item(Item={
            "user": user,
            "timestamp": now,
            "mode": mode,
            "backend": result.backend,
            "model": result.model,
            "cost_estimate": Decimal(str(round(result.cost_estimate, 6))),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        })
    except Exception as exc:  # pylint: disable=broad-except
        print(f"USAGE RECORD ERROR: {exc}")


def get_usage_summary(user: str) -> list[dict] | str:
    """Query all usage records for a user and return Slack blocks (or a plain string on error)."""
    try:
        table = _get_table()
        response = table.query(
            KeyConditionExpression="#u = :user",
            ExpressionAttributeNames={"#u": "user"},
            ExpressionAttributeValues={":user": user},
        )
        records = response.get("Items", [])
    except Exception as exc:  # pylint: disable=broad-except
        print(f"USAGE QUERY ERROR: {exc}")
        return "Failed to retrieve usage data."

    if not records:
        return "No usage recorded yet."

    now = datetime.now(timezone.utc)
    seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    month_prefix = now.strftime("%Y-%m")

    all_time = records
    last_7 = [r for r in records if r["timestamp"] >= seven_days_ago]
    this_month = [r for r in records if r["timestamp"].startswith(month_prefix)]

    month_name = now.strftime("%b")
    return [
        _format_block("Last 7 days", last_7),
        _format_block(month_name, this_month),
        _format_block("All time", all_time),
    ]


def get_total_cost(user: str) -> float:
    """Return the sum of all cost_estimate values for a user."""
    try:
        table = _get_table()
        response = table.query(
            KeyConditionExpression="#u = :user",
            ExpressionAttributeNames={"#u": "user"},
            ExpressionAttributeValues={":user": user},
            ProjectionExpression="cost_estimate",
        )
        return sum(float(r.get("cost_estimate", 0)) for r in response.get("Items", []))
    except Exception as exc:  # pylint: disable=broad-except
        print(f"USAGE QUERY ERROR: {exc}")
        return 0.0


def _format_block(label: str, records: list) -> dict:
    """Format a single time window as a Slack section block."""
    count = len(records)
    total_cost = sum(float(r.get("cost_estimate", 0)) for r in records)
    by_mode = {}
    for r in records:
        mode = r.get("mode", "text")
        by_mode.setdefault(mode, {"count": 0, "cost": 0.0})
        by_mode[mode]["count"] += 1
        by_mode[mode]["cost"] += float(r.get("cost_estimate", 0))
    MODE_LABELS = {"text": "Text", "image": "Image", "video": "Video"}
    breakdown = "\n".join(
        f"  _{MODE_LABELS.get(m, m)}:_ {s['count']} req — ${s['cost']:.2f}"
        for m, s in sorted(by_mode.items())
    )
    text = f"*{label}:* {count} requests — ${total_cost:.2f}"
    if breakdown:
        text += f"\n{breakdown}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}
