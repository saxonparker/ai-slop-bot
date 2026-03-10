"""S3/CloudFront upload with Pillow compression."""

import io
import random
import string
import urllib.parse

import boto3
from PIL import Image


def upload_to_s3(prompt: str, image_bytes: bytes) -> str:
    """Compress image and upload to S3, returning the CloudFront URL."""
    s3_client = boto3.client("s3")

    image = Image.open(io.BytesIO(image_bytes))
    compressed = io.BytesIO()
    image.save(compressed, "JPEG", optimize=True, quality=50)
    compressed.seek(0)

    rand_tag = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    final_file = f'{prompt[:512].replace(" ", "_")}_{rand_tag}.jpeg'
    encoded = urllib.parse.quote(final_file)
    print(f"Final file {final_file}, Encoded url: {encoded}")

    s3_client.upload_fileobj(compressed, "dallepics", f"dalle/{final_file}")
    uploaded_url = f"https://d2jagmvo7k5q5j.cloudfront.net/dalle/{encoded}"
    return uploaded_url
