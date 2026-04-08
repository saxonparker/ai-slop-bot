"""Dispatch Lambda for /slop-bot. Receives Slack webhook and publishes to SNS."""

import base64
import json
import os
import traceback
import urllib.parse

import boto3


HELP_TEXT = """*slop-bot* — AI text and image generation

*Usage:*
  `/slop-bot <prompt>` — text response
  `/slop-bot -i <prompt>` — image generation
  `/slop-bot -v [seconds] <prompt>` — video generation (default 10s, max 15s)
  `/slop-bot -e <prompt>` — emoji-only response
  `/slop-bot -p <prompt>` — potato mode (sarcastic & rude)
  `/slop-bot -b <backend> <prompt>` — use a specific backend
  `/slop-bot -u` — show your usage stats and balance
  `/slop-bot -pay <amount>` — add credits and get a Venmo payment link

*Flags can be combined:*
  `/slop-bot -p -i a beautiful sunset` — potato mode image
  `/slop-bot -i -b openai a cat` — image with DALL-E

*Hidden directives:*
  `/slop-bot tell me a joke [make it about dogs]` — text in `[brackets]` is sent to the AI but hidden from the channel

*Backends:*
  Text: `gemini` (default), `anthropic`, `openai`, `grok`
  Image: `gemini` (default), `openai`, `grok`
  Video: `grok` (default)"""


def dispatch(event, _):
    """Entry point for the dispatch Lambda. Publishes to SNS to invoke the bot Lambda."""

    def generate_response(message):
        """Generate a full HTTP JSON response."""
        return {
            "statusCode": str(200),
            "body": json.dumps({"text": message}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    try:
        print(event)
        body = event["body"]
        if event.get("isBase64Encoded", False):
            body = base64.b64decode(body).decode("ascii")
        params = dict(urllib.parse.parse_qsl(body))
        print(params)
        if "text" not in params or not params["text"]:
            return generate_response(HELP_TEXT)
        prompt = params["text"]
        if prompt.strip() in ("-h", "--help", "help"):
            return generate_response(HELP_TEXT)
        user = params["user_name"]
        print("DISPATCH COMMAND: " + prompt + " " + user)

        message = {
            "response_url": params["response_url"],
            "channel_id": params.get("channel_id", ""),
            "channel_name": params.get("channel_name", ""),
            "prompt": prompt,
            "user": user,
        }
        response = boto3.client("sns").publish(
            TopicArn=os.environ["AI_SLOP_SNS_TOPIC"],
            Message=json.dumps({"default": json.dumps(message)}),
            MessageStructure="json",
        )
        print("SNS PUBLISH: " + str(response))

        return generate_response(f'Processing prompt "{prompt}"...')
    # pylint: disable=broad-except
    except Exception as exc:
        print("DISPATCH ERROR: " + str(exc))
        traceback.print_exc()
        return generate_response(str(exc))
    # pylint: enable=broad-except
