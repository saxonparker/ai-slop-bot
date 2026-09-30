"""Emoji-ready copies of gallery photos.

Slack only offers an emoji upload API on Enterprise plans, so the bot prepares
the file and the user adds it in the workspace's Customize Emoji page. Emoji
live under emoji/, outside the gallery listing, keyed like video thumbnails.
"""

import hashlib
import io
import re
import unicodedata

import boto3
from PIL import Image, ImageOps

import hall_of_fame


PREFIX = "emoji"
# Slack's recommended size. A 128px RGBA PNG can't exceed Slack's 128 KB limit.
SIZE = 128
MAX_NAME_LENGTH = 32
SKIPPED_WORDS = {"a", "an", "the"}


def emoji_name(key):
    """A short, typeable name from the prompt; also the download's file name."""
    # Slack names are lowercase ASCII; fold accents (é -> e) rather than split words.
    title = unicodedata.normalize("NFKD", hall_of_fame.title_from_key(key))
    title = title.encode("ascii", "ignore").decode().lower()
    words = [word for word in re.findall(r"[a-z0-9]+", title) if word not in SKIPPED_WORDS]
    name = ""
    for word in words:
        candidate = f"{name}_{word}" if name else word[:MAX_NAME_LENGTH]
        if len(candidate) > MAX_NAME_LENGTH:
            break
        name = candidate
    return name or "slop"


def render(image_bytes):
    """Center-crop to a square PNG, keeping any transparency."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        emoji = ImageOps.fit(image.convert("RGBA"), (SIZE, SIZE), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    emoji.save(output, "PNG", optimize=True)
    return output.getvalue()


def create(key):
    """Render a gallery photo as an emoji; return its suggested name and download URL."""
    if hall_of_fame.validate_key(key).lower().endswith(hall_of_fame.VIDEO_EXTENSIONS):
        raise ValueError("Emoji can only be made from photos.")
    name = emoji_name(key)
    s3_client = boto3.client("s3")
    source = s3_client.get_object(Bucket=hall_of_fame.BUCKET, Key=key)["Body"].read()
    emoji_key = f"{PREFIX}/{hashlib.sha256(key.encode('utf-8')).hexdigest()}.png"
    s3_client.put_object(
        Bucket=hall_of_fame.BUCKET, Key=emoji_key, Body=render(source),
        ContentType="image/png",
        # Download as <name>.png so the file matches the suggested emoji name.
        ContentDisposition=f'attachment; filename="{name}.png"',
    )
    return {"name": name, "url": hall_of_fame.media_file_url(emoji_key)}
