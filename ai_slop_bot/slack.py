"""Slack response posting helpers."""

import json
import os

import requests
import hall_of_fame


def post_text_response(response_url: str, user: str, display: str, response: str,
                       render_in_block: bool = False):
    """Post a text response back to Slack.

    When render_in_block is set, the response goes in an mrkdwn section block
    (so emoji shortcodes render) instead of a legacy attachment, whose text is
    not parsed as mrkdwn.
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
    else:
        payload["attachments"] = [{"text": response}]
    requests.post(
        response_url,
        data=json.dumps(payload),
        timeout=10000,
    )


def post_text_response_in_thread(response_url: str, user: str, display: str,
                                 response: str, thread_ts: str,
                                 footer_blocks: list | None = None,
                                 render_in_block: bool = False):
    """Post a text response into a Slack thread via response_url.

    When render_in_block is set, the response goes in an mrkdwn section block
    (so emoji shortcodes render) instead of a legacy attachment, whose text is
    not parsed as mrkdwn.
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
    if render_in_block:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": response},
        })
    if footer_blocks:
        blocks.extend(footer_blocks)
    payload = {
        "response_type": "in_channel",
        "thread_ts": thread_ts,
        "blocks": blocks,
    }
    if not render_in_block:
        payload["attachments"] = [{"text": response}]
    requests.post(
        response_url,
        data=json.dumps(payload),
        timeout=10000,
    )


def post_text_chat_postmessage(channel_id: str, user: str, display: str,
                               response: str, thread_ts: str | None = None,
                               footer_blocks: list | None = None) -> str:
    """Post a text response via chat.postMessage. Returns the posted message's ts.

    Used for first-turn-at-top-level conversations to mint a thread_ts. Raises
    RuntimeError on Slack API failure (e.g. not_in_channel for DMs without bot).
    """
    token = os.environ["SLACK_BOT_TOKEN"]
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f'{user} asked slop-bot: "{display}":',
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": response},
        },
    ]
    if footer_blocks:
        blocks.extend(footer_blocks)
    payload = {
        "channel": channel_id,
        "blocks": blocks,
        "text": response,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack chat.postMessage failed: {data.get('error')}")
    return data["ts"]


def post_thread_notice(channel_id: str, thread_ts: str, text: str):
    """Post a plain notice into a thread via chat.postMessage (no preamble)."""
    token = os.environ["SLACK_BOT_TOKEN"]
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps({
            "channel": channel_id, "thread_ts": thread_ts, "text": text,
        }),
        timeout=30,
    )
    resp.raise_for_status()


def conversation_started_footer(backend: str) -> dict:
    """Return a Slack context block for the first-turn 'conversation started' footer."""
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f":speech_balloon: Conversation started — reply in this thread"
                    f" to continue. Backend: `{backend}`."
                ),
            },
        ],
    }


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


def post_image_response_in_thread(channel_id: str, user: str, display: str,
                                  image_url: str, thread_ts: str):
    """Post an image into a Slack thread via chat.postMessage.

    Used for the events-API path which has no response_url. Mirrors
    post_image_response's blocks but targets a thread on a channel directly.
    """
    token = os.environ["SLACK_BOT_TOKEN"]
    payload = {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": f'{user} generated: "{display}"',
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
    }
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack chat.postMessage (image) failed: {data.get('error')}")


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


def _gallery_card(key: str, title: str, description: str, video: bool) -> dict:
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
        card["accessory"] = {"type": "image", "image_url": hall_of_fame.VIDEO_POSTER_URL, "alt_text": "Video"}
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
            "thumbnail_url": hall_of_fame.VIDEO_POSTER_URL,
            "provider_name": "AI Slop Gallery",
        }
        if item["user"]:
            video["author_name"] = _truncate(item["user"], 49)
        return {"blocks": [video]}
    # Video cards, and photos whose encoded URL is too long for an image block
    return {"blocks": [_gallery_card(key, title, description, item["video"])]}


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


def post_video_response(channel_id: str, user: str, display: str, video_bytes: bytes,
                        thread_ts: str | None = None) -> str:
    """Upload a video, post it to the channel/thread, and return its Slack file ID."""
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
    complete_payload = {
        "files": [{"id": file_id, "title": display}],
        "channel_id": channel_id,
        "initial_comment": f'{user} generated video: "{display}"',
    }
    if thread_ts:
        complete_payload["thread_ts"] = thread_ts
    complete_resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={**headers, "Content-Type": "application/json"},
        json=complete_payload,
        timeout=30,
    )
    complete_data = complete_resp.json()
    if not complete_data.get("ok"):
        raise RuntimeError(f"Slack completeUploadExternal failed: {complete_data.get('error')}")
    print(f"SLACK UPLOAD: shared to channel {channel_id}")
    return file_id


def get_user_display_name(user_id: str) -> str:
    """Resolve a Slack user id to a display name via users.info, or fall back.

    Cosmetic only: used by the events-API path so transcripts read like
    `aaron asked` instead of `U12345 asked`. Any error returns the user id.
    """
    if not user_id:
        return user_id
    try:
        token = os.environ["SLACK_BOT_TOKEN"]
        resp = requests.get(
            "https://slack.com/api/users.info",
            headers={"Authorization": f"Bearer {token}"},
            params={"user": user_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            print(f"users.info failed for {user_id}: {data.get('error')}")
            return user_id
        profile = (data.get("user") or {}).get("profile") or {}
        return (
            profile.get("display_name")
            or (data.get("user") or {}).get("name")
            or user_id
        )
    # pylint: disable=broad-except
    except Exception as exc:
        print(f"users.info exception for {user_id}: {exc}")
        return user_id
    # pylint: enable=broad-except


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
