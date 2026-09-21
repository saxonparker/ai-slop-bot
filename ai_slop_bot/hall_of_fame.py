"""Shared Hall of Fame storage and the gallery's public curation API.

One DynamoDB item per selected S3 key keeps independent edits from overwriting
each other. The gallery is intentionally open to curation by anyone with its
link; Slack uses the same store after normal signature verification.
"""

import base64
import json
import os
import re
import urllib.parse

import boto3
from botocore.exceptions import ClientError


BUCKET = "dallepics"
CLOUDFRONT = "https://d2jagmvo7k5q5j.cloudfront.net"
GALLERY_URL = CLOUDFRONT + "/index.html#hall-of-fame"
API_PATH = "/gallery/hall-of-fame"
MEDIA_EXTENSIONS = (".jpeg", ".jpg", ".png", ".gif", ".webp", ".mp4", ".mov", ".webm")


def is_enabled():
    """Keep existing deployments functional until the table is configured."""
    return bool(os.environ.get("HALL_OF_FAME_TABLE_NAME"))


def _table():
    return boto3.resource("dynamodb").Table(os.environ["HALL_OF_FAME_TABLE_NAME"])


def validate_key(key):
    """Only generated gallery media can be curated (never uploads/metadata)."""
    if (not isinstance(key, str) or not key.startswith("dalle/")
            or len(key.encode("utf-8")) > 1024
            or not key.lower().endswith(MEDIA_EXTENSIONS)):
        raise ValueError("Choose a photo or video from the gallery.")
    return key


def key_from_url(url):
    """Recover an exact S3 key, including punctuation and Unicode in prompts."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != urllib.parse.urlsplit(CLOUDFRONT).netloc:
        raise ValueError("Choose a photo or video from the gallery.")
    return validate_key(urllib.parse.unquote(parsed.path.lstrip("/")))


def _media_table():
    return boto3.resource("dynamodb").Table(os.environ["GALLERY_MEDIA_TABLE_NAME"])


def register_slack_file(file_id, media_url):
    """Link an uploaded Slack video to its gallery copy without adding UI clutter."""
    _media_table().put_item(Item={"slack_file_id": file_id, "media_key": key_from_url(media_url)})


def key_from_slack_message(message):
    """Resolve image blocks/links or a registered Slack file to one gallery item."""
    candidates = set()

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for url in re.findall(re.escape(CLOUDFRONT) + r'/dalle/[^\s<>|]+', value):
                try:
                    candidates.add(key_from_url(url))
                except ValueError:
                    pass

    visit(message)
    for file in message.get("files") or []:
        if file.get("id"):
            row = _media_table().get_item(Key={"slack_file_id": file["id"]}, ConsistentRead=True).get("Item")
            if row:
                candidates.add(validate_key(row["media_key"]))
    # Don't silently select the wrong media from a message containing several.
    return next(iter(candidates)) if len(candidates) == 1 else None


def list_keys():
    """Read all selections, including galleries larger than a scan page."""
    table = _table()
    keys = []
    params = {"ConsistentRead": True, "ProjectionExpression": "media_key"}
    while True:
        result = table.scan(**params)
        keys.extend(row["media_key"] for row in result.get("Items", []))
        if not result.get("LastEvaluatedKey"):
            return keys
        params["ExclusiveStartKey"] = result["LastEvaluatedKey"]


def set_featured(key, featured):
    """Set explicit membership so duplicate clicks/retries never toggle it."""
    validate_key(key)
    if not isinstance(featured, bool):
        raise ValueError("featured must be true or false.")
    if featured:
        try:
            boto3.client("s3").head_object(Bucket=BUCKET, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise ValueError("That media is no longer in the gallery.") from exc
            raise
        _table().put_item(Item={"media_key": key})
    else:
        _table().delete_item(Key={"media_key": key})
    return {"key": key, "featured": featured}


def handle_http(event):
    """Public GET/PUT endpoint; API Gateway supplies the configured CORS headers."""
    if not is_enabled():
        return _response(503, {"error": "Hall of Fame is not configured yet."})
    method = event.get("httpMethod", "")
    try:
        if method == "GET":
            return _response(200, {"keys": list_keys()})
        if method != "PUT":
            return _response(405, {"error": "Use GET or PUT."})
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        if len(body) > 8192:
            raise ValueError("Request is too large.")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return _response(200, set_featured(data.get("key"), data.get("featured")))
    except (ValueError, UnicodeError) as exc:
        return _response(400, {"error": str(exc)})
    except Exception as exc:  # pylint: disable=broad-except
        print(f"HALL OF FAME ERROR: {exc}")
        return _response(503, {"error": "Hall of Fame is unavailable. Please try again."})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }
