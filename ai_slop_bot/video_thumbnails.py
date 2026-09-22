"""Best-effort JPEG posters for gallery videos, using the bundled FFmpeg."""

from pathlib import Path
import subprocess
import tempfile
import time

import hall_of_fame


MAX_VIDEO_BYTES = 200 * 1024 * 1024
EXTRACTION_TIMEOUT = 10


def extract_thumbnail(video_bytes: bytes) -> bytes:
    """Extract a bounded-size frame at 1s, or the first frame for short clips.

    A local input file supports MP4s with their index at the end. FFmpeg may
    only read local files, never follow network references embedded in media.
    Each invocation owns its temporary directory, which is cleaned on failure.
    """
    if not video_bytes or len(video_bytes) > MAX_VIDEO_BYTES:
        raise ValueError("Thumbnail input must be between 1 byte and 200 MiB.")
    # Lazy import: image uploads and Slack event dispatch don't need a decoder.
    import imageio_ffmpeg  # pylint: disable=import-outside-toplevel

    executable = imageio_ffmpeg.get_ffmpeg_exe()
    deadline = time.monotonic() + EXTRACTION_TIMEOUT
    with tempfile.TemporaryDirectory(prefix="slop-thumbnail-") as directory:
        source = Path(directory) / "video"
        poster = Path(directory) / "poster.jpg"
        source.write_bytes(video_bytes)
        for seek in ("1", "0"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Video thumbnail extraction timed out.")
            subprocess.run([
                executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file", "-threads", "1", "-ss", seek,
                "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-an",
                "-vf", "scale=640:640:force_original_aspect_ratio=decrease,setsar=1",
                "-threads", "1", "-q:v", "3", str(poster),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=remaining)
            # Seeking beyond a short clip may succeed without producing a frame.
            if poster.exists() and poster.stat().st_size:
                return poster.read_bytes()
    raise ValueError("No video frame was available for a thumbnail.")


def upload_thumbnail(s3_client, key: str, video_bytes: bytes) -> str:
    """Create a poster outside the gallery listing; return its public URL."""
    poster_key = hall_of_fame.thumbnail_key(key)
    jpeg = extract_thumbnail(video_bytes)
    s3_client.put_object(
        Bucket=hall_of_fame.BUCKET, Key=poster_key, Body=jpeg,
        ContentType="image/jpeg", CacheControl="public, max-age=86400",
    )
    return hall_of_fame.media_file_url(poster_key)


def try_upload_thumbnail(s3_client, key: str, video_bytes: bytes):
    """A missing decoder, timeout, or S3 error must not discard a generated video."""
    try:
        return upload_thumbnail(s3_client, key, video_bytes)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"VIDEO THUMBNAIL ERROR: {key}: {exc}")
        return None
