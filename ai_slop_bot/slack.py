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
                        "text": f'{user} asked slop-bot: "{display}":',
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": response,
                    },
                },
            ],
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


def post_ephemeral(response_url: str, text: str):
    """Post a message only visible to the requesting user."""
    requests.post(
        response_url,
        data=json.dumps({
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
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
