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
import urllib.request

import boto3


HELP_TEXT = """*slop-bot* — AI text and image generation

*Usage:*
  `/slop-bot <prompt>` — text response
  `/slop-bot -i <prompt>` — image generation
  `/slop-bot -v [seconds] <prompt>` — video generation (Grok: default 10s, max 15s; Veo: 4/6/8s)
  `/slop-bot -i --upload` — image generation/editing with uploaded reference images
  `/slop-bot -v --upload` — video generation with an uploaded start/reference image
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
  `/slop-bot -v -b gemini a corgi surfing` — video with Veo (native audio/dialogue)
  `/slop-bot -i --edit <image-url> turn this into a watercolor painting`
  `/slop-bot -v --start <image-url> slow cinematic push in`
  `/slop-bot -v --ref <image-url> --ref <image-url> combine these subjects`

*Conversations:*
  `/slop-bot -c <prompt>` starts a multi-turn text conversation rooted in a
  Slack thread. Continue it by `@`-mentioning the bot in the thread:
  `@slop-bot <prompt>`. Slack does not allow slash commands inside threads,
  so use mentions for follow-ups. Conversations are text-only (`-c` cannot
  combine with `-i` or `-v`) and are capped at ~200 KB of transcript.

*Hidden directives:*
  `/slop-bot tell me a joke [make it about dogs]` — text in `[brackets]` is sent to the AI but hidden from the channel
  `/slop-bot what's the capital of France? ]asking for a friend[` — text in reverse `]brackets[` is shown in the channel but not sent to the AI

*Backends:*
  Text: `gemini` (default), `anthropic`, `openai`, `grok`
  Image: `grok` (default), `gemini`, `openai`
  Video: `grok` (default), `gemini` (Veo 3.1)"""


# Matches a leading Slack user mention like `<@U12345>` or `<@U12345|name>`.
_LEADING_MENTION_RE = re.compile(r"^\s*<@[UW][A-Z0-9]+(\|[^>]+)?>\s*")


def dispatch(event, _):
    """Entry point for the dispatch Lambda. Routes by HTTP path."""
    try:
        print(event)
        path = event.get("path") or ""
        if path.endswith("/slack/events"):
            return _handle_event(event)
        if path.endswith("/slack/interactions"):
            return _handle_interaction(event)
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
    if _is_upload_request(prompt):
        if "trigger_id" not in params:
            return _json_response("Slack did not include a trigger_id; cannot open upload modal.")
        upload_options = _parse_upload_command(prompt)
        if upload_options["mode"] not in ("image", "video"):
            return _json_response("Use --upload with -i or -v.")
        _open_upload_modal(params, upload_options)
        return _json_response("Opening upload form...")
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


def _handle_interaction(event):
    """Slack interactivity endpoint. Handles upload modal submissions."""
    body = _decode_body(event)
    params = dict(urllib.parse.parse_qsl(body))
    raw_payload = params.get("payload", "")
    if not raw_payload:
        return _json_response("missing interaction payload")

    payload = json.loads(raw_payload)
    if payload.get("type") != "view_submission":
        return _json_payload({})
    view = payload.get("view") or {}
    if view.get("callback_id") != "ai_slop_upload":
        return _json_payload({})

    errors, message = _message_from_upload_submission(view)
    if errors:
        return _json_payload({"response_action": "errors", "errors": errors})
    _publish(message)
    return _json_payload({})


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
    return _json_payload({"text": message})


def _json_payload(payload: dict):
    """Generate a full HTTP JSON response for Slack."""
    return {
        "statusCode": "200",
        "body": json.dumps(payload),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    }


def _is_upload_request(prompt: str) -> bool:
    return "--upload" in prompt.split()


def _parse_upload_command(prompt: str) -> dict:
    """Parse enough flags in dispatch to build the upload modal."""
    tokens = prompt.split()
    mode = "text"
    duration = ""
    backend = ""
    prompt_tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower = token.lower()
        if lower == "--upload":
            pass
        elif lower == "-i":
            mode = "image"
        elif lower == "-v":
            mode = "video"
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i += 1
                duration = tokens[i]
        elif lower == "-b" and i + 1 < len(tokens):
            i += 1
            backend = tokens[i].lower()
        elif lower in ("--edit", "--ref", "--start") and i + 1 < len(tokens):
            i += 1
        else:
            prompt_tokens.append(token)
        i += 1
    return {
        "mode": mode,
        "duration": duration,
        "backend": backend,
        "prompt": " ".join(prompt_tokens),
    }


def _open_upload_modal(params: dict, upload_options: dict):
    """Open the Slack file-upload modal for a slash command."""
    mode = upload_options["mode"]
    metadata = {
        "response_url": params["response_url"],
        "channel_id": params.get("channel_id", ""),
        "channel_name": params.get("channel_name", ""),
        "user": params.get("user_name", ""),
        "mode": mode,
    }
    blocks = [
        {
            "type": "input",
            "block_id": "prompt_block",
            "label": {"type": "plain_text", "text": "Prompt"},
            "element": _plain_text_input(
                "prompt",
                initial_value=upload_options.get("prompt", ""),
                multiline=True,
            ),
        },
        {
            "type": "input",
            "block_id": "backend_block",
            "label": {"type": "plain_text", "text": "Backend"},
            "element": _backend_select(mode, upload_options.get("backend", "")),
        },
    ]
    if mode == "video":
        blocks.extend([
            {
                "type": "input",
                "block_id": "duration_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Duration"},
                "element": _plain_text_input(
                    "duration",
                    initial_value=upload_options.get("duration", ""),
                    placeholder="10",
                ),
            },
            {
                "type": "input",
                "block_id": "reference_role_block",
                "label": {"type": "plain_text", "text": "Image role"},
                "element": {
                    "type": "static_select",
                    "action_id": "reference_role",
                    "options": [
                        _option("Start frame", "start"),
                        _option("Loose reference", "reference"),
                    ],
                    "initial_option": _option("Start frame", "start"),
                },
            },
        ])
    blocks.append({
        "type": "input",
        "block_id": "files_block",
        "label": {"type": "plain_text", "text": "Reference images"},
        "element": {
            "type": "file_input",
            "action_id": "files",
            "filetypes": ["jpg", "jpeg", "png", "webp"],
            "max_files": 7 if mode == "video" else 3,
        },
    })

    payload = {
        "trigger_id": params["trigger_id"],
        "view": {
            "type": "modal",
            "callback_id": "ai_slop_upload",
            "private_metadata": json.dumps(metadata),
            "title": {"type": "plain_text", "text": "slop-bot"},
            "submit": {"type": "plain_text", "text": "Generate"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": blocks,
        },
    }
    _slack_api_post("views.open", payload)


def _plain_text_input(action_id: str, *, initial_value: str = "",
                      multiline: bool = False, placeholder: str = "") -> dict:
    element = {"type": "plain_text_input", "action_id": action_id}
    if initial_value:
        element["initial_value"] = initial_value
    if multiline:
        element["multiline"] = True
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}
    return element


def _backend_select(mode: str, selected: str) -> dict:
    backends = ["grok", "gemini", "openai"] if mode == "image" else ["grok", "gemini"]
    selected = selected if selected in backends else backends[0]
    return {
        "type": "static_select",
        "action_id": "backend",
        "options": [_option(name, name) for name in backends],
        "initial_option": _option(selected, selected),
    }


def _option(label: str, value: str) -> dict:
    return {"text": {"type": "plain_text", "text": label}, "value": value}


def _slack_api_post(method: str, payload: dict):
    token = os.environ["SLACK_BOT_TOKEN"]
    request = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        data = json.loads(response.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {data.get('error')}")


def _message_from_upload_submission(view: dict) -> tuple[dict, dict | None]:
    metadata = json.loads(view.get("private_metadata") or "{}")
    state = (view.get("state") or {}).get("values") or {}
    mode = metadata.get("mode", "image")
    prompt = (_state_value(state, "prompt_block", "prompt").get("value") or "").strip()
    backend = _selected_value(_state_value(state, "backend_block", "backend"))
    duration = (_state_value(state, "duration_block", "duration").get("value") or "").strip()
    role = _selected_value(_state_value(state, "reference_role_block", "reference_role"))
    file_ids = _file_ids(_state_value(state, "files_block", "files"))

    errors = {}
    if not prompt:
        errors["prompt_block"] = "Enter a prompt."
    if not file_ids:
        errors["files_block"] = "Upload at least one image."
    if mode == "video" and role == "start" and len(file_ids) > 1:
        errors["files_block"] = "Start-frame video accepts exactly one image."
    if duration:
        try:
            int(duration)
        except ValueError:
            errors["duration_block"] = "Duration must be a whole number."
    if errors:
        return errors, None

    command_parts = ["-i" if mode == "image" else "-v"]
    if duration and mode == "video":
        command_parts.append(duration)
    if backend:
        command_parts.extend(["-b", backend])
    command_parts.append(prompt)
    reference_role = "edit" if mode == "image" else (role or "start")
    references = [
        {
            "source": "slack_file",
            "value": file_id,
            "role": reference_role,
        }
        for file_id in file_ids
    ]
    return {}, {
        "response_url": metadata.get("response_url", ""),
        "channel_id": metadata.get("channel_id", ""),
        "channel_name": metadata.get("channel_name", ""),
        "thread_ts": "",
        "prompt": " ".join(command_parts),
        "user": metadata.get("user", ""),
        "source": "slash",
        "reference_images": references,
    }


def _state_value(state: dict, block_id: str, action_id: str) -> dict:
    return ((state.get(block_id) or {}).get(action_id) or {})


def _selected_value(value: dict) -> str:
    return ((value.get("selected_option") or {}).get("value") or "")


def _file_ids(value: dict) -> list[str]:
    files = value.get("files") or []
    ids = []
    for file_obj in files:
        if isinstance(file_obj, str):
            ids.append(file_obj)
        elif file_obj.get("id"):
            ids.append(file_obj["id"])
    return ids
