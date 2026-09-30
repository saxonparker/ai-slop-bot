"""Make emoji: a private, Slack-sized copy of a generated photo."""

import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import quote, urlencode

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_slop_dispatch"))

import ai_slop_bot
import ai_slop_dispatch
import emoji_maker
import hall_of_fame
import slack


KEY = 'dalle/a_"great"_cat_🏆_in_a_hat_ABC.jpeg'
RESPONSE_URL = "https://hooks.slack.com/example"


def encode(image, fmt):
    output = io.BytesIO()
    image.save(output, fmt)
    return output.getvalue()


def run_bot(message):
    ai_slop_bot.ai_slop_bot({"Records": [{"Sns": {"Message": json.dumps(message)}}]}, None)


@pytest.fixture
def s3():
    with patch("emoji_maker.boto3.client") as client:
        photo = encode(Image.new("RGB", (300, 200), "red"), "JPEG")
        client.return_value.get_object.side_effect = lambda **_: {"Body": io.BytesIO(photo)}
        yield client.return_value


@pytest.mark.parametrize("key,name", [
    (KEY, "great_cat_in_hat"),
    ("dalle/Café_déjà_vu_ABC.jpeg", "cafe_deja_vu"),
    ("dalle/" + "x" * 50 + "_ABC.jpeg", "x" * 32),
    ("dalle/a_photorealistic_image_of_a_very_small_cat_ABC.jpeg", "photorealistic_image_of_very"),
    ("dalle/🏆_ABC.jpeg", "slop"),
])
def test_names_are_short_typeable_prompt_words(key, name):
    assert emoji_maker.emoji_name(key) == name


def test_photos_are_center_cropped_to_square_png():
    wide = Image.new("RGB", (300, 100), "blue")
    wide.paste(Image.new("RGB", (100, 100), "red"), (100, 0))
    with Image.open(io.BytesIO(emoji_maker.render(encode(wide, "PNG")))) as emoji:
        assert emoji.format == "PNG"
        assert emoji.size == (128, 128)
        red, green, blue, _ = emoji.getpixel((64, 64))
        assert red > 200 and green < 50 and blue < 50
        assert emoji.getpixel((10, 64))[0] > 200  # cropped, not squashed: no blue sides


def test_transparency_survives():
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (128, 128), (0, 255, 0, 255)), (64, 64))
    with Image.open(io.BytesIO(emoji_maker.render(encode(image, "PNG")))) as emoji:
        assert emoji.getpixel((0, 0))[3] == 0
        assert emoji.getpixel((64, 64))[3] == 255


def test_even_incompressible_photos_fit_slack_size_limit():
    noise = Image.frombytes("RGBA", (512, 512), os.urandom(512 * 512 * 4))
    assert len(emoji_maker.render(encode(noise, "PNG"))) < 128 * 1024


def test_create_saves_download_outside_the_gallery(s3):
    emoji = emoji_maker.create(KEY)
    s3.get_object.assert_called_once_with(Bucket="dallepics", Key=KEY)
    put = s3.put_object.call_args.kwargs
    assert put["Key"].startswith("emoji/") and put["Key"].endswith(".png")
    assert put["ContentType"] == "image/png"
    assert put["ContentDisposition"] == 'attachment; filename="great_cat_in_hat.png"'
    assert emoji == {"name": "great_cat_in_hat", "url": hall_of_fame.media_file_url(put["Key"])}
    # Repeat clicks overwrite the same file.
    emoji_maker.create(KEY)
    assert s3.put_object.call_args.kwargs["Key"] == put["Key"]


@pytest.mark.parametrize("key", ["dalle/a_video_ABC.mp4", "source-videos/a.jpeg", "dalle/manifest.json"])
def test_create_rejects_videos_and_non_gallery_keys(s3, key):
    with pytest.raises(ValueError):
        emoji_maker.create(key)
    s3.put_object.assert_not_called()


def test_shortcut_is_queued_then_privately_returns_emoji(s3):
    payload = {"type": "message_action", "callback_id": "make_emoji", "response_url": RESPONSE_URL,
               "team": {"id": "T123", "domain": "slop-house"},
               "message": {"blocks": [{"type": "image", "image_url": hall_of_fame.CLOUDFRONT + "/" + quote(KEY)}]}}
    with patch("ai_slop_dispatch._publish") as publish:
        response = ai_slop_dispatch._handle_interaction({"body": urlencode({"payload": json.dumps(payload)})})
    assert int(response["statusCode"]) == 200
    message = publish.call_args.args[0]
    assert message["source"] == "make_emoji"
    assert message["team_domain"] == "slop-house"
    with patch("slack.requests.post") as post, patch("ai_slop_bot.budget.get_balance") as balance:
        run_bot(message)
    balance.assert_not_called()
    result = post.call_args.kwargs["json"]
    assert post.call_args.args[0] == RESPONSE_URL
    assert result["response_type"] == "ephemeral"
    assert result["replace_original"] is False
    text = result["blocks"][0]["text"]["text"]
    assert "`:great_cat_in_hat:`" in text
    assert "<https://slop-house.slack.com/customize/emoji|" in text
    emoji_url = hall_of_fame.media_file_url(s3.put_object.call_args.kwargs["Key"])
    assert f"<{emoji_url}|Download great_cat_in_hat.png>" in text
    assert result["blocks"][0]["accessory"]["image_url"] == emoji_url


@pytest.mark.parametrize("domain", ["", "evil.com/x?", "Slop House"])
def test_missing_or_odd_workspace_domain_falls_back_to_menu_directions(domain):
    with patch("slack.requests.post") as post:
        slack.post_emoji_result(RESPONSE_URL, {"name": "cat", "url": "https://example.com/cat.png"}, domain)
    text = post.call_args.kwargs["json"]["text"]
    assert ".slack.com" not in text
    assert "Customize workspace" in text


@pytest.mark.parametrize("slack_message", [
    {"text": "hello"},
    {"blocks": [{"image_url": hall_of_fame.CLOUDFRONT + "/dalle/one.jpeg"},
                {"image_url": hall_of_fame.CLOUDFRONT + "/dalle/two.jpeg"}]},
    {"text": hall_of_fame.CLOUDFRONT + "/dalle/a_video_ABC.mp4"},
])
def test_messages_without_one_photo_get_a_private_notice(s3, slack_message):
    with patch("slack.post_ephemeral") as notice:
        run_bot({"source": "make_emoji", "slack_message": slack_message, "response_url": RESPONSE_URL})
    assert "one generated photo" in notice.call_args.args[1]
    s3.put_object.assert_not_called()


def test_failures_are_reported_privately(s3):
    s3.get_object.side_effect = RuntimeError("S3 unavailable")
    slack_message = {"blocks": [{"type": "image", "image_url": hall_of_fame.CLOUDFRONT + "/" + quote(KEY)}]}
    with patch("slack.requests.post") as post:
        run_bot({"source": "make_emoji", "slack_message": slack_message, "response_url": RESPONSE_URL})
    payload = json.loads(post.call_args.kwargs["data"])
    assert payload["response_type"] == "ephemeral"
    assert payload["replace_original"] is False
    assert "Could not make an emoji" in payload["blocks"][0]["text"]["text"]
