"""S3/CloudFront upload with Pillow compression."""

import io
import random
import string
import urllib.parse

import boto3
from PIL import Image


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
    meta_suffix = f"~{user}~{channel}" if user else ""
    final_file = f'{slug}{meta_suffix}_{rand_tag}.{extension}'
    encoded = urllib.parse.quote(final_file)
    print(f"Final file {final_file}, Encoded url: {encoded}")

    metadata = {}
    if user:
        metadata["user"] = user
    if channel:
        metadata["channel"] = channel

    s3_client.upload_fileobj(compressed, "dallepics", f"dalle/{final_file}",
                             ExtraArgs={"ContentType": content_type,
                                        "Metadata": metadata})
    uploaded_url = f"https://d2jagmvo7k5q5j.cloudfront.net/dalle/{encoded}"
    return uploaded_url
