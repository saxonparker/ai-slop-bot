"""Tests for reference image resolution helpers."""

import io
import sys
from unittest.mock import MagicMock, patch

sys.path.append(".")

from PIL import Image

import media_refs


def _image_bytes(fmt="PNG") -> bytes:
    image = Image.new("RGB", (32, 32), color="blue")
    buf = io.BytesIO()
    image.save(buf, fmt)
    return buf.getvalue()


def test_reference_from_slack_escaped_url():
    ref = media_refs.reference_from_url("<https://example.com/cat.png|cat>", role="edit")
    assert ref.source == "url"
    assert ref.value == "https://example.com/cat.png"
    assert ref.role == "edit"


@patch("media_refs.requests.get")
def test_resolve_url_reference_normalizes_image(mock_get):
    mock_get.return_value = MagicMock(
        content=_image_bytes(),
        headers={"Content-Type": "image/png"},
        raise_for_status=MagicMock(),
    )

    resolved = media_refs.resolve_reference_image(
        media_refs.ReferenceImage(source="url", value="https://example.com/cat.png")
    )

    assert resolved.mime_type == "image/jpeg"
    assert resolved.original_url == "https://example.com/cat.png"
    assert resolved.data_uri().startswith("data:image/jpeg;base64,")


@patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-token"})
@patch("media_refs.requests.post")
@patch("media_refs.requests.get")
def test_resolve_slack_file_downloads_private_url_then_deletes(mock_get, mock_post):
    info_resp = MagicMock()
    info_resp.json.return_value = {
        "ok": True,
        "file": {
            "url_private_download": "https://files.slack.com/private",
            "mimetype": "image/png",
        },
    }
    info_resp.raise_for_status = MagicMock()
    file_resp = MagicMock(
        content=_image_bytes(),
        headers={"Content-Type": "image/png"},
        raise_for_status=MagicMock(),
    )
    mock_get.side_effect = [info_resp, file_resp]
    delete_resp = MagicMock()
    delete_resp.json.return_value = {"ok": True}
    delete_resp.raise_for_status = MagicMock()
    mock_post.return_value = delete_resp

    resolved = media_refs.resolve_reference_image(
        media_refs.ReferenceImage(source="slack_file", value="F123")
    )

    assert resolved.file_id == "F123"
    assert mock_get.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer xoxb-token"
    mock_post.assert_called_once_with(
        "https://slack.com/api/files.delete",
        headers={"Authorization": "Bearer xoxb-token"},
        data={"file": "F123"},
        timeout=30,
    )


@patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-token"})
@patch("media_refs.requests.post")
@patch("media_refs.requests.get")
def test_resolve_slack_file_ignores_cleanup_failure(mock_get, mock_post):
    info_resp = MagicMock()
    info_resp.json.return_value = {
        "ok": True,
        "file": {
            "url_private_download": "https://files.slack.com/private",
            "mimetype": "image/png",
        },
    }
    info_resp.raise_for_status = MagicMock()
    file_resp = MagicMock(
        content=_image_bytes(),
        headers={"Content-Type": "image/png"},
        raise_for_status=MagicMock(),
    )
    mock_get.side_effect = [info_resp, file_resp]
    delete_resp = MagicMock()
    delete_resp.json.return_value = {"ok": False, "error": "cant_delete_file"}
    delete_resp.raise_for_status = MagicMock()
    mock_post.return_value = delete_resp

    resolved = media_refs.resolve_reference_image(
        media_refs.ReferenceImage(source="slack_file", value="F123")
    )

    assert resolved.file_id == "F123"
