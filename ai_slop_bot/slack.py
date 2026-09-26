"""Slack response posting helpers."""

import json
import os

import requests
import hall_of_fame


def post_text_response(response_url: str, user: str, display: str, response: str,
                       render_in_block: bool = False, actions: dict | None = None):
    """Post a text response back to Slack.

    When render_in_block is set, the response goes in an mrkdwn section block
    (so emoji shortcodes render) instead of a legacy attachment, whose text is
    not parsed as mrkdwn. `actions` (e.g. conversation_action) renders below
    the response.
    """
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f'{user} asked slop-bot: "{display}":',
            },
        },
    ]
    payload = {
        "response_type": "in_channel",
        "blocks": blocks,
    }
    if render_in_block:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": response},
        })
        if actions:
            blocks.append(actions)
    else:
        payload["attachments"] = [{"text": response}]
        if actions:
            # Top-level blocks render above attachments; a block-only
            # attachment keeps the button under the response text.
            payload["attachments"].append({"blocks": [actions]})
    requests.post(
        response_url,
        data=json.dumps(payload),
        timeout=10000,
    )


def post_image_response(response_url: str, user: str, display: str, image_url: str):
    """Post an image response back to Slack."""
    requests.post(
        response_url,
        data=json.dumps({
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f'{user} generated: "{display}"',
                    },
                },
                {
                    "type": "image",
                    "image_url": image_url,
                    "alt_text": display,
                },
            ],
        }),
        timeout=10000,
    )


def conversation_action(conversation_id: str) -> dict:
    """Continue button carried by every continuable text reply."""
    return {
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "Continue"},
            "action_id": "conversation_continue",
            "value": conversation_id,
        }],
    }


def hall_of_fame_action(key: str, featured: bool = True) -> dict:
    """Use explicit add/remove actions so old Slack messages stay safe to click."""
    return {
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": (
                "🏆 Add to Hall of Fame" if featured else "Remove from Hall of Fame"
            )},
            "action_id": "hall_of_fame_add" if featured else "hall_of_fame_remove",
            "value": key,
        }],
    }


def post_hall_of_fame_result(response_url: str, selection: dict):
    """Confirm privately with an undo button, preserving the generated post."""
    text = "Added to Hall of Fame." if selection["featured"] else "Removed from Hall of Fame."
    resp = requests.post(response_url, json={
        "response_type": "ephemeral", "replace_original": False, "text": text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"{text} <{hall_of_fame.GALLERY_URL}|View Hall of Fame>"
            )}},
            hall_of_fame_action(selection["key"], not selection["featured"]),
        ],
    }, timeout=30)
    resp.raise_for_status()


# Slack's cap on section text and on image block URLs.
SLACK_TEXT_LIMIT = 3000


def _escape_mrkdwn(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mrkdwn_within(text: str, limit: int) -> str:
    """Escape text for mrkdwn, shortening the text (never an escape) to fit."""
    escaped = _escape_mrkdwn(text)
    if len(escaped) <= limit:
        return escaped
    pieces, size = [], len("…")
    for char in text:
        piece = _escape_mrkdwn(char)
        if size + len(piece) > limit:
            break
        pieces.append(piece)
        size += len(piece)
    return "".join(pieces) + "…"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _gallery_card(key: str, title: str, description: str, video: bool,
                  thumbnail_url: str = hall_of_fame.VIDEO_POSTER_URL) -> dict:
    """A section preview that stays within Slack's text limit.

    Permalinks percent-encode the whole prompt, so a long non-ASCII prompt
    can leave no room for one. The unfurled message already carries the
    link, so drop it rather than truncate the URL.
    """
    title = _mrkdwn_within(title, 2000)
    description = _mrkdwn_within(description, 900)
    text = f"*<{hall_of_fame.permalink(key)}|{title}>*\n{description}"
    if len(text) > SLACK_TEXT_LIMIT:
        text = f"*{title}*\n{description}"
    card = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    if video:
        card["accessory"] = {"type": "image", "image_url": thumbnail_url, "alt_text": "Video"}
    return card


def gallery_unfurl(item: dict, embed_video: bool = True) -> dict:
    """Preview gallery media: the photo itself, an inline player, or a card."""
    key = item["key"]
    title = item["title"].strip() or "Untitled"
    byline = " ".join(part for part in (
        f"by {item['user']}" if item["user"] else "",
        f"in #{item['channel']}" if item["channel"] else "",
    ) if part)
    kind = "video" if item["video"] else "photo"
    description = f"{kind.capitalize()} {byline}" if byline else f"AI Slop Gallery {kind}"
    image_url = hall_of_fame.media_file_url(key)
    thumbnail_url = item.get("thumbnail_url") or hall_of_fame.VIDEO_POSTER_URL
    if not item["video"] and len(image_url) <= SLACK_TEXT_LIMIT:
        blocks = [{
            "type": "image",
            "image_url": image_url,
            "alt_text": _truncate(title, 2000),
            "title": {"type": "plain_text", "text": _truncate(title, 2000)},
        }]
        if byline:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _mrkdwn_within(byline, 2000)}]})
        return {"blocks": blocks}
    if item["video"] and embed_video:
        # Slack requires a thumbnail and an embeddable page on the unfurl domain.
        video = {
            "type": "video",
            "title": {"type": "plain_text", "text": _truncate(title, 199)},
            "title_url": hall_of_fame.permalink(key),
            "description": {"type": "plain_text", "text": _truncate(description, 199)},
            "alt_text": _truncate(title, 2000),
            "video_url": hall_of_fame.player_url(key),
            "thumbnail_url": thumbnail_url,
            "provider_name": "AI Slop Gallery",
        }
        if item["user"]:
            video["author_name"] = _truncate(item["user"], 49)
        return {"blocks": [video]}
    # Video cards, and photos whose encoded URL is too long for an image block
    return {"blocks": [_gallery_card(key, title, description, item["video"], thumbnail_url)]}


def post_gallery_unfurls(message: dict, items: dict):
    """Unfurl gallery links, retrying with video cards if Slack rejects an embed."""
    try:
        _chat_unfurl(message, {url: gallery_unfurl(item) for url, item in items.items()})
    except RuntimeError as exc:
        if not any(item["video"] for item in items.values()):
            raise
        print(f"SLACK VIDEO UNFURL REJECTED, USING CARDS: {exc}")
        _chat_unfurl(message, {url: gallery_unfurl(item, embed_video=False) for url, item in items.items()})


def _chat_unfurl(message: dict, unfurls: dict):
    token = os.environ["SLACK_BOT_TOKEN"]
    # unfurl_id + source also covers links still in the message composer.
    if message.get("unfurl_id") and message.get("unfurl_source"):
        target = {"unfurl_id": message["unfurl_id"], "source": message["unfurl_source"]}
    else:
        target = {"channel": message["channel"], "ts": message["message_ts"]}
    resp = requests.post(
        "https://slack.com/api/chat.unfurl",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps({**target, "unfurls": unfurls}),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack chat.unfurl failed: {data.get('error')}")


def post_video_response(channel_id: str, user: str, display: str, video_bytes: bytes) -> str:
    """Upload a video, post it to the channel, and return its Slack file ID."""
    token = os.environ["SLACK_BOT_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    filename = display[:100].replace(" ", "_") + ".mp4"

    # Step 1: Request an upload URL
    print(f"SLACK UPLOAD: requesting upload URL for {len(video_bytes)} bytes")
    resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers=headers,
        data={"filename": filename, "length": len(video_bytes)},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack getUploadURLExternal failed: {data.get('error')}")
    upload_url = data["upload_url"]
    file_id = data["file_id"]
    print(f"SLACK UPLOAD: got file_id={file_id}")

    # Step 2: Upload the file bytes
    upload_resp = requests.post(
        upload_url, files={"file": (filename, video_bytes, "video/mp4")}, timeout=60,
    )
    upload_resp.raise_for_status()
    print("SLACK UPLOAD: file uploaded")

    # Step 3: Complete the upload and share to channel
    complete_resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "files": [{"id": file_id, "title": display}],
            "channel_id": channel_id,
            "initial_comment": f'{user} generated video: "{display}"',
        },
        timeout=30,
    )
    complete_data = complete_resp.json()
    if not complete_data.get("ok"):
        raise RuntimeError(f"Slack completeUploadExternal failed: {complete_data.get('error')}")
    print(f"SLACK UPLOAD: shared to channel {channel_id}")
    return file_id


def post_ephemeral(response_url: str, text: str = "", blocks: list[dict] | None = None):
    """Post a message only visible to the requesting user."""
    if blocks is None:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    payload = {"response_type": "ephemeral", "replace_original": False, "blocks": blocks}
    if text:
        payload["text"] = text
    requests.post(response_url, data=json.dumps(payload), timeout=10000)


def post_error(response_url: str, error: str):
    """Post an error message back to Slack."""
    requests.post(
        response_url,
        data=json.dumps({"text": str(error)}),
        timeout=10000,
    )
