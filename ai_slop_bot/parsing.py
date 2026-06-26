"""Flag parsing and directive syntax for /slop-bot commands."""

import urllib.parse
from dataclasses import dataclass, field

import media_refs


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


@dataclass
class ParsedCommand:
    """Result of parsing a /slop-bot command string."""
    mode: str  # "text", "image", or "video"
    display_text: str = ""
    prompt_text: str = ""
    emoji_mode: bool = False
    potato_mode: bool = False
    backend_override: str | None = None
    usage: bool = False
    report: bool = False
    gallery: bool = False
    video_duration: int | None = None
    video_op: str | None = None
    video_source_url: str | None = None
    pay_amount: float | None = None
    credit_target: str | None = None
    credit_amount: float | None = None
    conversation: bool = False
    upload_requested: bool = False
    source_image: media_refs.ReferenceImage | None = None
    reference_images: list[media_refs.ReferenceImage] = field(default_factory=list)


def parse_command(input_str: str) -> ParsedCommand:
    """Parse flags (-i, -e, -b <name>) and [hidden directive] syntax from input.

    Flags can appear in any order. Remaining tokens form the prompt.
    """
    tokens = input_str.split()
    image_mode = False
    video_mode = False
    video_duration = None
    emoji_mode = False
    potato_mode = False
    usage_mode = False
    report_mode = False
    gallery_mode = False
    backend_override = None
    pay_amount = None
    video_op = None
    video_source_url = None
    credit_target = None
    credit_amount = None
    conversation_mode = False
    upload_requested = False
    source_image = None
    reference_images = []

    # Extract flags
    prompt_tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower = _normalize_flag_token(token)
        if lower == "-i":
            image_mode = True
        elif lower == "-v":
            video_mode = True
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i += 1
                video_duration = int(tokens[i])
        elif lower == "-e":
            emoji_mode = True
        elif lower == "-p":
            potato_mode = True
        elif lower in ("-u", "--usage"):
            usage_mode = True
        elif lower in ("-report", "--report"):
            report_mode = True
        elif lower in ("-g", "--gallery"):
            gallery_mode = True
        elif lower == "-b":
            if i + 1 < len(tokens):
                i += 1
                backend_override = tokens[i].lower()
        elif lower in ("-pay", "--pay"):
            if i + 1 < len(tokens):
                i += 1
                try:
                    pay_amount = float(tokens[i])
                except ValueError:
                    prompt_tokens.append(token)
                    prompt_tokens.append(tokens[i])
        elif lower in ("-c", "--conversation"):
            conversation_mode = True
        elif lower == "--upload":
            upload_requested = True
        elif lower in ("--edit-video", "--extend-video"):
            if i + 1 < len(tokens):
                i += 1
                url = media_refs.parse_reference_url(tokens[i])
                parsed_url = urllib.parse.urlparse(url)
                if parsed_url.scheme in ("http", "https") and parsed_url.netloc:
                    video_mode = True
                    video_op = "edit" if lower == "--edit-video" else "extend"
                    video_source_url = url
                else:
                    prompt_tokens.append(token)
                    prompt_tokens.append(tokens[i])
            else:
                prompt_tokens.append(token)
        elif lower in ("--edit", "--ref", "--start"):
            if i + 1 < len(tokens):
                i += 1
                role = {
                    "--edit": "edit",
                    "--ref": "reference",
                    "--start": "start",
                }[lower]
                try:
                    reference = media_refs.reference_from_url(tokens[i], role=role)
                except ValueError:
                    prompt_tokens.append(token)
                    prompt_tokens.append(tokens[i])
                else:
                    if role == "start":
                        source_image = reference
                    else:
                        reference_images.append(reference)
            else:
                prompt_tokens.append(token)
        elif lower in ("-credit", "--credit"):
            if i + 2 < len(tokens):
                i += 1
                credit_target = tokens[i]
                i += 1
                try:
                    credit_amount = float(tokens[i])
                except ValueError:
                    prompt_tokens.append(token)
                    prompt_tokens.append(credit_target)
                    prompt_tokens.append(tokens[i])
                    credit_target = None
        else:
            prompt_tokens.append(token)
        i += 1

    text = " ".join(prompt_tokens)

    if emoji_mode:
        text += " [Respond only with emojis. No text.]"

    # Parse [hidden] (sent to LLM, stripped from channel display) and
    # ]shown[ (shown in channel, stripped from LLM input) via a single
    # left-to-right walk so the closing ] of a [hidden] pair isn't
    # mistaken for the opening of a ]shown[ pair.
    segments: list[tuple[str, str]] = []
    state = "neutral"
    buf = ""
    for ch in text:
        if state == "neutral":
            if ch == "[":
                if buf:
                    segments.append(("normal", buf))
                    buf = ""
                state = "hidden"
            elif ch == "]":
                if buf:
                    segments.append(("normal", buf))
                    buf = ""
                state = "shown"
            else:
                buf += ch
        elif state == "hidden":
            if ch == "]":
                segments.append(("hidden", buf))
                buf = ""
                state = "neutral"
            else:
                buf += ch
        else:  # state == "shown"
            if ch == "[":
                segments.append(("shown", buf))
                buf = ""
                state = "neutral"
            else:
                buf += ch

    if state == "hidden":
        # Lone [ with no closing ]: drop from display, keep in prompt.
        segments.append(("hidden", buf))
    elif state == "shown":
        # Lone ] with no closing [: revert. The ] survives in display
        # as a literal and is stripped from prompt by the final replace.
        segments.append(("normal", "]" + buf))
    elif buf:
        segments.append(("normal", buf))

    display_text = "".join(c for t, c in segments if t in ("normal", "shown"))
    prompt_text = "".join(c for t, c in segments if t in ("normal", "hidden"))
    prompt_text = prompt_text.replace("[", "").replace("]", "")

    display_text = " ".join(display_text.split())
    prompt_text = " ".join(prompt_text.split())

    mode = "video" if video_mode else "image" if image_mode else "text"
    return ParsedCommand(
        mode=mode,
        display_text=display_text,
        prompt_text=prompt_text,
        emoji_mode=emoji_mode,
        potato_mode=potato_mode,
        backend_override=backend_override,
        usage=usage_mode,
        report=report_mode,
        gallery=gallery_mode,
        video_duration=video_duration,
        video_op=video_op,
        video_source_url=video_source_url,
        pay_amount=pay_amount,
        credit_target=credit_target,
        credit_amount=credit_amount,
        conversation=conversation_mode,
        upload_requested=upload_requested,
        source_image=source_image,
        reference_images=reference_images,
    )
