"""Bufo emoji catalog loading.

Emoji names come from the all-the-bufo repository, which is the source the
emoji are actually imported from:

    https://github.com/knobiknows/all-the-bufo

Its ``index.md`` lists every emoji image as a Markdown table row. The Slack
emoji name is the image file name with the extension removed, preserving the
file name's hyphens and underscores verbatim (e.g. ``bufo-party``,
``bigbufo_0_0``). A handful of files use characters Slack emoji names cannot
contain (apostrophes, ``+``, non-ASCII); those are skipped. A vendored snapshot
is used when the repository cannot be reached.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path


BUFO_CATALOG_URL = (
    "https://raw.githubusercontent.com/knobiknows/all-the-bufo/main/index.md"
)

_FALLBACK_PATH = Path(__file__).with_name("bufo_emojis.json")
_BUFO_EMOJI_NAMES: tuple[str, ...] | None = None

# Image file references in the catalog; capture the base name before the extension.
_IMAGE_FILE_RE = re.compile(
    r"([A-Za-z0-9][^\s|/\\]*?)\.(?:avif|gif|jpe?g|png|svg|webp)\b",
    re.IGNORECASE,
)
# Bare ``:name:`` tokens used when sanitizing model output.
_COLON_NAME_RE = re.compile(r":([a-zA-Z0-9][a-zA-Z0-9_-]*):")
# Trailing image extension (with optional retina ``@2x`` suffix) to strip.
_EXTENSION_RE = re.compile(
    r"(?:@[0-9]+x)?\.(?:avif|gif|jpe?g|png|svg|webp)$", re.IGNORECASE
)
# A valid Slack custom emoji name: lowercase letters/digits, underscores, hyphens.
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def get_bufo_emoji_names() -> list[str]:
    """Return the available Bufo emoji names as bare names without colons."""
    global _BUFO_EMOJI_NAMES  # pylint: disable=global-statement

    if _BUFO_EMOJI_NAMES is None:
        try:
            names = _fetch_bufo_emoji_names()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"BUFO CATALOG FETCH FAILED: {exc}; using vendored fallback")
            names = []

        if not names:
            print("BUFO CATALOG FETCH RETURNED NO NAMES; using vendored fallback")
            names = _load_fallback_bufo_emoji_names()

        _BUFO_EMOJI_NAMES = tuple(names)

    return list(_BUFO_EMOJI_NAMES)


def sanitize_bufo_output(text: str, names: set[str]) -> str:
    """Return only valid Bufo emoji tokens from model output.

    The supplied ``names`` set contains bare emoji names without colons.

    >>> sanitize_bufo_output("great :bufo-party: nope :foo:", {"bufo", "bufo-party"})
    ':bufo-party:'
    >>> sanitize_bufo_output(":bufo::bufo-sad:", {"bufo", "bufo-sad"})
    ':bufo: :bufo-sad:'
    >>> sanitize_bufo_output("plain prose", {"bufo"})
    ':bufo:'
    >>> sanitize_bufo_output(":foo:", {"bufo-sad"})
    ':bufo-sad:'
    """
    valid_tokens = [
        f":{match.group(1)}:"
        for match in _COLON_NAME_RE.finditer(text)
        if match.group(1) in names
    ]
    if valid_tokens:
        return " ".join(valid_tokens)

    if "bufo" in names:
        return ":bufo:"
    return f":{next(iter(names))}:" if names else ":bufo:"


def _fetch_bufo_emoji_names() -> list[str]:
    names = _parse_bufo_emoji_names(_read_url(BUFO_CATALOG_URL))
    if not names:
        raise ValueError("all-the-bufo index did not contain any emoji names")
    return names


def _read_url(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/plain,text/markdown,*/*;q=0.8",
            "User-Agent": "ai-slop-bot/1.0 (+https://github.com/knobiknows/all-the-bufo)",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def _parse_bufo_emoji_names(payload: bytes | str) -> list[str]:
    text = (
        payload.decode("utf-8", errors="replace")
        if isinstance(payload, bytes)
        else payload
    )
    candidates = [match.group(1) for match in _IMAGE_FILE_RE.finditer(text)]
    return _normalize_names(candidates)


def _load_fallback_bufo_emoji_names() -> list[str]:
    with _FALLBACK_PATH.open(encoding="utf-8") as fallback_file:
        fallback = json.load(fallback_file)

    if isinstance(fallback, dict):
        fallback = fallback.get("emoji_names", fallback.get("names", []))

    if not isinstance(fallback, list):
        raise ValueError("Bufo emoji fallback must be a list of names")

    names = _normalize_names(fallback)
    if not names:
        raise ValueError("Bufo emoji fallback is empty")
    return names


def _normalize_names(candidates: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        name = _normalize_name(candidate)
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    return names


def _normalize_name(candidate: str) -> str | None:
    name = str(candidate).strip().strip(":").lower()
    name = name.rsplit("/", maxsplit=1)[-1]
    name = _EXTENSION_RE.sub("", name)

    if not _VALID_NAME_RE.match(name):
        return None
    return name
