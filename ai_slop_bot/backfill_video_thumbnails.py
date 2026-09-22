"""Create posters for existing gallery videos. Defaults to a read-only dry run."""

import argparse
import json

import boto3
from botocore.exceptions import ClientError

import hall_of_fame
import video_thumbnails


def gallery_videos(s3_client):
    """Stream the entire gallery without loading its listing into memory."""
    pages = s3_client.get_paginator("list_objects_v2").paginate(
        Bucket=hall_of_fame.BUCKET, Prefix=hall_of_fame.MEDIA_PREFIX,
    )
    for page in pages:
        for item in page.get("Contents", []):
            if item["Key"].lower().endswith(hall_of_fame.VIDEO_EXTENSIONS):
                yield item["Key"]


def backfill(s3_client, *, keys=None, apply=False, limit=None):
    """Skip existing posters; leave original media and metadata untouched."""
    counts = {"created": 0, "would_create": 0, "skipped": 0, "failed": 0}
    candidates = keys if keys is not None else gallery_videos(s3_client)
    pending = 0
    for key in candidates:
        try:
            poster_key = hall_of_fame.thumbnail_key(key)
            try:
                s3_client.head_object(Bucket=hall_of_fame.BUCKET, Key=poster_key)
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                    raise
            else:
                counts["skipped"] += 1
                continue
            pending += 1
            if not apply:
                print(f"Would create thumbnail: {key}")
                counts["would_create"] += 1
            else:
                response = s3_client.get_object(Bucket=hall_of_fame.BUCKET, Key=key)
                with response["Body"] as body:
                    if response.get("ContentLength", 0) > video_thumbnails.MAX_VIDEO_BYTES:
                        raise ValueError("Video exceeds the 200 MiB thumbnail input limit.")
                    content = body.read(video_thumbnails.MAX_VIDEO_BYTES + 1)
                video_thumbnails.upload_thumbnail(s3_client, key, content)
                counts["created"] += 1
                print(f"Created thumbnail: {key}")
        except Exception as exc:  # pylint: disable=broad-except
            counts["failed"] += 1
            print(f"Thumbnail failed: {key}: {exc}")
        if limit is not None and pending >= limit:
            break
    return counts


def main():
    """Run the operator backfill and report incomplete work through the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Generate and upload missing posters")
    parser.add_argument("--key", action="append", help="Process a specific dalle/... video key; repeatable")
    parser.add_argument("--limit", type=int, help="Process at most this many missing posters")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    counts = backfill(boto3.client("s3"), keys=args.key, apply=args.apply, limit=args.limit)
    print(json.dumps(counts, sort_keys=True))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
