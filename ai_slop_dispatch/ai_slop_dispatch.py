"""Dispatch Lambda for /slop-bot. Receives Slack webhook and publishes to SNS.

Two routes are served from the same Lambda:
  POST /ai-slop       — slash command (form-encoded body)
  POST /slack/events  — Events API (JSON body); we only act on app_mention
                        events inside a thread, treating them as continuation
                        turns of an existing tracked conversation. Top-level
                        @-mentions are ignored.
"""

import base64
import json
import os
import re
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
  `/slop-bot -c <prompt>` — start a conversation; reply with `@slop-bot <prompt>` in the thread to continue
  `/slop-bot -b <backend> <prompt>` — use a specific backend
  `/slop-bot -u` — show your usage stats and balance
  `/slop-bot -g` — link to the image gallery
  `/slop-bot -pay <amount>` — add credits and get a Venmo payment link

*Flags can be combined:*
  `/slop-bot -p -i a beautiful sunset` — potato mode image
  `/slop-bot -i -b openai a cat` — image with DALL-E

*Conversations:*
  `/slop-bot -c <prompt>` starts a multi-turn text conversation rooted in a
  Slack thread. Continue it by `@`-mentioning the bot in the thread:
  `@slop-bot <prompt>`. Slack does not allow slash commands inside threads,
  so use mentions for follow-ups. Conversations are text-only (`-c` cannot
  combine with `-i` or `-v`) and are capped at ~200 KB of transcript.

*Hidden directives:*
  `/slop-bot tell me a joke [make it about dogs]` — text in `[brackets]` is sent to the AI but hidden from the channel

*Backends:*
  Text: `gemini` (default), `anthropic`, `openai`, `grok`
  Image: `grok` (default), `gemini`, `openai`
  Video: `grok` (default)"""


# Matches a leading Slack user mention like `<@U12345>` or `<@U12345|name>`.
_LEADING_MENTION_RE = re.compile(r"^\s*<@[UW][A-Z0-9]+(\|[^>]+)?>\s*")


def dispatch(event, _):
    """Entry point for the dispatch Lambda. Routes by HTTP path."""
    try:
        print(event)
        path = event.get("path") or ""
        if path.endswith("/slack/events"):
            return _handle_event(event)
        return _handle_slash_command(event)
    # pylint: disable=broad-except
    except Exception as exc:
        print("DISPATCH ERROR: " + str(exc))
        traceback.print_exc()
        return _json_response(str(exc))
    # pylint: enable=broad-except


def _handle_slash_command(event):
    """Form-encoded slash command from /ai-slop. Publishes to SNS, acks fast."""
    body = _decode_body(event)
    params = dict(urllib.parse.parse_qsl(body))
    print(params)
    if "text" not in params or not params["text"]:
        return _json_response(HELP_TEXT)
    prompt = params["text"]
    if prompt.strip() in ("-h", "--help", "help"):
        return _json_response(HELP_TEXT)
    user = params["user_name"]
    print("DISPATCH COMMAND: " + prompt + " " + user)

    message = {
        "response_url": params["response_url"],
        "channel_id": params.get("channel_id", ""),
        "channel_name": params.get("channel_name", ""),
        "thread_ts": params.get("thread_ts", ""),
        "prompt": prompt,
        "user": user,
        "source": "slash",
    }
    _publish(message)
    return _json_response(f'Processing prompt "{prompt}"...')


def _handle_event(event):
    """Slack Events API. Handles url_verification + app_mention."""
    body = _decode_body(event)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _json_response("invalid event body")

    event_type = payload.get("type")
    if event_type == "url_verification":
        return {
            "statusCode": "200",
            "body": json.dumps({"challenge": payload.get("challenge", "")}),
            "headers": {"Content-Type": "application/json"},
        }

    if event_type != "event_callback":
        return _json_response("ok")

    inner = payload.get("event") or {}
    if inner.get("type") != "app_mention":
        return _json_response("ok")

    # Self-mention / bot loop guard.
    if inner.get("bot_id") or inner.get("subtype") == "bot_message":
        return _json_response("ok")

    thread_ts = inner.get("thread_ts")
    if not thread_ts:
        # Top-level @-mention: ignore silently (design choice).
        return _json_response("ok")

    raw_text = inner.get("text") or ""
    prompt = _LEADING_MENTION_RE.sub("", raw_text).strip()
    if not prompt:
        return _json_response("ok")

    channel_id = inner.get("channel", "")
    event_user_id = inner.get("user", "")
    print(f"DISPATCH MENTION: channel={channel_id} thread={thread_ts} "
          f"user={event_user_id} prompt={prompt!r}")

    message = {
        # Events API has no response_url; bot Lambda will use chat.postMessage.
        "response_url": "",
        "channel_id": channel_id,
        "channel_name": "",
        "thread_ts": thread_ts,
        "prompt": prompt,
        "user": event_user_id,
        "event_user_id": event_user_id,
        "source": "event_mention",
    }
    _publish(message)
    return _json_response("ok")


def _decode_body(event) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded", False):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _publish(message: dict):
    response = boto3.client("sns").publish(
        TopicArn=os.environ["AI_SLOP_SNS_TOPIC"],
        Message=json.dumps({"default": json.dumps(message)}),
        MessageStructure="json",
    )
    print("SNS PUBLISH: " + str(response))


def _json_response(message: str):
    """Generate a full HTTP JSON response."""
    return {
        "statusCode": "200",
        "body": json.dumps({"text": message}),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    }
