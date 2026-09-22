"""Exercise the actual packaged decoder, also run inside the Lambda base image."""

import io
from pathlib import Path
import subprocess
import tempfile

import imageio_ffmpeg
from PIL import Image

import video_thumbnails


def check_thumbnail(duration="2", size="1280x720"):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "clip.mp4"
        subprocess.run([
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=red:s={size}:r=10", "-t", duration,
            "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(source),
        ], check=True, timeout=20)
        # Default MP4 muxing puts its index at EOF, exercising seekable input.
        jpeg = video_thumbnails.extract_thumbnail(source.read_bytes())
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 640
        assert image.width > 0 and image.height > 0
        red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        assert red > 200 and green < 30 and blue < 30
        return image.size


if __name__ == "__main__":
    assert check_thumbnail() == (640, 360)
    assert check_thumbnail(duration="0.2", size="720x1280") == (360, 640)
    print("Packaged FFmpeg extracted landscape and short portrait thumbnails successfully.")
