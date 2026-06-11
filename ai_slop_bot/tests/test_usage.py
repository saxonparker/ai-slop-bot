"""Tests for usage tracking module."""

import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.append(".")

from usage import (
    GenerationResult,
    effective_cost,
    estimate_text_cost,
    get_total_cost,
    get_usage_summary,
    record_usage,
    xai_cost_from_usage,
)


# ── Cost estimation ──────────────────────────────────────────────────────────

def test_estimate_text_cost_anthropic():
    # 1000 input tokens @ $3/M + 500 output tokens @ $15/M
    cost = estimate_text_cost("anthropic", 1000, 500)
    assert abs(cost - 0.0105) < 1e-9


def test_estimate_text_cost_openai():
    cost = estimate_text_cost("openai", 1000, 1000)
    # 1000 * 5/1M + 1000 * 15/1M = 0.005 + 0.015 = 0.02
    assert abs(cost - 0.02) < 1e-9


def test_estimate_text_cost_gemini():
    cost = estimate_text_cost("gemini", 1000, 1000)
    # 1000 * 0.50/1M + 1000 * 3.00/1M = 0.0005 + 0.003 = 0.0035
    assert abs(cost - 0.0035) < 1e-9


def test_estimate_text_cost_grok():
    cost = estimate_text_cost("grok", 1000, 1000)
    # 1000 * 0.20/1M + 1000 * 0.50/1M = 0.0002 + 0.0005 = 0.0007
    assert abs(cost - 0.0007) < 1e-9


def test_estimate_text_cost_unknown_backend():
    cost = estimate_text_cost("unknown", 1000, 1000)
    assert cost == 0.0


# ── GenerationResult ─────────────────────────────────────────────────────────

def test_generation_result_text():
    r = GenerationResult("hello", "anthropic", "claude-sonnet-4-6", 10, 20, 0.001)
    assert r.content == "hello"
    assert r.backend == "anthropic"
    assert r.model == "claude-sonnet-4-6"


def test_generation_result_image():
    r = GenerationResult(b"\x89PNG", "gemini", "gemini-image", 0, 0, 0.04)
    assert isinstance(r.content, bytes)
    assert r.cost_estimate == 0.04


def test_xai_cost_from_usage_object():
    cost, ticks = xai_cost_from_usage(SimpleNamespace(cost_in_usd_ticks=200000000))
    assert ticks == 200000000
    assert cost == 0.02


def test_xai_cost_from_usage_dict():
    cost, ticks = xai_cost_from_usage({"cost_in_usd_ticks": "123456789"})
    assert ticks == 123456789
    assert cost == 0.0123456789


def test_effective_cost_prefers_actual():
    record = {"cost_estimate": Decimal("0.05"), "cost_actual": Decimal("0.02")}
    assert effective_cost(record) == 0.02


# ── record_usage ─────────────────────────────────────────────────────────────

@patch("usage.boto3")
def test_record_usage_writes_item(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    result = GenerationResult("hello", "anthropic", "claude-sonnet-4-6", 100, 200, 0.0033)
    record_usage("testuser", result)

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["user"] == "testuser"
    assert item["mode"] == "text"
    assert item["backend"] == "anthropic"
    assert item["model"] == "claude-sonnet-4-6"
    assert item["input_tokens"] == 100
    assert item["output_tokens"] == 200
    assert isinstance(item["cost_estimate"], Decimal)


@patch("usage.boto3")
def test_record_usage_writes_actual_cost(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    result = GenerationResult(
        "hello", "grok", "grok-4-1-fast-non-reasoning", 100, 200, 0.0033,
        cost_actual=0.0025, cost_in_usd_ticks=25000000,
    )
    record_usage("testuser", result)

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["cost_actual"] == Decimal("0.0025")
    assert item["cost_in_usd_ticks"] == 25000000


@patch("usage.boto3")
def test_record_usage_image_mode(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    result = GenerationResult(b"\x89PNG", "gemini", "gemini-image", 0, 0, 0.04)
    record_usage("testuser", result)

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["mode"] == "image"


@patch("usage.boto3")
def test_record_usage_video_mode(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    result = GenerationResult(b"\x00\x00\x00\x1cftypisom", "grok", "grok-imagine-video", 0, 0, 0.40)
    record_usage("testuser", result)

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["mode"] == "video"


@patch("usage.boto3")
def test_record_usage_veo_video_mode(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    # Veo bytes look like image bytes, so the model name must mark it as video.
    result = GenerationResult(b"\x00\x00\x00\x1cftypisom", "gemini", "veo-3.1-fast-generate-preview", 0, 0, 1.20)
    record_usage("testuser", result)

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["mode"] == "video"


@patch("usage.boto3")
def test_record_usage_swallows_exceptions(mock_boto3):
    mock_boto3.resource.side_effect = RuntimeError("DynamoDB is down")

    result = GenerationResult("hello", "anthropic", "model", 10, 20, 0.001)
    # Should not raise
    record_usage("testuser", result)


# ── get_usage_summary ────────────────────────────────────────────────────────

@patch("usage.boto3")
def test_get_usage_summary_no_records(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": []}

    result = get_usage_summary("testuser")
    assert result == "No usage recorded yet."


@patch("usage.boto3")
def test_get_usage_summary_formats_output(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    now = datetime.now(timezone.utc)
    recent_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    mock_table.query.return_value = {"Items": [
        {"timestamp": recent_ts, "mode": "text", "backend": "anthropic", "cost_estimate": Decimal("0.05")},
        {"timestamp": recent_ts, "mode": "image", "backend": "gemini", "cost_estimate": Decimal("0.02")},
        {"timestamp": old_ts, "mode": "text", "backend": "anthropic", "cost_estimate": Decimal("0.10")},
    ]}

    result = get_usage_summary("testuser")
    assert isinstance(result, list)
    assert len(result) == 3
    # Each entry is a Slack section block
    texts = [b["text"]["text"] for b in result]
    assert any("Last 7 days" in t for t in texts)
    assert any("All time" in t for t in texts)
    assert any("3 requests" in t for t in texts)
    assert any("$0.17" in t for t in texts)
    # Verify per-mode breakdown is present
    all_text = "\n".join(texts)
    assert "Text:" in all_text
    assert "Image:" in all_text


@patch("usage.boto3")
def test_get_usage_summary_prefers_actual_cost(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock_table.query.return_value = {"Items": [
        {
            "timestamp": now,
            "mode": "image",
            "backend": "grok",
            "cost_estimate": Decimal("0.05"),
            "cost_actual": Decimal("0.02"),
        },
    ]}

    result = get_usage_summary("testuser")
    texts = [b["text"]["text"] for b in result]
    assert any("$0.02" in t for t in texts)


@patch("usage.boto3")
def test_get_total_cost_prefers_actual_cost(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": [
        {"cost_estimate": Decimal("0.05"), "cost_actual": Decimal("0.02")},
        {"cost_estimate": Decimal("0.03")},
    ]}

    assert get_total_cost("testuser") == 0.05


@patch("usage.boto3")
def test_get_usage_summary_handles_query_error(mock_boto3):
    mock_boto3.resource.side_effect = RuntimeError("DynamoDB is down")

    result = get_usage_summary("testuser")
    assert result == "Failed to retrieve usage data."
