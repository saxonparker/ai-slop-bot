"""Tests for usage tracking module."""

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.append(".")

from usage import GenerationResult, estimate_text_cost, record_usage, get_usage_summary


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
    # 1000 * 0.15/1M + 1000 * 0.60/1M = 0.00015 + 0.0006 = 0.00075
    assert abs(cost - 0.00075) < 1e-9


def test_estimate_text_cost_unknown_backend():
    cost = estimate_text_cost("unknown", 1000, 1000)
    assert cost == 0.0


# ── GenerationResult ─────────────────────────────────────────────────────────

def test_generation_result_text():
    r = GenerationResult("hello", "anthropic", "claude-sonnet-4-20250514", 10, 20, 0.001)
    assert r.content == "hello"
    assert r.backend == "anthropic"
    assert r.model == "claude-sonnet-4-20250514"


def test_generation_result_image():
    r = GenerationResult(b"\x89PNG", "gemini", "gemini-image", 0, 0, 0.04)
    assert isinstance(r.content, bytes)
    assert r.cost_estimate == 0.04


# ── record_usage ─────────────────────────────────────────────────────────────

@patch("usage.boto3")
def test_record_usage_writes_item(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    result = GenerationResult("hello", "anthropic", "claude-sonnet-4-20250514", 100, 200, 0.0033)
    record_usage("testuser", result)

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["user"] == "testuser"
    assert item["mode"] == "text"
    assert item["backend"] == "anthropic"
    assert item["model"] == "claude-sonnet-4-20250514"
    assert item["input_tokens"] == 100
    assert item["output_tokens"] == 200
    assert isinstance(item["cost_estimate"], Decimal)


@patch("usage.boto3")
def test_record_usage_image_mode(mock_boto3):
    mock_table = MagicMock()
    mock_boto3.resource.return_value.Table.return_value = mock_table

    result = GenerationResult(b"\x89PNG", "gemini", "gemini-image", 0, 0, 0.04)
    record_usage("testuser", result)

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["mode"] == "image"


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
        {"timestamp": recent_ts, "backend": "anthropic", "cost_estimate": Decimal("0.05")},
        {"timestamp": recent_ts, "backend": "gemini", "cost_estimate": Decimal("0.02")},
        {"timestamp": old_ts, "backend": "anthropic", "cost_estimate": Decimal("0.10")},
    ]}

    result = get_usage_summary("testuser")
    assert "*7d:*" in result
    assert "*All:*" in result
    assert "3 req" in result
    assert "$0.17" in result


@patch("usage.boto3")
def test_get_usage_summary_handles_query_error(mock_boto3):
    mock_boto3.resource.side_effect = RuntimeError("DynamoDB is down")

    result = get_usage_summary("testuser")
    assert result == "Failed to retrieve usage data."
