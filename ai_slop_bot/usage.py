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
    cost_actual: float | None = None
    cost_in_usd_ticks: int | None = None


COST_PER_MILLION_TOKENS = {
    "anthropic": {"input": 3.00, "output": 15.00},
    "openai_text": {"input": 5.00, "output": 15.00},
    "gemini_text": {"input": 0.50, "output": 3.00},
    "grok_text": {"input": 0.20, "output": 0.50},
}

COST_PER_VIDEO = {
    "grok": 0.05,  # per second of video
    "gemini": 0.10,  # Veo 3.1 Fast @ 720p, per second (incl. audio)
}

COST_PER_IMAGE = {
    "gemini": 0.04,
    "openai": 0.08,
    "grok": 0.05,  # grok-imagine-image-quality
}

TICKS_PER_USD = 10_000_000_000
ERROR_MESSAGE_MAX = 500


class ProviderGenerationError(RuntimeError):
    """Provider failure that may still carry billable cost metadata."""

    def __init__(
        self,
        message: str,
        *,
        backend: str,
        model: str = "",
        error_type: str = "provider_error",
        cost_estimate: float = 0.0,
        cost_actual: float | None = None,
        cost_in_usd_ticks: int | None = None,
    ):
        super().__init__(message)
        self.backend = backend
        self.model = model
        self.error_type = error_type
        self.cost_estimate = cost_estimate
        self.cost_actual = cost_actual
        self.cost_in_usd_ticks = cost_in_usd_ticks


def estimate_text_cost(backend: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost for a text generation request."""
    key = f"{backend}_text" if backend in ("openai", "gemini", "grok") else backend
    rates = COST_PER_MILLION_TOKENS.get(key, {"input": 0.0, "output": 0.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def xai_cost_from_usage(api_usage) -> tuple[float | None, int | None]:
    """Extract xAI's exact billed cost from an SDK object or raw JSON usage dict."""
    if not api_usage:
        return None, None
    if isinstance(api_usage, dict):
        raw_ticks = api_usage.get("cost_in_usd_ticks")
    else:
        raw_ticks = getattr(api_usage, "cost_in_usd_ticks", None)
    if raw_ticks is None:
        return None, None
    if not isinstance(raw_ticks, (int, str, Decimal)):
        return None, None
    try:
        ticks = int(raw_ticks)
    except (TypeError, ValueError):
        return None, None
    return ticks / TICKS_PER_USD, ticks


def xai_cost_from_error(exc: Exception) -> tuple[float | None, int | None]:
    """Extract xAI billed cost from common SDK/requests error payload shapes."""
    direct = xai_cost_from_usage(getattr(exc, "usage", None))
    if direct != (None, None):
        return direct
    for payload in _error_payloads(exc):
        if not isinstance(payload, dict):
            continue
        cost = xai_cost_from_usage(payload.get("usage"))
        if cost != (None, None):
            return cost
    return None, None


def effective_cost(record: dict) -> float:
    """Return actual billed cost when present, falling back to the estimate."""
    cost = record.get("cost_actual", record.get("cost_estimate", 0))
    return float(cost or 0)


def _error_payloads(exc: Exception) -> list:
    """Collect JSON-like payloads from provider exceptions without raising."""
    payloads = []
    body = getattr(exc, "body", None)
    if body is not None:
        payloads.append(body)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payloads.append(response.json())
        except (AttributeError, ValueError, TypeError):
            pass
    payload = getattr(exc, "payload", None)
    if payload is not None:
        payloads.append(payload)
    return payloads


def _get_table():
    """Get DynamoDB table resource."""
    table_name = os.environ.get("USAGE_TABLE_NAME", "ai-slop-usage")
    return boto3.resource("dynamodb").Table(table_name)


VIDEO_MODELS = {"grok-imagine-video", "veo-3.1-fast-generate-preview"}


def _is_video_model(model: str) -> bool:
    """Video and image content are both bytes, so the model name disambiguates."""
    return model in VIDEO_MODELS or model.startswith("veo")


def record_usage(user: str, result: GenerationResult):
    """Write a usage record to DynamoDB. Failures are logged but never propagated."""
    try:
        if _is_video_model(result.model):
            mode = "video"
        elif isinstance(result.content, bytes):
            mode = "image"
        else:
            mode = "text"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        item = {
            "user": user,
            "timestamp": now,
            "status": "succeeded",
            "mode": mode,
            "backend": result.backend,
            "model": result.model,
            "cost_estimate": Decimal(str(round(result.cost_estimate, 6))),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        if result.cost_actual is not None:
            item["cost_actual"] = Decimal(str(round(result.cost_actual, 10)))
        if result.cost_in_usd_ticks is not None:
            item["cost_in_usd_ticks"] = int(result.cost_in_usd_ticks)
        _get_table().put_item(Item=item)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"USAGE RECORD ERROR: {exc}")


def record_failed_request(
    user: str,
    *,
    mode: str,
    backend: str,
    model: str = "",
    error_type: str = "provider_error",
    error_message: str = "",
    cost_estimate: float = 0.0,
    cost_actual: float | None = None,
    cost_in_usd_ticks: int | None = None,
    exc: Exception | None = None,
):
    """Write a failed provider attempt to DynamoDB. Failures are logged only."""
    try:
        if exc is not None:
            backend = getattr(exc, "backend", backend) or backend
            model = getattr(exc, "model", model) or model
            error_type = getattr(exc, "error_type", error_type) or error_type
            error_message = error_message or str(exc)
            cost_estimate = getattr(exc, "cost_estimate", cost_estimate)
            cost_actual = getattr(exc, "cost_actual", cost_actual)
            cost_in_usd_ticks = getattr(exc, "cost_in_usd_ticks", cost_in_usd_ticks)
            parsed_actual, parsed_ticks = xai_cost_from_error(exc)
            cost_actual = cost_actual if cost_actual is not None else parsed_actual
            cost_in_usd_ticks = (
                cost_in_usd_ticks if cost_in_usd_ticks is not None else parsed_ticks
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        item = {
            "user": user,
            "timestamp": now,
            "status": "failed",
            "mode": mode,
            "backend": backend,
            "model": model,
            "cost_estimate": Decimal(str(round(float(cost_estimate or 0), 6))),
            "input_tokens": 0,
            "output_tokens": 0,
            "error_type": error_type,
            "error_message": str(error_message or "")[:ERROR_MESSAGE_MAX],
        }
        if cost_actual is not None:
            item["cost_actual"] = Decimal(str(round(cost_actual, 10)))
        if cost_in_usd_ticks is not None:
            item["cost_in_usd_ticks"] = int(cost_in_usd_ticks)
        _get_table().put_item(Item=item)
    except Exception as record_exc:  # pylint: disable=broad-except
        print(f"USAGE FAILURE RECORD ERROR: {record_exc}")


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
            ProjectionExpression="cost_estimate,cost_actual",
        )
        return sum(effective_cost(r) for r in response.get("Items", []))
    except Exception as exc:  # pylint: disable=broad-except
        print(f"USAGE QUERY ERROR: {exc}")
        return 0.0


def _format_block(label: str, records: list) -> dict:
    """Format a single time window as a Slack section block."""
    count = len(records)
    failed = sum(1 for r in records if r.get("status", "succeeded") == "failed")
    total_cost = sum(effective_cost(r) for r in records)
    by_mode = {}
    for r in records:
        mode = r.get("mode", "text")
        by_mode.setdefault(mode, {"count": 0, "failed": 0, "cost": 0.0})
        by_mode[mode]["count"] += 1
        if r.get("status", "succeeded") == "failed":
            by_mode[mode]["failed"] += 1
        by_mode[mode]["cost"] += effective_cost(r)
    MODE_LABELS = {"text": "Text", "image": "Image", "video": "Video"}
    breakdown = "\n".join(
        _format_mode_line(MODE_LABELS.get(m, m), s)
        for m, s in sorted(by_mode.items())
    )
    failed_text = f" ({failed} failed)" if failed else ""
    text = f"*{label}:* {count} requests{failed_text} — ${total_cost:.2f}"
    if breakdown:
        text += f"\n{breakdown}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _format_mode_line(label: str, stats: dict) -> str:
    """Format a per-mode usage line."""
    failed_text = f", {stats['failed']} failed" if stats["failed"] else ""
    return f"  _{label}:_ {stats['count']} req{failed_text} — ${stats['cost']:.2f}"
