"""Unified Slack generation form. Only stdlib dependencies belong in dispatch.

The prompt stays in a stable Slack input. Compact private metadata retains each
mode's settings and uploaded file IDs across view updates. File inputs cannot be
prefilled, so retained uploads are shown as removable selections instead.
"""

import copy
import json
import os
import re
import time
import urllib.parse


CALLBACK_ID = "ai_slop_generate"
MAX_AGE_SECONDS = 25 * 60
BACKENDS = {
    "text": {"openai": "OpenAI", "grok": "Grok", "anthropic": "Claude", "gemini": "Gemini"},
    "image": {"grok": "Grok", "gemini": "Gemini", "openai": "OpenAI"},
    "video": {"grok": "Grok", "gemini": "Gemini / Veo"},
}
PRESET_VOICES = {
    "altair": "M", "ara": "F", "atlas": "M", "aurora": "F",
    "carina": "F", "castor": "M", "celeste": "F", "cosmo": "M",
    "eve": "F", "helios": "M", "helix": "M", "iris": "F",
    "kepler": "M", "leo": "M", "liora": "F", "lumen": "M",
    "luna": "F", "lux": "M", "naksh": "M", "orion": "M",
    "perseus": "M", "rex": "M", "rigel": "M", "sal": "M",
    "sirius": "M", "ursa": "F", "zagan": "M", "zenith": "M",
}
FIELDS = {
    "backend", "output", "search", "potato", "image_op", "video_op",
    "duration", "resolution", "role", "urls", "video_url", "voices", "custom_voices",
}


def _plain(text):
    return {"type": "plain_text", "text": text}


def _option(value, label):
    return {"text": _plain(label), "value": str(value)}


def _select(action, options, selected):
    choices = [_option(value, label) for value, label in options.items()]
    return {
        "type": "static_select", "action_id": action, "options": choices,
        "initial_option": next(option for option in choices if option["value"] == str(selected)),
    }


def _text(action, value="", *, multiline=False, max_length=3000):
    element = {
        "type": "plain_text_input", "action_id": action,
        "multiline": multiline, "max_length": max_length,
    }
    if value:
        element["initial_value"] = value
    return element


def _input(mode, action, label, element, *, optional=False, dynamic=False, suffix=""):
    # pylint: disable=too-many-arguments
    return {
        "type": "input", "block_id": f"{mode}_{action}{suffix}_block",
        "label": _plain(label), "optional": optional,
        "dispatch_action": dynamic, "element": element,
    }


def _note(text):
    return {"type": "context", "elements": [_plain(text)]}


def _defaults(mode):
    backend = os.environ.get(f"{mode.upper()}_BACKEND", next(iter(BACKENDS[mode])))
    if backend not in BACKENDS[mode]:
        backend = next(iter(BACKENDS[mode]))
    return {
        "backend": backend, "output": "normal", "search": "auto", "potato": [],
        "image_op": "generate", "video_op": "generate", "role": "start",
        "duration": "8" if backend == "gemini" else "10", "resolution": "1080p",
        "urls": "", "video_url": "", "voices": [], "custom_voices": "",
        "images": [], "videos": [],
    }


def _normalize(mode, draft):
    """Adjust controls after changing provider or operation, before rendering."""
    if draft["backend"] not in BACKENDS[mode]:
        draft["backend"] = _defaults(mode)["backend"]
    if mode == "text" and (draft["backend"] == "gemini" or draft["output"] != "normal"):
        draft["search"] = "off"
    if mode == "video":
        if draft["backend"] == "gemini":
            draft["video_op"] = "generate"
            draft["role"] = "start"
            durations = (4, 6, 8)
        else:
            durations = range(1, 11 if draft["role"] == "reference" and draft["video_op"] == "generate" else 16)
        try:
            requested = int(draft["duration"])
        except ValueError:
            requested = int(_defaults(mode)["duration"])
        draft["duration"] = str(min(durations, key=lambda value: abs(value - requested)))


def initial_view(params, options=None):
    """Open a form in the slash command's channel, with optional flag prefills."""
    options = options or {}
    mode = options.get("mode", "text")
    draft = _defaults(mode)
    draft.update({key: value for key, value in options.items() if key in FIELDS and value != ""})
    if draft["backend"] not in BACKENDS[mode]:
        raise ValueError(f"Choose a {mode} provider: {', '.join(BACKENDS[mode])}.")
    if draft["resolution"] not in ("480p", "720p", "1080p"):
        raise ValueError("Use -r with 480, 720, or 1080.")
    if len(draft["voices"]) > 3:
        raise ValueError("Choose at most 3 voices.")
    if len(options.get("prompt", "")) > 3000:
        raise ValueError("The generation form supports prompts up to 3,000 characters.")
    _normalize(mode, draft)
    metadata = {
        "mode": mode, "revision": 0, "opened_at": int(time.time()),
        "context": {key: params.get(key, "") for key in (
            "response_url", "channel_id", "channel_name", "user_name",
        )},
        "drafts": {mode: draft},
    }
    return build_view(metadata, options.get("prompt", ""))


def build_view(metadata, prompt="", *, notice=""):
    """Render only controls supported by the active mode and provider."""
    mode = metadata["mode"]
    draft = metadata["drafts"][mode]
    backend = draft["backend"]
    buttons = []
    for name in BACKENDS:
        button = {"type": "button", "action_id": f"mode_{name}", "value": name, "text": _plain(name.title())}
        if name == mode:
            button["style"] = "primary"
        buttons.append(button)
    blocks = [{"type": "actions", "block_id": "generation_types", "elements": buttons}]
    if notice:
        blocks.append(_note(notice))
    blocks.extend([
        {"type": "input", "block_id": "prompt_block", "label": _plain("Prompt"),
         "element": _text("prompt", prompt, multiline=True)},
        _input(mode, "backend", "Provider", _select("backend", BACKENDS[mode], backend), dynamic=True),
    ])
    if mode == "text":
        blocks.append(_input(mode, "output", "Response style", _select("output", {
            "normal": "Text", "emoji": "Emoji only", "bufo": "Bufo emojis",
        }, draft["output"]), dynamic=True))
        if backend != "gemini" and draft["output"] == "normal":
            blocks.append(_input(mode, "search", "Web search", _select("search", {
                "auto": "Auto", "required": "Required", "off": "Off",
            }, draft["search"])))
        else:
            blocks.append(_note("Web search is off for Gemini and emoji responses."))
    elif mode == "image":
        blocks.append(_input(mode, "image_op", "Operation", _select("image_op", {
            "generate": "Generate", "edit": "Edit an image",
        }, draft["image_op"]), dynamic=True))
        blocks.extend(_image_inputs(metadata, 3))
    else:
        blocks.extend(_video_inputs(metadata))
    if not (mode == "text" and draft["output"] == "bufo"):
        potato = {"type": "checkboxes", "action_id": "potato",
                  "options": [_option("on", "Potato mode (sarcastic and rude)")]}
        if draft["potato"]:
            potato["initial_options"] = potato["options"]
        blocks.append(_input(mode, "potato", "Style", potato, optional=True))
    channel = metadata["context"].get("channel_name")
    blocks.append(_note(f"Results post to #{channel}." if channel else
                        "Results post to the conversation where you opened this form."))
    return {
        "type": "modal", "callback_id": CALLBACK_ID, "title": _plain("Generate with slop-bot"),
        "submit": _plain("Generate"), "close": _plain("Cancel"),
        "private_metadata": _encode(metadata), "blocks": blocks,
    }


def _encode(metadata):
    encoded = json.dumps(metadata, separators=(",", ":"), ensure_ascii=False)
    if len(encoded) > 3000:
        raise ValueError("This draft is too large to switch settings. "
                         "Shorten the reference URLs or submit the current form.")
    return encoded


def _image_inputs(metadata, limit):
    mode = metadata["mode"]
    draft = metadata["drafts"][mode]
    blocks = [_input(mode, "urls", "Image URLs (one per line)",
                     _text("urls", draft["urls"], multiline=True), optional=True)]
    label = "Reference images / start frame" if mode == "video" else "Reference images"
    blocks.extend(_uploads(metadata, "images", label, ["jpg", "jpeg", "png", "webp"], limit))
    note = f"Use uploads, URLs, or both; at most {limit} image{'s' if limit != 1 else ''} in total."
    if mode == "image" and draft["image_op"] == "edit":
        note += " Editing requires at least one image."
    blocks.append(_note(note))
    return blocks


def _uploads(metadata, kind, label, filetypes, limit):
    mode = metadata["mode"]
    files = metadata["drafts"][mode][kind]
    revision = f"_{metadata['revision']}"
    blocks = []
    if files:
        options = [_option(file["id"], file["name"]) for file in files]
        blocks.append(_input(mode, f"keep_{kind}", f"Keep uploaded {kind}", {
            "type": "multi_static_select", "action_id": f"keep_{kind}",
            "options": options, "initial_options": options,
        }, optional=True, suffix=revision))
    blocks.append(_input(mode, f"upload_{kind}", label, {
        "type": "file_input", "action_id": f"upload_{kind}", "filetypes": filetypes,
        "max_files": limit,
    }, optional=True, suffix=revision))
    return blocks


def _video_inputs(metadata):
    draft = metadata["drafts"]["video"]
    grok = draft["backend"] == "grok"
    operation = draft["video_op"]
    blocks = []
    if grok:
        blocks.append(_input("video", "video_op", "Operation", _select("video_op", {
            "generate": "Generate", "edit": "Edit a video", "extend": "Extend a video",
        }, operation), dynamic=True))
    else:
        blocks.append(_note("Veo generates videos with native audio. An optional image supplies the start frame."))
    durations = ((4, 6, 8) if not grok else
                 range(1, 11 if operation == "generate" and draft["role"] == "reference" else 16))
    suffix = f"_{draft['backend']}_{operation}_{draft['role']}"
    blocks.append(_input("video", "duration", "Duration", _select("duration", {
        str(value): f"{value} seconds" for value in durations
    }, draft["duration"]), suffix=suffix))
    if operation != "generate":
        blocks.append(_input("video", "video_url", "Source video URL",
                             _text("video_url", draft["video_url"]), optional=True))
        blocks.extend(_uploads(metadata, "videos", "Source video upload", ["mp4", "mov", "webm"], 1))
        blocks.append(_note("Supply one source video, using a URL or an upload. "
                            "Resolution follows the source, capped at 720p."))
        return blocks
    if grok:
        blocks.extend([
            _input("video", "resolution", "Resolution", _select("resolution", {
                "480p": "480p — $0.08 / second", "720p": "720p — $0.14 / second",
                "1080p": "1080p — $0.25 / second",
            }, draft["resolution"])),
            _input("video", "role", "Use images as", _select("role", {
                "start": "Start frame", "reference": "Loose references",
            }, draft["role"]), dynamic=True),
        ])
    blocks.extend(_image_inputs(metadata, 7 if grok and draft["role"] == "reference" else 1))
    if grok:
        voices = [_option(voice, f"{voice.title()} ({gender})") for voice, gender in PRESET_VOICES.items()]
        element = {"type": "multi_static_select", "action_id": "voices", "options": voices, "max_selected_items": 3}
        selected = [option for option in voices if option["value"] in draft["voices"]]
        if selected:
            element["initial_options"] = selected
        blocks.extend([
            _input("video", "voices", "Voices", element, optional=True),
            _input("video", "custom_voices", "Custom voice IDs (comma separated)",
                   _text("custom_voices", draft["custom_voices"], max_length=100), optional=True),
            _note("Choose up to 3 voices total. Use <AUDIO_0>, <AUDIO_1>, and <AUDIO_2> "
                  "in the prompt to assign speakers. Loose references and voices cap resolution at 720p; "
                  "loose references also cap duration at 10 seconds."),
        ])
    return blocks


def _values(view):
    return {action: value for block in (view.get("state", {}).get("values") or {}).values()
            for action, value in block.items()}


def _capture(view):
    metadata = json.loads(view["private_metadata"])
    mode = metadata["mode"]
    draft = metadata["drafts"][mode]
    values = _values(view)
    for action, value in values.items():
        if action not in FIELDS:
            continue
        if "selected_options" in value:
            draft[action] = [option["value"] for option in value.get("selected_options") or []]
        elif "selected_option" in value:
            if value.get("selected_option"):
                draft[action] = value["selected_option"]["value"]
        else:
            draft[action] = value.get("value") or ""
    for kind in ("images", "videos"):
        retained = draft[kind]
        if f"keep_{kind}" in values:
            keep = {option["value"] for option in values[f"keep_{kind}"].get("selected_options") or []}
            retained = [file for file in retained if file["id"] in keep]
        files = {file["id"]: file for file in retained}
        for file in values.get(f"upload_{kind}", {}).get("files") or []:
            if isinstance(file, str):
                file = {"id": file}
            file_id = file["id"]
            files[file_id] = {"id": file_id, "name": (file.get("name") or file_id)[:75]}
        draft[kind] = list(files.values())
    return metadata, values.get("prompt", {}).get("value") or ""


def updated_view(view, actions):
    """Capture inputs before switching, retaining drafts without an external store."""
    metadata, prompt = _capture(view)
    for action in actions:
        if action["action_id"].startswith("mode_") and action.get("value") in BACKENDS:
            metadata["mode"] = action["value"]
    mode = metadata["mode"]
    draft = metadata["drafts"].setdefault(mode, _defaults(mode))
    _normalize(mode, draft)
    metadata["revision"] += 1
    try:
        return build_view(metadata, prompt)
    except ValueError as exc:
        # Keep the original form and Slack input IDs when metadata would exceed
        # Slack's limit. Never silently discard a draft or its uploaded files.
        result = copy.deepcopy(view)
        result = {key: result[key] for key in (
            "type", "callback_id", "title", "submit", "close", "private_metadata", "blocks",
        )}
        result["blocks"] = [block for block in result["blocks"] if block.get("block_id") != "draft_notice"]
        note = _note(str(exc))
        note["block_id"] = "draft_notice"
        result["blocks"].insert(1, note)
        # The visible state remains in Slack. Use original metadata until the
        # user shortens the inputs or submits the current form.
        return result


def _url(raw):
    value = raw.strip().removeprefix("<").removesuffix(">")
    value = value.split("|", 1)[0]
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or any(char.isspace() for char in value):
        raise ValueError("Enter an http or https URL with a hostname.")
    return value


def submission(view):
    """Validate before acknowledging submission; return a structured SNS job."""
    metadata, prompt = _capture(view)
    mode = metadata["mode"]
    draft = metadata["drafts"][mode]
    # Errors must target an input block that is currently visible.
    block_ids = {block["element"]["action_id"]: block["block_id"]
                 for block in view["blocks"] if block["type"] == "input"}
    errors = {}

    def error(action, message):
        errors[block_ids.get(action, "prompt_block")] = message

    if not prompt.strip():
        error("prompt", "Enter a prompt.")
    if time.time() - metadata["opened_at"] > MAX_AGE_SECONDS:
        error("prompt", "This form expired. Reopen /slop-bot so the result can be delivered.")
    backend = draft["backend"]
    if backend not in BACKENDS[mode]:
        error("backend", "Choose a supported provider.")
    request = {"mode": mode, "backend": backend, "prompt": prompt, "potato": bool(draft["potato"])}
    references = []
    source_video = None
    if mode == "text":
        output = draft["output"]
        search = draft["search"] if backend != "gemini" and output == "normal" else "off"
        if output not in ("normal", "emoji", "bufo") or search not in ("auto", "required", "off"):
            error("output", "Choose a supported response style and search mode.")
        request.update(output=output, search=search, potato=bool(draft["potato"]) and output != "bufo")
    else:
        references, source_video = _validate_media(mode, draft, request, error)
        if mode == "video":
            _validate_video(draft, request, error)
    if errors:
        return errors, None
    context = metadata["context"]
    return {}, {
        "source": "generation_modal", "generation": request,
        "prompt": prompt, "user": context["user_name"],
        **{key: context[key] for key in ("response_url", "channel_id", "channel_name")},
        "reference_images": references, "source_video": source_video,
    }


def _validate_media(mode, draft, request, error):
    operation = draft[f"{mode}_op"]
    if operation not in (("generate", "edit") if mode == "image" else ("generate", "edit", "extend")):
        error(f"{mode}_op", "Choose a supported operation.")
    if mode == "video" and draft["backend"] == "gemini" and operation != "generate":
        error("backend", "Video editing and extending require Grok.")
    if mode == "image" or operation == "generate":
        return _image_references(mode, draft, error), None
    source_video = None
    if draft["video_url"].strip():
        try:
            request["video_url"] = _url(draft["video_url"])
        except ValueError as exc:
            error("video_url", str(exc))
    count = len(draft["videos"]) + bool(draft["video_url"].strip())
    if count != 1:
        error("upload_videos", "Supply exactly one source video, using either a URL or an upload.")
    elif draft["videos"]:
        source_video = {"source": "slack_file", "value": draft["videos"][0]["id"], "role": operation}
    request["video_op"] = operation
    return [], source_video


def _image_references(mode, draft, error):
    role = "edit" if mode == "image" else draft["role"]
    if mode == "video" and (role not in ("start", "reference") or draft["backend"] == "gemini" and role != "start"):
        error("role", "Choose a supported image role; Gemini accepts a start frame only.")
    references = []
    for raw in draft["urls"].splitlines():
        if raw.strip():
            try:
                references.append({"source": "url", "value": _url(raw), "role": role})
            except ValueError as exc:
                error("urls", str(exc))
    references.extend({"source": "slack_file", "value": file["id"], "role": role} for file in draft["images"])
    limit = 3 if mode == "image" else 7 if role == "reference" else 1
    if len(references) > limit:
        error("upload_images", f"Use at most {limit} image(s) total, including URLs and retained uploads.")
    if mode == "image" and draft["image_op"] == "edit" and not references:
        error("upload_images", "Upload an image or enter an image URL to edit.")
    return references


def _validate_video(draft, request, error):
    grok = draft["backend"] == "grok"
    generate = draft["video_op"] == "generate"
    durations = (4, 6, 8) if not grok else range(1, 11 if generate and draft["role"] == "reference" else 16)
    try:
        duration = int(draft["duration"])
    except ValueError:
        duration = 0
    if duration not in durations:
        error("duration", "Choose one of the supported durations.")
    request["duration"] = duration
    if grok and generate:
        resolution = draft["resolution"]
        if resolution not in ("480p", "720p", "1080p"):
            error("resolution", "Choose 480p, 720p, or 1080p.")
        request["resolution"] = resolution
        voices = draft["voices"] + [value.lower() for value in
                                    re.split(r"[,\s]+", draft["custom_voices"].strip()) if value]
        voices = list(dict.fromkeys(voices))
        if len(voices) > 3 or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", voice) for voice in voices):
            error("custom_voices", "Use at most 3 voice IDs total, "
                  "containing letters, digits, underscores, or hyphens.")
        request["voices"] = voices


def command_options(prompt, normalize_flag):
    """Read generation flags for prefills without treating prompt words as UI state."""
    # This mirrors the command grammar; each branch consumes a distinct flag.
    # pylint: disable=too-many-branches
    options = {"mode": "text", "prompt": ""}
    tokens = prompt.split()
    words, urls, voices = [], [], []
    index = 0
    value_flags = {"-b": "backend", "-r": "resolution", "--resolution": "resolution"}
    while index < len(tokens):
        token = tokens[index]
        flag = normalize_flag(token)
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if flag in ("--modal", "--upload"):
            pass
        elif flag == "-i":
            options["mode"] = "image"
        elif flag == "-v":
            options["mode"] = "video"
            if following.isdigit():
                options["duration"] = following
                index += 1
        elif flag in value_flags and following:
            options[value_flags[flag]] = following.lower()
            index += 1
        elif flag in ("--edit", "--ref", "--start"):
            if flag == "--edit":
                options.update(mode="image", image_op="edit")
            if flag in ("--ref", "--start"):
                options["role"] = "reference" if flag == "--ref" else "start"
            if following.startswith(("http://", "https://", "<http://", "<https://")):
                urls.append(following)
                index += 1
        elif flag in ("--edit-video", "--extend-video"):
            options.update(mode="video", video_op="edit" if flag == "--edit-video" else "extend")
            if following.startswith(("http://", "https://", "<http://", "<https://")):
                options["video_url"] = following
                index += 1
        elif flag == "--voice" and following:
            voices.append(following.lower())
            index += 1
        elif flag in ("-s", "--search", "-t", "--no-search"):
            options["search"] = "required" if flag in ("-s", "--search") else "off"
        elif flag in ("-e", "-bufo", "--bufo"):
            options["output"] = "emoji" if flag == "-e" else "bufo"
        elif flag == "-p":
            options["potato"] = ["on"]
        else:
            words.append(token)
        index += 1
    options.update(prompt=" ".join(words), urls="\n".join(urls),
                   voices=[voice for voice in voices if voice in PRESET_VOICES],
                   custom_voices=", ".join(voice for voice in voices if voice not in PRESET_VOICES))
    if options.get("resolution", "") in ("480", "720", "1080"):
        options["resolution"] += "p"
    return options
