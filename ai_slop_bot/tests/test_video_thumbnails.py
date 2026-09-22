"""Real decoder coverage plus bounded failures and safe, resumable backfills."""

import io
import subprocess
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
import pytest

import backfill_video_thumbnails
import hall_of_fame
from tests.thumbnail_smoke import check_thumbnail
import video_thumbnails


VIDEO = "dalle/AC/DC_🏆_ABC.mp4"
MISSING = ClientError({"Error": {"Code": "404"}}, "HeadObject")


@pytest.mark.parametrize("duration,size,expected", [
    ("2", "1280x720", (640, 360)),
    ("0.2", "720x1280", (360, 640)),
])
def test_real_ffmpeg_extracts_landscape_and_short_portrait_clips(duration, size, expected):
    assert check_thumbnail(duration, size) == expected


def test_invalid_video_fails_and_cleans_temporary_files(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with pytest.raises(subprocess.CalledProcessError):
        video_thumbnails.extract_thumbnail(b"not a video")
    assert list(tmp_path.iterdir()) == []


def test_timeout_cleans_up_and_never_uploads_a_poster(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    client = MagicMock()
    with patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"), \
            patch("video_thumbnails.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 10)) as run:
        assert video_thumbnails.try_upload_thumbnail(client, VIDEO, b"video") is None
    assert run.call_args.kwargs["timeout"] <= 10
    assert "-protocol_whitelist" in run.call_args.args[0]
    client.put_object.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_empty_and_oversized_inputs_do_not_start_ffmpeg(monkeypatch):
    monkeypatch.setattr(video_thumbnails, "MAX_VIDEO_BYTES", 4)
    with patch("video_thumbnails.subprocess.run") as run:
        for content in (b"", b"12345"):
            with pytest.raises(ValueError):
                video_thumbnails.extract_thumbnail(content)
    run.assert_not_called()


def test_thumbnail_keys_are_short_unique_and_outside_the_gallery():
    key = hall_of_fame.thumbnail_key(VIDEO)
    assert key == "thumbnails/e2b4f9f5f752ab4c5f13905f3503fec791aca407d66d9ea17f1ac500eecb9745.jpg"
    assert key.startswith("thumbnails/") and key.endswith(".jpg")
    assert len(key) == len("thumbnails/") + 64 + len(".jpg")
    assert key != hall_of_fame.thumbnail_key(VIDEO.replace(".mp4", ".mov"))
    assert len(hall_of_fame.thumbnail_key("dalle/" + "猫" * 330 + ".mp4")) == len(key)
    with pytest.raises(ValueError):
        hall_of_fame.thumbnail_key("source-videos/source.mp4")


def test_upload_stores_a_public_jpeg_without_creating_a_gallery_entry():
    client = MagicMock()
    with patch("video_thumbnails.extract_thumbnail", return_value=b"jpeg"):
        url = video_thumbnails.upload_thumbnail(client, VIDEO, b"video")
    key = hall_of_fame.thumbnail_key(VIDEO)
    assert url == hall_of_fame.CLOUDFRONT + "/" + key
    client.put_object.assert_called_once_with(
        Bucket="dallepics", Key=key, Body=b"jpeg", ContentType="image/jpeg",
        CacheControl="public, max-age=86400",
    )


def test_backfill_dry_run_never_downloads_or_writes_video():
    client = MagicMock()
    client.head_object.side_effect = MISSING
    counts = backfill_video_thumbnails.backfill(client, keys=[VIDEO])
    assert counts == {"created": 0, "would_create": 1, "skipped": 0, "failed": 0}
    client.get_object.assert_not_called()
    client.put_object.assert_not_called()


def test_backfill_resumes_skips_existing_and_limits_new_work():
    client = MagicMock()
    client.head_object.side_effect = [{}, MISSING]
    client.get_object.return_value = {"Body": io.BytesIO(b"video"), "ContentLength": 5}
    with patch("video_thumbnails.extract_thumbnail", return_value=b"jpeg"):
        counts = backfill_video_thumbnails.backfill(
            client, keys=["dalle/old.mp4", VIDEO, "dalle/later.mp4"], apply=True, limit=1,
        )
    assert counts == {"created": 1, "would_create": 0, "skipped": 1, "failed": 0}
    client.get_object.assert_called_once_with(Bucket="dallepics", Key=VIDEO)
    assert client.put_object.call_args.kwargs["Key"] == hall_of_fame.thumbnail_key(VIDEO)


def test_backfill_reports_permission_errors_without_overwriting():
    client = MagicMock()
    client.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
    counts = backfill_video_thumbnails.backfill(client, keys=[VIDEO], apply=True)
    assert counts["failed"] == 1
    client.get_object.assert_not_called()
    client.put_object.assert_not_called()


def test_backfill_paginates_and_ignores_other_gallery_objects():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "dalle/cat.jpeg"}, {"Key": VIDEO}]},
        {"Contents": [{"Key": "dalle/second.WEBM"}, {"Key": "dalle/manifest.json"}]},
    ]
    assert list(backfill_video_thumbnails.gallery_videos(client)) == [VIDEO, "dalle/second.WEBM"]
