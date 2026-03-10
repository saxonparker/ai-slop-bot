"""Tests for image_upload module using mocked S3."""

import io
import sys
from unittest.mock import MagicMock, patch

sys.path.append(".")

from PIL import Image

import image_upload


def _make_test_image_bytes() -> bytes:
    """Create a minimal valid PNG image as bytes."""
    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@patch("image_upload.boto3.client")
def test_upload_to_s3_returns_cloudfront_url(mock_boto_client):
    mock_s3 = MagicMock()
    mock_boto_client.return_value = mock_s3

    image_bytes = _make_test_image_bytes()
    url = image_upload.upload_to_s3("a cute cat", image_bytes)

    assert url.startswith("https://d2jagmvo7k5q5j.cloudfront.net/dalle/")
    assert "a_cute_cat_" in url
    assert url.endswith(".jpeg")
    mock_s3.upload_fileobj.assert_called_once()
    call_args = mock_s3.upload_fileobj.call_args
    assert call_args.args[1] == "dallepics"
    assert call_args.args[2].startswith("dalle/")


@patch("image_upload.boto3.client")
def test_upload_to_s3_compresses_image(mock_boto_client):
    mock_s3 = MagicMock()
    mock_boto_client.return_value = mock_s3

    image_bytes = _make_test_image_bytes()
    image_upload.upload_to_s3("test", image_bytes)

    # Verify the uploaded data is valid JPEG (compressed from PNG input)
    uploaded_fileobj = mock_s3.upload_fileobj.call_args.args[0]
    uploaded_fileobj.seek(0)
    img = Image.open(uploaded_fileobj)
    assert img.format == "JPEG"


@patch("image_upload.boto3.client")
def test_upload_to_s3_truncates_long_prompts(mock_boto_client):
    mock_s3 = MagicMock()
    mock_boto_client.return_value = mock_s3

    long_prompt = "a" * 600
    image_bytes = _make_test_image_bytes()
    image_upload.upload_to_s3(long_prompt, image_bytes)

    s3_key = mock_s3.upload_fileobj.call_args.args[2]
    # prompt[:512] + "_" + 10 chars + ".jpeg" = should be well under 600
    filename = s3_key.removeprefix("dalle/")
    # 512 a's + _ + 10 rand + .jpeg
    assert len(filename) == 512 + 1 + 10 + 5
