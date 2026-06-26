"""Bufo emoji catalog loading.

Bufopedia is a Vite app. Its HTML shell points to a bundled JavaScript asset
that embeds the catalog as ``JSON.parse(`[{"id": ..., "fileName": ...}]`)``.
The site computes Slack emoji commands from ``fileName`` by stripping the
extension, lowercasing, and replacing non-alphanumeric runs with underscores.
This module mirrors that format, with generic filename/colon parsing as a
backup and a vendored fallback when the site cannot be reached or parsed.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BUFO_CATALOG_URL = "https://bufopedia.com/"

_FALLBACK_PATH = Path(__file__).with_name("bufo_emojis.json")
_BUFO_EMOJI_NAMES: tuple[str, ...] | None = None

_NAME_RE = re.compile(r"(?<![a-zA-Z0-9_-])([a-zA-Z0-9][a-zA-Z0-9_-]*)(?![a-zA-Z0-9_-])")
_COLON_NAME_RE = re.compile(r":([a-zA-Z0-9][a-zA-Z0-9_-]*):")
_FILENAME_NAME_RE = re.compile(
    r"(?:^|[/\"'=])"
    r"([a-zA-Z0-9][a-zA-Z0-9_-]*)"
    r"(?:@[0-9]+x)?\.(?:avif|gif|jpe?g|png|svg|webp)",
    re.IGNORECASE,
)
_SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+\.js)[\"']", re.IGNORECASE)
_JSON_PARSE_TEMPLATE_RE = re.compile(r"JSON\.parse\(`(.*?)`\)", re.DOTALL)
_EXTENSION_RE = re.compile(r"(?:@[0-9]+x)?\.(?:avif|gif|jpe?g|png|svg|webp)$", re.IGNORECASE)
_NON_EMOJI_NAMES = {"bufopedia"}


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
    payload = _read_url(BUFO_CATALOG_URL)
    names = _parse_bufo_emoji_names(payload)
    asset_names: list[str] = []
    for asset_url in _iter_script_asset_urls(payload):
        asset_names.extend(_parse_bufo_emoji_names(_read_url(asset_url)))
    names = _normalize_names(names + asset_names)

    if not names:
        raise ValueError("Bufopedia response did not contain any Bufo emoji names")
    return names


def _read_url(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "ai-slop-bot/1.0 (+https://bufopedia.com/)",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def _iter_script_asset_urls(payload: bytes | str) -> list[str]:
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    text = html.unescape(text)
    return [
        urllib.parse.urljoin(BUFO_CATALOG_URL, match.group(1))
        for match in _SCRIPT_SRC_RE.finditer(text)
    ]


def _parse_bufo_emoji_names(payload: bytes | str) -> list[str]:
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    text = html.unescape(urllib.parse.unquote(text))

    catalog_names = _parse_bufopedia_catalog_names(text)
    if catalog_names:
        return _normalize_names(catalog_names)

    candidates: list[str] = []
    candidates.extend(match.group(1) for match in _COLON_NAME_RE.finditer(text))
    candidates.extend(match.group(1) for match in _FILENAME_NAME_RE.finditer(text))

    for json_text in _iter_embedded_json(text):
        try:
            candidates.extend(_iter_json_string_values(json.loads(json_text)))
        except json.JSONDecodeError:
            continue

    return _normalize_names(candidates)


def _parse_bufopedia_catalog_names(text: str) -> list[str]:
    names: list[str] = []
    for json_text in _JSON_PARSE_TEMPLATE_RE.findall(text):
        try:
            value = json.loads(json_text)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("fileName"):
                names.append(_slack_name_from_filename(str(item["fileName"])))
    return names


def _slack_name_from_filename(filename: str) -> str:
    name = html.unescape(urllib.parse.unquote(filename)).rsplit("/", maxsplit=1)[-1]
    name = _EXTENSION_RE.sub("", name).lower()
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_")


def _iter_embedded_json(text: str) -> list[str]:
    return re.findall(
        r"<script\b[^>]*(?:type=[\"']application/json[\"']|id=[\"']__NEXT_DATA__[\"'])[^>]*>(.*?)</script>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _iter_json_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_iter_json_string_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_iter_json_string_values(item))
        return values
    return []


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
    name = html.unescape(urllib.parse.unquote(str(candidate))).strip().strip(":").lower()
    name = name.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    name = _EXTENSION_RE.sub("", name)

    match = _NAME_RE.fullmatch(name)
    if not match or "bufo" not in name or name in _NON_EMOJI_NAMES:
        return None
    return match.group(1)
