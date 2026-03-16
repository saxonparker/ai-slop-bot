#!/usr/bin/env python3
"""Scrape CloudWatch Logs for all prompts sent through the AI Slack bot."""

import ast
import csv
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3

REGION = "us-east-2"
DISPATCH_LOG_GROUP = "/aws/lambda/ai-slop-dispatch"
BOT_LOG_GROUP = "/aws/lambda/ai-slop-bot"
LOOKBACK_DAYS = 30
MAX_RESULTS_PER_QUERY = 10000


def run_insights_query(client, log_group, query, start, end):
    """Run a CloudWatch Logs Insights query and poll until complete."""
    resp = client.start_query(
        logGroupName=log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
        limit=MAX_RESULTS_PER_QUERY,
    )
    query_id = resp["queryId"]

    while True:
        result = client.get_query_results(queryId=query_id)
        status = result["status"]
        if status in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(0.5)

    if status != "Complete":
        print(f"WARNING: Query against {log_group} ended with status: {status}", file=sys.stderr)
        return []

    rows = []
    for entry in result["results"]:
        row = {field["field"]: field["value"] for field in entry}
        rows.append(row)

    print(f"  {log_group}: {len(rows)} results (stats: {result.get('statistics', {})})", file=sys.stderr)
    if len(rows) == MAX_RESULTS_PER_QUERY:
        print(f"  WARNING: hit {MAX_RESULTS_PER_QUERY} limit — some results may be missing. "
              "Consider reducing LOOKBACK_DAYS.", file=sys.stderr)
    return rows


def classify_prompt(text):
    """Classify the raw command text: 'image', 'text', or 'usage'."""
    tokens = text.split()
    if "-u" in tokens or "--usage" in tokens:
        return "usage"
    if "-i" in tokens:
        return "image"
    return "text"


def parse_dispatch_logs(rows):
    """Extract user + prompt from the params dict printed on dispatch line 54."""
    records = []
    for row in rows:
        msg = row.get("@message", "")
        ts = row.get("@timestamp", "")

        # The log line is the repr of a dict from urllib.parse.parse_qsl
        # Try to find and parse the dict in the message
        try:
            # CloudWatch messages may have prefixes (request ID, etc.) — find the dict
            dict_start = msg.index("{")
            dict_str = msg[dict_start:]
            params = ast.literal_eval(dict_str)
            raw_text = params.get("text", "")
            user = params.get("user_name", "")
            records.append({
                "timestamp": ts,
                "user": user,
                "prompt": raw_text,
                "type": classify_prompt(raw_text),
                "source": "dispatch",
            })
        except (ValueError, SyntaxError):
            # Fall back: try parsing DISPATCH COMMAND line
            if "DISPATCH COMMAND:" in msg:
                after = msg.split("DISPATCH COMMAND:", 1)[1].strip()
                records.append({
                    "timestamp": ts,
                    "user": "?",
                    "prompt": after,
                    "type": classify_prompt(after),
                    "source": "dispatch-fallback",
                })
    return records



# Known system message endings (from prompts.py) used to find the split point
# between the system message and the user prompt in GENERATE TEXT log lines.
SYSTEM_MSG_ENDINGS = [
    "not what you think should have been asked.",   # default
    "sarcasm loses punch when it's long-winded",    # potato
    "Make it work.",                                 # corn (matthew)
]


def parse_bot_logs(rows):
    """Extract system prompt + user prompt from GENERATE TEXT lines."""
    records = []
    for row in rows:
        msg = row.get("@message", "")
        ts = row.get("@timestamp", "")

        if "GENERATE TEXT:" not in msg:
            continue

        after = msg.split("GENERATE TEXT:", 1)[1].strip()
        # Format: "f{system}, {prompt}" — the system message contains commas,
        # so we find the known ending of the system message to split correctly.
        system = ""
        prompt = after
        for ending in SYSTEM_MSG_ENDINGS:
            idx = after.find(ending)
            if idx != -1:
                split_pos = idx + len(ending)
                system = after[:split_pos].strip()
                # Skip the ", " separator between system and prompt
                remainder = after[split_pos:]
                if remainder.startswith(", "):
                    remainder = remainder[2:]
                prompt = remainder.strip()
                break

        records.append({
            "timestamp": ts,
            "system": system,
            "prompt": prompt,
            "source": "bot",
        })
    return records


def main():
    client = boto3.client("logs", region_name=REGION)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    print(f"Querying CloudWatch Logs from {start.date()} to {end.date()} ...", file=sys.stderr)

    # Query 1: dispatch params dict (has user + prompt cleanly separated)
    # Use two simple filters — the complex regex was too strict and missed results
    dispatch_query = """
        fields @timestamp, @message
        | filter @message like "'text'"
        | filter @message like "'user_name'"
        | sort @timestamp asc
    """
    dispatch_rows = run_insights_query(client, DISPATCH_LOG_GROUP, dispatch_query, start, end)
    dispatch_records = parse_dispatch_logs(dispatch_rows)

    # Query 2: bot GENERATE TEXT lines (has system prompt context)
    bot_query = """
        fields @timestamp, @message
        | filter @message like /GENERATE TEXT:/
        | filter @message not like /GENERATE TEXT COMPLETE:/
        | sort @timestamp asc
    """
    bot_rows = run_insights_query(client, BOT_LOG_GROUP, bot_query, start, end)
    bot_records = parse_bot_logs(bot_rows)

    # Split dispatch records by type
    text_records = [r for r in dispatch_records if r["type"] == "text"]
    image_records = [r for r in dispatch_records if r["type"] == "image"]
    usage_records = [r for r in dispatch_records if r["type"] == "usage"]

    # Print text prompts (the ones that went to Anthropic)
    print(f"\n{'='*120}", file=sys.stderr)
    print(f"TEXT PROMPTS (Anthropic) — {len(text_records)} found", file=sys.stderr)
    print(f"IMAGE PROMPTS (Gemini) — {len(image_records)} found", file=sys.stderr)
    print(f"USAGE QUERIES (skipped) — {len(usage_records)} found", file=sys.stderr)
    print(f"BOT LOG entries (skipped — only contain system message fragments) — {len(bot_records)}", file=sys.stderr)
    print(f"{'='*120}", file=sys.stderr)

    if text_records:
        print(f"\n{'TIMESTAMP':<28} {'USER':<20} PROMPT")
        print(f"{'-'*28} {'-'*20} {'-'*70}")
        for r in text_records:
            print(f"{r['timestamp']:<28} {r['user']:<20} {r['prompt']}")

    # Export text prompts (Anthropic) to one TSV
    text_tsv = "prompts_text.tsv"
    with open(text_tsv, "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["timestamp", "user", "prompt"])
        for r in text_records:
            writer.writerow([r["timestamp"], r["user"], r["prompt"]])

    # Export image prompts (Gemini) to another TSV
    image_tsv = "prompts_image.tsv"
    with open(image_tsv, "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["timestamp", "user", "prompt"])
        for r in image_records:
            writer.writerow([r["timestamp"], r["user"], r["prompt"]])

    print(f"\nExported to {text_tsv} ({len(text_records)}) and {image_tsv} ({len(image_records)})", file=sys.stderr)


if __name__ == "__main__":
    main()
