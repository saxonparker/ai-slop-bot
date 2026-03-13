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
    "gemini_text": {"input": 0.15, "output": 0.60},
}

COST_PER_IMAGE = {
    "gemini": 0.04,
    "openai": 0.08,
}


def estimate_text_cost(backend: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost for a text generation request."""
    key = f"{backend}_text" if backend in ("openai", "gemini") else backend
    rates = COST_PER_MILLION_TOKENS.get(key, {"input": 0.0, "output": 0.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _get_table():
    """Get DynamoDB table resource."""
    table_name = os.environ.get("USAGE_TABLE_NAME", "ai-slop-usage")
    return boto3.resource("dynamodb").Table(table_name)


def record_usage(user: str, result: GenerationResult):
    """Write a usage record to DynamoDB. Failures are logged but never propagated."""
    try:
        mode = "image" if isinstance(result.content, bytes) else "text"
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


def get_usage_summary(user: str) -> str:
    """Query all usage records for a user and return a formatted Slack mrkdwn summary."""
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

    lines = ["*Your usage stats:*"]
    lines.append("")
    lines.append(_format_window("Last 7 days", last_7))
    lines.append("")
    lines.append(_format_window(f"This month ({now.strftime('%B')})", this_month))
    lines.append("")
    lines.append(_format_window("All time", all_time))
    return "\n".join(lines)


def _format_window(label: str, records: list) -> str:
    """Format a single time window section."""
    count = len(records)
    total_cost = sum(float(r.get("cost_estimate", 0)) for r in records)
    lines = [f"*{label}:*"]
    lines.append(f"  Requests: {count}  |  Est. cost: ${total_cost:.2f}")

    # Backend breakdown if multiple backends used
    by_backend: dict[str, list] = {}
    for r in records:
        by_backend.setdefault(r["backend"], []).append(r)
    if len(by_backend) > 1:
        parts = []
        for backend, recs in sorted(by_backend.items()):
            cost = sum(float(r.get("cost_estimate", 0)) for r in recs)
            parts.append(f"{backend}: {len(recs)} (${cost:.2f})")
        lines.append("  " + " \u00b7 ".join(parts))

    return "\n".join(lines)
