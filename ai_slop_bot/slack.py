"""Slack response posting helpers."""

import json
import os

import requests


def post_text_response(response_url: str, user: str, display: str, response: str):
    """Post a text response back to Slack."""
    requests.post(
        response_url,
        data=json.dumps({
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f'{user} asked slop-bot: "{display}":',
                    },
                },
            ],
            "attachments": [{"text": response}],
        }),
        timeout=10000,
    )


def post_text_response_in_thread(response_url: str, user: str, display: str,
                                 response: str, thread_ts: str,
                                 footer_blocks: list | None = None):
    """Post a text response into a Slack thread via response_url."""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f'{user} asked slop-bot: "{display}":',
            },
        },
    ]
    if footer_blocks:
        blocks.extend(footer_blocks)
    requests.post(
        response_url,
        data=json.dumps({
            "response_type": "in_channel",
            "thread_ts": thread_ts,
            "blocks": blocks,
            "attachments": [{"text": response}],
        }),
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


def post_video_response(channel_id: str, user: str, display: str, video_bytes: bytes,
                        thread_ts: str | None = None):
    """Upload a video to Slack and post it to the channel (or a thread)."""
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
    payload = {"response_type": "ephemeral", "blocks": blocks}
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
