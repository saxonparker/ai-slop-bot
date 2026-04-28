"""Flag parsing and directive syntax for /ai-slop commands."""

from dataclasses import dataclass


@dataclass
class ParsedCommand:
    """Result of parsing an /ai-slop command string."""
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
    pay_amount: float | None = None
    credit_target: str | None = None
    credit_amount: float | None = None
    conversation: bool = False


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
    credit_target = None
    credit_amount = None
    conversation_mode = False

    # Extract flags
    prompt_tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower = token.lower()
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

    # Parse [hidden directive] syntax
    split_text = text.split("[")
    display_text = split_text[0].strip()
    if len(split_text) > 1:
        right_split = split_text[1].split("]")
        if len(right_split) > 1:
            display_text += right_split[1]

    prompt_text = text.replace("[", "").replace("]", "")

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
        pay_amount=pay_amount,
        credit_target=credit_target,
        credit_amount=credit_amount,
        conversation=conversation_mode,
    )
