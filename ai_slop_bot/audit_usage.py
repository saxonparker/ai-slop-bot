"""Audit usage rows in DynamoDB against provider billing totals."""

import argparse
import csv
import json
import os
from collections import defaultdict
from decimal import Decimal

import boto3

from usage import effective_cost


def main():
    """Run the usage audit CLI."""
    parser = argparse.ArgumentParser(
        description="Scan ai-slop usage records and aggregate cost by day.",
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("USAGE_TABLE_NAME", "ai-slop-usage"),
        help="DynamoDB usage table name.",
    )
    parser.add_argument("--start-date", help="Inclusive UTC date: YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Inclusive UTC date: YYYY-MM-DD.")
    parser.add_argument("--backend", help="Filter to one backend, e.g. grok.")
    parser.add_argument("--mode", help="Filter to one mode: text, image, or video.")
    parser.add_argument("--status", help="Filter to succeeded or failed.")
    parser.add_argument("--user", help="Filter to one Slack user.")
    parser.add_argument("--model", help="Filter to one model.")
    parser.add_argument(
        "--details-csv",
        help="Write matching per-request rows to this CSV path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print aggregate summary as JSON instead of a table.",
    )
    args = parser.parse_args()

    records = [
        record for record in scan_usage_table(args.table)
        if matches_filters(record, args)
    ]
    summary = summarize(records)

    if args.details_csv:
        write_details_csv(args.details_csv, records)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_summary(summary, records)


def scan_usage_table(table_name: str) -> list[dict]:
    """Scan the usage table, following DynamoDB pagination."""
    table = boto3.resource("dynamodb").Table(table_name)
    records = []
    kwargs = {}
    while True:
        response = table.scan(**kwargs)
        records.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return records
        kwargs["ExclusiveStartKey"] = last_key


def matches_filters(record: dict, args) -> bool:
    """Return True when a usage row matches CLI filters."""
    timestamp = str(record.get("timestamp", ""))
    day = timestamp[:10]
    if args.start_date and day < args.start_date:
        return False
    if args.end_date and day > args.end_date:
        return False
    for attr in ("backend", "mode", "status", "user", "model"):
        expected = getattr(args, attr)
        if expected and str(record.get(attr, "")) != expected:
            return False
    return True


def summarize(records: list[dict]) -> list[dict]:
    """Aggregate usage by UTC day, backend, mode, and model."""
    buckets = defaultdict(lambda: {
        "requests": 0,
        "estimated_cost": 0.0,
        "actual_cost": 0.0,
        "effective_cost": 0.0,
        "actual_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    })
    for record in records:
        key = (
            str(record.get("timestamp", ""))[:10],
            str(record.get("backend", "")),
            str(record.get("mode", "")),
            str(record.get("status", "succeeded")),
            str(record.get("model", "")),
        )
        bucket = buckets[key]
        bucket["requests"] += 1
        bucket["estimated_cost"] += float(record.get("cost_estimate", 0) or 0)
        bucket["effective_cost"] += effective_cost(record)
        bucket["input_tokens"] += int(record.get("input_tokens", 0) or 0)
        bucket["output_tokens"] += int(record.get("output_tokens", 0) or 0)
        if "cost_actual" in record:
            bucket["actual_requests"] += 1
            bucket["actual_cost"] += float(record.get("cost_actual", 0) or 0)

    rows = []
    for (day, backend, mode, status, model), values in buckets.items():
        row = {
            "date": day,
            "backend": backend,
            "mode": mode,
            "status": status,
            "model": model,
            **values,
        }
        row["estimate_delta"] = row["effective_cost"] - row["estimated_cost"]
        rows.append(row)
    return sorted(rows, key=lambda row: (
        row["date"], row["backend"], row["mode"], row["status"], row["model"],
    ))


def write_details_csv(path: str, records: list[dict]):
    """Write matching usage records to CSV for spreadsheet-level inspection."""
    fields = [
        "timestamp",
        "user",
        "backend",
        "mode",
        "status",
        "model",
        "input_tokens",
        "output_tokens",
        "cost_estimate",
        "cost_actual",
        "cost_in_usd_ticks",
        "error_type",
        "error_message",
        "effective_cost",
    ]
    with open(path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields)
        writer.writeheader()
        for record in sorted(records, key=lambda item: str(item.get("timestamp", ""))):
            row = {field: decimal_to_plain(record.get(field, "")) for field in fields}
            row["effective_cost"] = f"{effective_cost(record):.10f}"
            writer.writerow(row)


def print_summary(summary: list[dict], records: list[dict]):
    """Print an aligned aggregate table."""
    print(f"Matched {len(records)} usage records")
    if not summary:
        return
    headers = [
        "date",
        "backend",
        "mode",
        "status",
        "requests",
        "actual_req",
        "estimated",
        "actual",
        "effective",
        "delta",
        "model",
    ]
    rows = [
        [
            row["date"],
            row["backend"],
            row["mode"],
            row["status"],
            str(row["requests"]),
            str(row["actual_requests"]),
            money(row["estimated_cost"]),
            money(row["actual_cost"]),
            money(row["effective_cost"]),
            money(row["estimate_delta"]),
            row["model"],
        ]
        for row in summary
    ]
    widths = [
        max(len(str(value)) for value in [header] + [row[idx] for row in rows])
        for idx, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


def money(value: float) -> str:
    """Format a cost value with enough precision for sub-cent audit work."""
    return f"${value:.6f}"


def decimal_to_plain(value):
    """Convert DynamoDB Decimal values to JSON/CSV-friendly scalars."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


if __name__ == "__main__":
    main()
