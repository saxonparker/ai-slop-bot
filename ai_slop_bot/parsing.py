"""Flag parsing and directive syntax for /ai-slop commands."""

import typing


class ParsedCommand(typing.NamedTuple):
    """Result of parsing an /ai-slop command string."""
    mode: str  # "text", "image", or "video"
    display_text: str
    prompt_text: str
    emoji_mode: bool
    potato_mode: bool
    backend_override: str | None
    usage: bool


def parse_command(input_str: str) -> ParsedCommand:
    """Parse flags (-i, -e, -b <name>) and [hidden directive] syntax from input.

    Flags can appear in any order. Remaining tokens form the prompt.
    """
    tokens = input_str.split()
    image_mode = False
    video_mode = False
    emoji_mode = False
    potato_mode = False
    usage_mode = False
    backend_override = None

    # Extract flags
    prompt_tokens = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-i":
            image_mode = True
        elif token == "-v":
            video_mode = True
        elif token == "-e":
            emoji_mode = True
        elif token == "-p":
            potato_mode = True
        elif token in ("-u", "--usage"):
            usage_mode = True
        elif token == "-b":
            if i + 1 < len(tokens):
                i += 1
                backend_override = tokens[i]
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
    return ParsedCommand(mode, display_text, prompt_text, emoji_mode, potato_mode, backend_override, usage_mode)
