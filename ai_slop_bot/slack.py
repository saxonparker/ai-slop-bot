"""Slack response posting helpers."""

import json

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
                        "text": f'{user} asked ai-slop: "{display}":',
                    },
                }
            ],
            "attachments": [{"text": f"{response}"}],
        }),
        timeout=10000,
    )


def post_image_response(response_url: str, user: str, display: str, image_url: str):
    """Post an image response back to Slack."""
    requests.post(
        response_url,
        data=json.dumps({
            "response_type": "in_channel",
            "attachments": [
                {
                    "fallback": display,
                    "text": f'{user} generated: "{display}"',
                    "image_url": image_url,
                }
            ],
        }),
        timeout=10000,
    )


def post_error(response_url: str, error: str):
    """Post an error message back to Slack."""
    requests.post(
        response_url,
        data=json.dumps({"text": str(error)}),
        timeout=10000,
    )
