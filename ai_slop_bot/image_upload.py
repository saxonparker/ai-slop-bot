"""S3/CloudFront upload with Pillow compression."""

import io
import json
import random
import string
import urllib.parse

import boto3
from PIL import Image

BUCKET = "dallepics"
MANIFEST_KEY = "dalle/manifest.json"


def _update_manifest(s3_client, key: str, user: str, channel: str):
    """Read the manifest, add an entry, and write it back."""
    try:
        resp = s3_client.get_object(Bucket=BUCKET, Key=MANIFEST_KEY)
        manifest = json.loads(resp["Body"].read())
    except s3_client.exceptions.NoSuchKey:
        manifest = {}
    except Exception as exc:  # pylint: disable=broad-except
        print(f"MANIFEST READ ERROR: {exc}")
        manifest = {}

    manifest[key] = {"user": user, "channel": channel}

    s3_client.put_object(
        Bucket=BUCKET, Key=MANIFEST_KEY,
        Body=json.dumps(manifest),
        ContentType="application/json",
    )


def upload_to_s3(prompt: str, file_bytes: bytes, extension: str = "jpeg",
                 user: str = "", channel: str = "") -> str:
    """Compress (if image) and upload to S3, returning the CloudFront URL."""
    s3_client = boto3.client("s3")

    if extension == "jpeg":
        image = Image.open(io.BytesIO(file_bytes))
        compressed = io.BytesIO()
        image.save(compressed, "JPEG", optimize=True, quality=50)
        compressed.seek(0)
        content_type = "image/jpeg"
    else:
        compressed = io.BytesIO(file_bytes)
        content_type = "video/mp4" if extension == "mp4" else f"application/{extension}"

    rand_tag = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    slug = prompt[:512].replace(" ", "_")
    final_file = f'{slug}_{rand_tag}.{extension}'
    s3_key = f"dalle/{final_file}"
    encoded = urllib.parse.quote(final_file)
    print(f"Final file {final_file}, Encoded url: {encoded}")

    metadata = {}
    if user:
        metadata["user"] = user
    if channel:
        metadata["channel"] = channel

    s3_client.upload_fileobj(compressed, BUCKET, s3_key,
                             ExtraArgs={"ContentType": content_type,
                                        "Metadata": metadata})

    if user or channel:
        try:
            _update_manifest(s3_client, s3_key, user, channel)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"MANIFEST WRITE ERROR: {exc}")

    uploaded_url = f"https://d2jagmvo7k5q5j.cloudfront.net/dalle/{encoded}"
    return uploaded_url
