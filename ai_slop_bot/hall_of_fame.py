"""Shared gallery links, Hall of Fame storage, and the public curation API.

One DynamoDB item per selected S3 key keeps independent edits from overwriting
each other. The gallery is intentionally open to curation by anyone with its
link; Slack uses the same store after normal signature verification. Gallery
permalinks (index.html?item=<key without "dalle/">) resolve to the same keys
for Slack link previews.
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
PLAYER_URL = CLOUDFRONT + "/player.html"
VIDEO_POSTER_URL = CLOUDFRONT + "/video-poster.png"
API_PATH = "/gallery/hall-of-fame"
MEDIA_PREFIX = "dalle/"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")
MEDIA_EXTENSIONS = (".jpeg", ".jpg", ".png", ".gif", ".webp") + VIDEO_EXTENSIONS


def is_enabled():
    """Keep existing deployments functional until the table is configured."""
    return bool(os.environ.get("HALL_OF_FAME_TABLE_NAME"))


def _table():
    return boto3.resource("dynamodb").Table(os.environ["HALL_OF_FAME_TABLE_NAME"])


def validate_key(key):
    """Only generated gallery media can be curated (never uploads/metadata)."""
    if (not isinstance(key, str) or not key.startswith(MEDIA_PREFIX)
            or len(key.encode("utf-8")) > 1024
            or not key.lower().endswith(MEDIA_EXTENSIONS)):
        raise ValueError("Choose a photo or video from the gallery.")
    return key


def key_from_url(url):
    """Recover an exact S3 key from a media URL or gallery permalink.

    Punctuation and Unicode in prompts survive both encodings.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != urllib.parse.urlsplit(CLOUDFRONT).netloc:
        raise ValueError("Choose a photo or video from the gallery.")
    if parsed.path in ("/", "/index.html"):
        item = urllib.parse.parse_qs(parsed.query).get("item", [""])[0]
        return validate_key(MEDIA_PREFIX + item)
    return validate_key(urllib.parse.unquote(parsed.path.lstrip("/")))


def _item_query(key):
    return urllib.parse.urlencode({"item": key[len(MEDIA_PREFIX):]})


def permalink(key):
    """The link the gallery's Copy link button shares."""
    return f"{CLOUDFRONT}/index.html?{_item_query(key)}"


def player_url(key):
    """An embeddable player page for Slack video blocks."""
    return f"{PLAYER_URL}?{_item_query(key)}"


def media_file_url(key):
    """The CloudFront URL of the media itself, as image_upload returns it."""
    return f"{CLOUDFRONT}/{urllib.parse.quote(key)}"


def title_from_key(key):
    """Match the gallery's titleFromKey: drop the prefix, extension, and random tag."""
    name = key[len(MEDIA_PREFIX):]
    dot = name.rfind(".")
    if dot > 0:
        name = name[:dot]
    underscore = name.rfind("_")
    if underscore > 0:
        name = name[:underscore]
    return name.replace("_", " ")


def media_details(key):
    """Describe gallery media for Slack previews, or None if S3 can't serve it."""
    try:
        head = boto3.client("s3").head_object(Bucket=BUCKET, Key=validate_key(key))
    except ClientError as exc:
        # Without s3:ListBucket, S3 reports a missing key as 403 instead of 404.
        print(f"GALLERY MEDIA LOOKUP ERROR: {key}: {exc}")
        return None
    metadata = head.get("Metadata") or {}
    return {
        "key": key,
        "title": title_from_key(key),
        "video": key.lower().endswith(VIDEO_EXTENSIONS),
        "user": metadata.get("user", ""),
        "channel": metadata.get("channel", ""),
    }


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
            # Media URLs and permalinks (index.html?item=...) shared from the gallery
            for url in re.findall(re.escape(CLOUDFRONT) + r'/(?:dalle/|(?:index\.html)?\?)[^\s<>|]+', value):
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
