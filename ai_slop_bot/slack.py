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


def post_video_response(channel_id: str, user: str, display: str, video_bytes: bytes):
    """Upload a video to Slack and post it to the channel."""
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
