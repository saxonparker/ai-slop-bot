"""Gallery permalinks resolve to exact media keys and unfurl as Slack previews."""

import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import quote

from botocore.exceptions import ClientError
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_slop_dispatch"))

import ai_slop_bot
import ai_slop_dispatch
import hall_of_fame
import slack


PHOTO = 'dalle/a_"great"_cat_🏆_ABC.jpeg'
VIDEO = "dalle/surfing_dog_XYZ.mp4"
# Exactly what the gallery's Copy link button produces (URLSearchParams encoding).
PHOTO_LINK = hall_of_fame.CLOUDFRONT + "/index.html?item=a_%22great%22_cat_%F0%9F%8F%86_ABC.jpeg"
VIDEO_LINK = hall_of_fame.CLOUDFRONT + "/index.html?item=surfing_dog_XYZ.mp4"


def test_permalinks_match_the_gallery_copy_link():
    assert hall_of_fame.permalink(PHOTO) == PHOTO_LINK
    assert hall_of_fame.permalink(VIDEO) == VIDEO_LINK


@pytest.mark.parametrize("url, key", [
    (PHOTO_LINK, PHOTO),
    (VIDEO_LINK + "#hall-of-fame", VIDEO),
    (hall_of_fame.CLOUDFRONT + "/?item=surfing_dog_XYZ.mp4", VIDEO),
    (hall_of_fame.CLOUDFRONT + "/" + quote(PHOTO), PHOTO),
    # A "/" in the prompt nests the key; "+" must not decode as a space.
    (hall_of_fame.permalink("dalle/AC/DC_1+1_ABC.mp4"), "dalle/AC/DC_1+1_ABC.mp4"),
])
def test_permalinks_and_media_urls_resolve_to_exact_keys(url, key):
    assert hall_of_fame.key_from_url(url) == key


@pytest.mark.parametrize("url", [
    "http://d2jagmvo7k5q5j.cloudfront.net/index.html?item=surfing_dog_XYZ.mp4",
    "https://example.com/index.html?item=surfing_dog_XYZ.mp4",
    hall_of_fame.CLOUDFRONT + "/index.html",
    hall_of_fame.CLOUDFRONT + "/index.html#hall-of-fame",
    hall_of_fame.CLOUDFRONT + "/index.html?item=",
    hall_of_fame.CLOUDFRONT + "/index.html?item=manifest.json",
    hall_of_fame.CLOUDFRONT + "/player.html?item=surfing_dog_XYZ.mp4",
    hall_of_fame.CLOUDFRONT + "/source-videos/a.mp4",
])
def test_other_links_are_not_gallery_items(url):
    with pytest.raises(ValueError):
        hall_of_fame.key_from_url(url)


@pytest.mark.parametrize("key, title", [
    (PHOTO, 'a "great" cat 🏆'),
    ("dalle/AC/DC_rocks_ABC.mp4", "AC/DC rocks"),
    ("dalle/untagged.jpeg", "untagged"),
])
def test_titles_match_the_gallery(key, title):
    assert hall_of_fame.title_from_key(key) == title


def test_hall_of_fame_shortcut_accepts_shared_permalinks():
    gallery = f"<{hall_of_fame.CLOUDFRONT}/index.html|gallery>"
    assert hall_of_fame.key_from_slack_message({"text": f"<{VIDEO_LINK}|surfing dog> from the {gallery}"}) == VIDEO
    unfurled = {"text": f"look <{PHOTO_LINK}>", "attachments": [{"blocks": [
        {"type": "image", "image_url": hall_of_fame.media_file_url(PHOTO)},
    ]}]}
    assert hall_of_fame.key_from_slack_message(unfurled) == PHOTO
    assert hall_of_fame.key_from_slack_message({"text": f"<{PHOTO_LINK}> <{VIDEO_LINK}>"}) is None


def link_shared(*urls):
    return {"path": "/slack/events", "body": json.dumps({"type": "event_callback", "event": {
        "type": "link_shared", "channel": "C123", "message_ts": "1700000000.000100",
        "unfurl_id": "C123.1700000000.000100.abc", "source": "conversations_history",
        "links": [{"domain": "d2jagmvo7k5q5j.cloudfront.net", "url": url} for url in urls],
    }})}


def test_dispatch_queues_only_gallery_links_for_previews():
    other = hall_of_fame.CLOUDFRONT + "/index.html#hall-of-fame"
    with patch("ai_slop_dispatch._publish") as publish:
        response = ai_slop_dispatch.dispatch(link_shared(PHOTO_LINK, other, VIDEO_LINK), None)
    assert response["statusCode"] == "200"
    publish.assert_called_once_with({
        "source": "link_shared", "unfurl_id": "C123.1700000000.000100.abc",
        "unfurl_source": "conversations_history", "channel": "C123",
        "message_ts": "1700000000.000100", "links": [PHOTO_LINK, VIDEO_LINK],
    })


def test_dispatch_ignores_links_it_cannot_preview():
    with patch("ai_slop_dispatch._publish") as publish:
        ai_slop_dispatch.dispatch(link_shared(hall_of_fame.CLOUDFRONT + "/index.html"), None)
    publish.assert_not_called()


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-token")
    with patch("hall_of_fame.boto3.client") as client:
        def head_object(**kwargs):
            if kwargs["Key"].startswith("thumbnails/"):
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {"Metadata": {"user": "bob", "channel": "videos"}}
        client.return_value.head_object.side_effect = head_object
        yield client.return_value


def shared(*links, **fields):
    message = {"source": "link_shared", "unfurl_id": "U1", "unfurl_source": "composer",
               "channel": "C123", "message_ts": "1700000000.000100", "links": list(links), **fields}
    return {"Records": [{"Sns": {"Message": json.dumps(message)}}]}


def unfurl_requests(post):
    assert {call.args[0] for call in post.call_args_list} == {"https://slack.com/api/chat.unfurl"}
    return [json.loads(call.kwargs["data"]) for call in post.call_args_list]


def test_photos_unfurl_as_images_and_videos_as_inline_players(s3):
    with patch("slack.requests.post") as post, patch("ai_slop_bot.budget.get_balance") as balance, \
            patch("ai_slop_bot.providers.get_video_provider") as provider:
        post.return_value.json.return_value = {"ok": True}
        ai_slop_bot.ai_slop_bot(shared(PHOTO_LINK, VIDEO_LINK), None)
    [request] = unfurl_requests(post)
    assert (request["unfurl_id"], request["source"]) == ("U1", "composer")
    [image, byline] = request["unfurls"][PHOTO_LINK]["blocks"]
    assert image["image_url"] == hall_of_fame.CLOUDFRONT + "/" + quote(PHOTO)
    assert image["title"]["text"] == image["alt_text"] == 'a "great" cat 🏆'
    assert byline["elements"][0]["text"] == "by bob in #videos"
    [player] = request["unfurls"][VIDEO_LINK]["blocks"]
    assert player["type"] == "video"
    assert player["video_url"] == hall_of_fame.PLAYER_URL + "?item=surfing_dog_XYZ.mp4"
    assert player["thumbnail_url"] == hall_of_fame.VIDEO_POSTER_URL
    assert player["title_url"] == VIDEO_LINK
    assert player["title"]["text"] == "surfing dog"
    assert player["description"]["text"] == "Video by bob in #videos"
    assert player["author_name"] == "bob"
    s3.head_object.assert_any_call(Bucket="dallepics", Key=VIDEO)
    balance.assert_not_called()
    provider.assert_not_called()


def test_rejected_video_embed_falls_back_to_an_escaped_card(s3):
    s3.head_object.side_effect = [
        {"Metadata": {"user": "<!channel> & co", "channel": "videos"}},
        ClientError({"Error": {"Code": "404"}}, "HeadObject"),
    ]
    with patch("slack.requests.post") as post:
        post.return_value.json.side_effect = [{"ok": False, "error": "invalid_blocks"}, {"ok": True}]
        ai_slop_bot.ai_slop_bot(shared(VIDEO_LINK), None)
    embed, card = unfurl_requests(post)
    assert embed["unfurls"][VIDEO_LINK]["blocks"][0]["type"] == "video"
    [section] = card["unfurls"][VIDEO_LINK]["blocks"]
    assert section["text"]["text"] == f"*<{VIDEO_LINK}|surfing dog>*\nVideo by &lt;!channel&gt; &amp; co in #videos"
    assert section["accessory"]["image_url"] == hall_of_fame.VIDEO_POSTER_URL


def test_generated_thumbnail_is_used_for_both_video_embed_and_fallback_card(s3):
    s3.head_object.side_effect = None
    s3.head_object.return_value = {"Metadata": {"user": "bob", "channel": "videos"}}
    with patch("slack.requests.post") as post:
        post.return_value.json.side_effect = [{"ok": False, "error": "invalid_blocks"}, {"ok": True}]
        ai_slop_bot.ai_slop_bot(shared(VIDEO_LINK), None)
    embed, card = unfurl_requests(post)
    expected = hall_of_fame.media_file_url(hall_of_fame.thumbnail_key(VIDEO))
    assert embed["unfurls"][VIDEO_LINK]["blocks"][0]["thumbnail_url"] == expected
    assert card["unfurls"][VIDEO_LINK]["blocks"][0]["accessory"]["image_url"] == expected


def test_thumbnail_access_failure_keeps_video_preview(s3):
    s3.head_object.side_effect = [
        {"Metadata": {}}, ClientError({"Error": {"Code": "403"}}, "HeadObject"),
    ]
    with patch("slack.requests.post") as post:
        post.return_value.json.return_value = {"ok": True}
        ai_slop_bot.ai_slop_bot(shared(VIDEO_LINK), None)
    [request] = unfurl_requests(post)
    assert request["unfurls"][VIDEO_LINK]["blocks"][0]["thumbnail_url"] == hall_of_fame.VIDEO_POSTER_URL


def test_photo_unfurl_failures_are_logged_without_retrying(s3):
    with patch("slack.requests.post") as post:
        post.return_value.json.return_value = {"ok": False, "error": "cannot_unfurl_url"}
        ai_slop_bot.ai_slop_bot(shared(PHOTO_LINK), None)
    assert post.call_count == 1


def test_media_s3_cannot_serve_is_not_previewed(s3):
    # Without s3:ListBucket, S3 answers 403 for a deleted key.
    s3.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
    with patch("slack.requests.post") as post:
        ai_slop_bot.ai_slop_bot(shared(PHOTO_LINK), None)
    post.assert_not_called()


def test_unfurls_without_an_unfurl_id_target_the_posted_message(s3):
    with patch("slack.requests.post") as post:
        post.return_value.json.return_value = {"ok": True}
        ai_slop_bot.ai_slop_bot(shared(PHOTO_LINK, unfurl_id=""), None)
    [request] = unfurl_requests(post)
    assert (request["channel"], request["ts"]) == ("C123", "1700000000.000100")
    assert "unfurl_id" not in request


def test_video_blocks_respect_slack_text_limits():
    item = {"key": "dalle/" + "x" * 300 + "_ABC.mp4", "title": "x" * 300, "video": True,
            "user": "u" * 80, "channel": ""}
    [player] = slack.gallery_unfurl(item)["blocks"]
    assert len(player["title"]["text"]) < 200 and player["title"]["text"].endswith("…")
    assert len(player["author_name"]) < 50
    assert player["description"]["text"] == "Video by " + "u" * 80


# 978 bytes; each character percent-encodes to 9, so its permalink is 2940 characters.
LONG_VIDEO = "dalle/" + "猫が海辺を歩いている。" * 29 + "_ABCDEFGHIJ.mp4"


def test_video_cards_fit_slack_limits_even_when_the_permalink_cannot():
    title = hall_of_fame.title_from_key(LONG_VIDEO)
    item = {"key": LONG_VIDEO, "title": title, "video": True, "user": "", "channel": ""}
    [card] = slack.gallery_unfurl(item, embed_video=False)["blocks"]
    # The unfurled message already carries the link, so drop it rather than truncate it.
    assert card["text"]["text"] == f"*{title}*\nAI Slop Gallery video"


def test_photos_too_long_for_an_image_block_unfurl_as_cards():
    # 1012 bytes, but its encoded URL exceeds the image block's 3000 characters.
    key = "dalle/" + "猫" * 330 + "_ABCDEFGHIJ.jpeg"
    item = {"key": key, "title": hall_of_fame.title_from_key(key), "video": False, "user": "alice", "channel": "cats"}
    [card] = slack.gallery_unfurl(item)["blocks"]
    assert card["type"] == "section" and "accessory" not in card
    assert card["text"]["text"] == "*" + "猫" * 330 + "*\nPhoto by alice in #cats"


def test_card_titles_shorten_without_splitting_escapes():
    item = {"key": "dalle/" + "&" * 1000 + "_ABC.mp4", "title": "&" * 1000, "video": True, "user": "", "channel": ""}
    [card] = slack.gallery_unfurl(item, embed_video=False)["blocks"]
    assert card["text"]["text"] == "*" + "&amp;" * 399 + "…*\nAI Slop Gallery video"
