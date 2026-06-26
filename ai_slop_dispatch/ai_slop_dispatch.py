"""Dispatch Lambda for /slop-bot. Receives Slack webhook and publishes to SNS.

Two routes are served from the same Lambda:
  POST /ai-slop       — HTTP route for Slack slash-command payloads
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


CANONICAL_SLASH_COMMAND = "/slop-bot"
HTTP_SLASH_ROUTE = "/ai-slop"


HELP_TEXT = f"""*slop-bot* — AI text, image, and video generation

*Usage:*
  `{CANONICAL_SLASH_COMMAND} <prompt>` — text response
  `{CANONICAL_SLASH_COMMAND} -i <prompt>` — image generation
  `{CANONICAL_SLASH_COMMAND} -v [seconds] <prompt>` — video generation
    Grok: default 10s, max 15s; Grok reference-to-video: max 10s; Veo: 4/6/8s, snaps to nearest
  `{CANONICAL_SLASH_COMMAND} -e <prompt>` — emoji-only text response
  `{CANONICAL_SLASH_COMMAND} -bufo <prompt>` or `{CANONICAL_SLASH_COMMAND} --bufo <prompt>` — sentiment-analyzed bufo-emoji-only rewrite using names from bufopedia.com
  `{CANONICAL_SLASH_COMMAND} -p <prompt>` — potato mode (sarcastic & rude)
  `{CANONICAL_SLASH_COMMAND} -c <prompt>` or `{CANONICAL_SLASH_COMMAND} --conversation <prompt>` — start a text-only conversation in a Slack thread
  `{CANONICAL_SLASH_COMMAND} -b <backend> <prompt>` — use a specific backend
  `{CANONICAL_SLASH_COMMAND} -u` or `{CANONICAL_SLASH_COMMAND} --usage` — show your usage stats and credit balance
  `{CANONICAL_SLASH_COMMAND} -g` or `{CANONICAL_SLASH_COMMAND} --gallery` — show the AI Slop Gallery link
  `{CANONICAL_SLASH_COMMAND} -pay <amount>` or `{CANONICAL_SLASH_COMMAND} --pay <amount>` — add credits and receive a Venmo payment link
  `{CANONICAL_SLASH_COMMAND} --report` (`-report` also works) — admin-only balance report; requires `ADMIN_USERS`
  `{CANONICAL_SLASH_COMMAND} --credit <user> <amount>` (`-credit` also works) — admin-only credit adjustment; requires `ADMIN_USERS`

*Flags can be combined:*
  `{CANONICAL_SLASH_COMMAND} -p -i a beautiful sunset` — potato mode image
  `{CANONICAL_SLASH_COMMAND} -i -b openai a cat` — image with DALL-E
  `{CANONICAL_SLASH_COMMAND} -v -b gemini a corgi surfing` — video with Veo (native audio/dialogue)

*Reference images and videos:*
  `{CANONICAL_SLASH_COMMAND} -i --upload` — open an image prompt form with 1-3 uploaded references
  `{CANONICAL_SLASH_COMMAND} -i --edit` — open the same form for an uploaded image edit
  `{CANONICAL_SLASH_COMMAND} -i --edit make this watercolor` — open the form with the prompt pre-filled
  `{CANONICAL_SLASH_COMMAND} -i --edit <image-url> turn this into a watercolor painting` — edit an image from a URL
  `{CANONICAL_SLASH_COMMAND} -i --ref <image-url> make something in this style` — add an image reference; repeat for multiple references
  `{CANONICAL_SLASH_COMMAND} -v --upload` — open a video prompt form for a start frame, loose references, or a source video
  `{CANONICAL_SLASH_COMMAND} -v --start <image-url> slow cinematic push in` — use an image URL as the start frame
  `{CANONICAL_SLASH_COMMAND} -v --ref <image-url> --ref <image-url> combine these subjects` — add image references
  `{CANONICAL_SLASH_COMMAND} -v --edit-video <video-url> restyle this clip` — edit an existing video (Grok only)
  `{CANONICAL_SLASH_COMMAND} -v --extend-video <video-url> keep the action going` — extend a video from its last frame (Grok only)
  Video edit/extend source files are mutually exclusive with reference images.
  Uploaded reference/source files are deleted from Slack after the bot downloads them.

*Conversations:*
  `{CANONICAL_SLASH_COMMAND} -c <prompt>` starts a multi-turn text conversation rooted in a
  Slack thread. Continue it by `@`-mentioning the bot in the thread:
  `@slop-bot <prompt>`. Slack does not allow slash commands inside threads,
  so use mentions for follow-ups. Conversations are text-only (`-c` cannot
  combine with `-i` or `-v`) and are capped at ~200 KB of transcript.

*Hidden directives:*
  `{CANONICAL_SLASH_COMMAND} tell me a joke [make it about dogs]` — text in `[brackets]` is sent to the AI but hidden from the channel
  `{CANONICAL_SLASH_COMMAND} what's the capital of France? ]asking for a friend[` — text in reverse `]brackets[` is shown in the channel but not sent to the AI

*Backends:*
  Text: `gemini` (default), `anthropic`, `openai`, `grok`
  Image: `grok` (default), `gemini`, `openai`
  Video: `grok` (default), `gemini` (Veo 3.1)"""


# Matches a leading Slack user mention like `<@U12345>` or `<@U12345|name>`.
_LEADING_MENTION_RE = re.compile(r"^\s*<@[UW][A-Z0-9]+(\|[^>]+)?>\s*")
_SMART_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_DASH_TRANSLATION = str.maketrans({ch: "-" for ch in _SMART_DASHES})
_LONG_FLAGS = {
    "--usage",
    "--report",
    "--gallery",
    "--pay",
    "--conversation",
    "--upload",
    "--edit",
    "--edit-video",
    "--extend-video",
    "--ref",
    "--start",
    "--credit",
    "--bufo",
}


def _normalize_flag_token(token: str) -> str:
    """Normalize smart-punctuation variants only for flag matching."""
    lower = token.lower()
    normalized = lower.translate(_DASH_TRANSLATION)
    if normalized.startswith("--"):
        return normalized
    if lower[:1] in _SMART_DASHES:
        long_form = f"--{normalized[1:]}"
        if long_form in _LONG_FLAGS:
            return long_form
    return normalized


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
    """Slack slash-command payload posted to the /ai-slop route."""
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
    if payload.get("type") == "block_actions":
        return _handle_block_action(payload)
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


def _handle_block_action(payload: dict):
    """Update the upload modal when a stateful select changes."""
    view = payload.get("view") or {}
    if view.get("callback_id") != "ai_slop_upload":
        return _json_payload({})
    actions = payload.get("actions") or []
    action = next(
        (item for item in actions if item.get("action_id") == "video_op"),
        None,
    )
    if action is None:
        return _json_payload({})

    metadata = json.loads(view.get("private_metadata") or "{}")
    if metadata.get("mode") != "video":
        return _json_payload({})

    state = (view.get("state") or {}).get("values") or {}
    upload_options = {
        "mode": "video",
        "prompt": (_state_value(state, "prompt_block", "prompt").get("value") or ""),
        "backend": _selected_value(_state_value(state, "backend_block", "backend")),
        "duration": (_state_value(state, "duration_block", "duration").get("value") or ""),
        "video_op": _selected_value(action) or "generate",
        "video_url": (
            _state_value(state, "video_url_block", "video_url").get("value") or ""
        ),
        "reference_role": _selected_value(
            _state_value(state, "reference_role_block", "reference_role")
        ),
    }
    request = {
        "view_id": view.get("id"),
        "view": _upload_view(metadata, upload_options),
    }
    if view.get("hash"):
        request["hash"] = view["hash"]
    _slack_api_post("views.update", request)
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
    tokens = prompt.split()
    return any(_normalize_flag_token(token) == "--upload" for token in tokens) or _has_bare_image_edit(tokens)


def _has_bare_image_edit(tokens: list[str]) -> bool:
    for i, token in enumerate(tokens):
        if _normalize_flag_token(token) != "--edit":
            continue
        if i + 1 >= len(tokens):
            return True
        if not _looks_like_url(tokens[i + 1]):
            return True
    return False


def _looks_like_url(token: str) -> bool:
    value = token.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    if "|" in value:
        value = value.split("|", 1)[0]
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in ("http", "https")


def _parse_upload_command(prompt: str) -> dict:
    """Parse enough flags in dispatch to build the upload modal."""
    tokens = prompt.split()
    mode = "text"
    duration = ""
    backend = ""
    video_op = "generate"
    video_url = ""
    prompt_tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower = _normalize_flag_token(token)
        if lower == "--upload":
            pass
        elif lower == "--edit":
            if i + 1 < len(tokens) and _looks_like_url(tokens[i + 1]):
                i += 1
            else:
                mode = "image"
        elif lower in ("--edit-video", "--extend-video"):
            mode = "video"
            video_op = "edit" if lower == "--edit-video" else "extend"
            if i + 1 < len(tokens) and _looks_like_url(tokens[i + 1]):
                i += 1
                video_url = tokens[i]
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
        elif lower in ("--ref", "--start") and i + 1 < len(tokens):
            i += 1
        else:
            prompt_tokens.append(token)
        i += 1
    return {
        "mode": mode,
        "duration": duration,
        "backend": backend,
        "video_op": video_op,
        "video_url": video_url,
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
    payload = {
        "trigger_id": params["trigger_id"],
        "view": _upload_view(metadata, upload_options),
    }
    _slack_api_post("views.open", payload)


def _upload_view(metadata: dict, upload_options: dict) -> dict:
    """Build the Slack modal view for the current upload options."""
    mode = metadata.get("mode", upload_options.get("mode", "image"))
    return {
        "type": "modal",
        "callback_id": "ai_slop_upload",
        "private_metadata": json.dumps(metadata),
        "title": {"type": "plain_text", "text": "slop-bot"},
        "submit": {"type": "plain_text", "text": "Generate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": _upload_blocks({**upload_options, "mode": mode}),
    }


def _upload_blocks(upload_options: dict) -> list[dict]:
    """Build upload modal blocks for the current mode and video operation."""
    mode = upload_options["mode"]
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
        video_op = upload_options.get("video_op") or "generate"
        video_op = video_op if video_op in ("generate", "edit", "extend") else "generate"
        blocks.extend([
            {
                "type": "input",
                "block_id": "video_op_block",
                "dispatch_action": True,
                "label": {"type": "plain_text", "text": "Video operation"},
                "element": {
                    "type": "static_select",
                    "action_id": "video_op",
                    "options": [
                        _option("Generate", "generate"),
                        _option("Edit", "edit"),
                        _option("Extend", "extend"),
                    ],
                    "initial_option": _option(
                        {
                            "generate": "Generate",
                            "edit": "Edit",
                            "extend": "Extend",
                        }[video_op],
                        video_op,
                    ),
                },
            },
        ])
        if video_op in ("edit", "extend"):
            blocks.extend([
                {
                    "type": "input",
                    "block_id": "video_url_block",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Source video URL"},
                    "element": _plain_text_input(
                        "video_url",
                        initial_value=upload_options.get("video_url", ""),
                    ),
                },
                {
                    "type": "input",
                    "block_id": "source_video_block",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Source video upload"},
                    "element": {
                        "type": "file_input",
                        "action_id": "source_video",
                        "filetypes": ["mp4", "mov", "webm"],
                        "max_files": 1,
                    },
                },
            ])
        blocks.append(
            {
                "type": "input",
                "optional": True,
                "block_id": "duration_block",
                "label": {"type": "plain_text", "text": "Duration"},
                "element": _plain_text_input(
                    "duration",
                    initial_value=upload_options.get("duration", ""),
                    placeholder="10",
                ),
            }
        )
        if video_op == "generate":
            reference_role = upload_options.get("reference_role") or "start"
            reference_role = (
                reference_role
                if reference_role in ("start", "reference")
                else "start"
            )
            blocks.append({
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
                    "initial_option": _option(
                        "Start frame" if reference_role == "start" else "Loose reference",
                        reference_role,
                    ),
                },
            })
    if mode == "video" and upload_options.get("video_op") in ("edit", "extend"):
        return blocks

    files_block = {
        "type": "input",
        "block_id": "files_block",
        "label": {
            "type": "plain_text",
            "text": "Reference images" if mode == "image" else "Reference images / start frame",
        },
        "element": {
            "type": "file_input",
            "action_id": "files",
            "filetypes": ["jpg", "jpeg", "png", "webp"],
            "max_files": 7 if mode == "video" else 3,
        },
    }
    if mode == "video":
        files_block["optional"] = True
    blocks.append(files_block)
    return blocks


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
    video_op = _selected_value(_state_value(state, "video_op_block", "video_op"))
    video_url = (_state_value(state, "video_url_block", "video_url").get("value") or "").strip()
    source_video_refs = _file_refs(_state_value(state, "source_video_block", "source_video"))
    role = _selected_value(_state_value(state, "reference_role_block", "reference_role"))
    file_refs = _file_refs(_state_value(state, "files_block", "files"))
    is_video_source_op = mode == "video" and video_op in ("edit", "extend")

    errors = {}
    if not prompt:
        errors["prompt_block"] = "Enter a prompt."
    has_video_url = bool(video_url) and _looks_like_url(video_url)
    if is_video_source_op and video_url and not has_video_url:
        errors["video_url_block"] = "Enter an http or https source video URL."
    if is_video_source_op and has_video_url and source_video_refs:
        errors["source_video_block"] = "Use either a source video URL or an upload, not both."
    if is_video_source_op and not has_video_url and not source_video_refs:
        errors["source_video_block"] = "Upload a source video or enter a source video URL."
    if is_video_source_op and file_refs:
        errors["files_block"] = "Reference images cannot be used with video edit/extend."
    if not is_video_source_op and source_video_refs:
        errors["source_video_block"] = "Choose Edit or Extend to use a source video."
    if not file_refs and not is_video_source_op:
        errors["files_block"] = "Upload at least one image."
    if mode == "video" and not is_video_source_op and role == "start" and len(file_refs) > 1:
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
    if is_video_source_op:
        source_video = None
        if has_video_url:
            command_parts.extend([
                "--edit-video" if video_op == "edit" else "--extend-video",
                video_url,
                prompt,
            ])
        else:
            command_parts.append(prompt)
            source_video = {**source_video_refs[0], "role": video_op}
        return {}, {
            "response_url": metadata.get("response_url", ""),
            "channel_id": metadata.get("channel_id", ""),
            "channel_name": metadata.get("channel_name", ""),
            "thread_ts": "",
            "prompt": " ".join(command_parts),
            "user": metadata.get("user", ""),
            "source": "slash",
            "reference_images": [],
            "source_video": source_video,
        }
    command_parts.append(prompt)
    reference_role = "edit" if mode == "image" else (role or "start")
    references = [
        {
            **file_ref,
            "role": reference_role,
        }
        for file_ref in file_refs
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
    return [ref["value"] for ref in _file_refs(value)]


def _file_refs(value: dict) -> list[dict]:
    files = value.get("files") or []
    refs = []
    for file_obj in files:
        if isinstance(file_obj, str):
            refs.append({"source": "slack_file", "value": file_obj})
        elif file_obj.get("id"):
            ref = {"source": "slack_file", "value": file_obj["id"]}
            if file_obj.get("mimetype"):
                ref["mime_type"] = file_obj["mimetype"]
            filename = file_obj.get("name") or file_obj.get("title")
            if filename:
                ref["filename"] = filename
            refs.append(ref)
    return refs
