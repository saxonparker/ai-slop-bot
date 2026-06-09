"""Reference image metadata, download, and normalization helpers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import logging
import os
import urllib.parse

from PIL import Image
import requests


ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_IMAGE_EDGE = 2048
LOGGER = logging.getLogger(__name__)


@dataclass
class ReferenceImage:
    """An unresolved image reference supplied by a user."""

    source: str
    value: str
    role: str = "reference"
    mime_type: str | None = None

    def to_payload(self) -> dict:
        """Serialize for SNS or Slack modal metadata."""
        payload = {"source": self.source, "value": self.value, "role": self.role}
        if self.mime_type:
            payload["mime_type"] = self.mime_type
        return payload

    @classmethod
    def from_payload(cls, payload: dict | "ReferenceImage") -> "ReferenceImage":
        """Deserialize from SNS payloads; tolerate already-built instances."""
        if isinstance(payload, ReferenceImage):
            return payload
        return cls(
            source=payload["source"],
            value=payload["value"],
            role=payload.get("role", "reference"),
            mime_type=payload.get("mime_type"),
        )


@dataclass
class ResolvedImage:
    """Image bytes normalized for provider calls."""

    data: bytes
    mime_type: str
    role: str = "reference"
    source: str = ""
    original_url: str | None = None
    file_id: str | None = None

    def data_uri(self) -> str:
        """Return a base64 data URI for providers that accept image URLs only."""
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"

    def provider_url(self) -> str:
        """Return a public URL when available, otherwise a data URI."""
        if self.original_url:
            return self.original_url
        return self.data_uri()


def parse_reference_url(raw: str) -> str:
    """Normalize a URL token from a slash command, including Slack's escaped form."""
    value = raw.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    if "|" in value:
        value = value.split("|", 1)[0]
    return value


def reference_from_url(raw: str, *, role: str = "reference") -> ReferenceImage:
    """Build a URL reference after basic scheme validation."""
    url = parse_reference_url(raw)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Reference images must use http or https URLs.")
    return ReferenceImage(source="url", value=url, role=role)


def resolve_reference_images(references: list[ReferenceImage]) -> list[ResolvedImage]:
    """Download and normalize a set of references."""
    return [resolve_reference_image(ref) for ref in references]


def resolve_reference_image(reference: ReferenceImage) -> ResolvedImage:
    """Download and normalize a single reference image."""
    reference = ReferenceImage.from_payload(reference)
    if reference.source == "url":
        data, mime_type = _download_url(reference.value)
        return _normalize_image(
            reference,
            data,
            mime_type or reference.mime_type,
            original_url=reference.value,
        )
    if reference.source == "slack_file":
        try:
            data, mime_type = _download_slack_file(reference.value, reference.mime_type)
            return _normalize_image(
                reference,
                data,
                mime_type,
                file_id=reference.value,
            )
        finally:
            _delete_slack_file(reference.value)
    raise ValueError(f"Unsupported reference image source: {reference.source}")


def _download_url(url: str) -> tuple[bytes, str | None]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content, _clean_content_type(response.headers.get("Content-Type"))


def _download_slack_file(file_id: str, fallback_mime_type: str | None) -> tuple[bytes, str | None]:
    token = os.environ["SLACK_BOT_TOKEN"]
    info_resp = requests.get(
        "https://slack.com/api/files.info",
        headers={"Authorization": f"Bearer {token}"},
        params={"file": file_id},
        timeout=30,
    )
    info_resp.raise_for_status()
    info = info_resp.json()
    if not info.get("ok"):
        raise RuntimeError(f"Slack files.info failed: {info.get('error')}")

    file_obj = info.get("file") or {}
    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    if not url:
        raise RuntimeError("Slack file is missing a private download URL.")
    mime_type = file_obj.get("mimetype") or fallback_mime_type

    file_resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    file_resp.raise_for_status()
    return file_resp.content, _clean_content_type(file_resp.headers.get("Content-Type")) or mime_type


def _delete_slack_file(file_id: str):
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        LOGGER.warning("Skipping Slack reference file cleanup; SLACK_BOT_TOKEN is unset")
        return

    try:
        response = requests.post(
            "https://slack.com/api/files.delete",
            headers={"Authorization": f"Bearer {token}"},
            data={"file": file_id},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            LOGGER.warning(
                "Slack reference file cleanup failed for %s: %s",
                file_id,
                payload.get("error"),
            )
    except (requests.RequestException, ValueError, AttributeError) as exc:
        LOGGER.warning("Slack reference file cleanup failed for %s: %s", file_id, exc)


def _normalize_image(reference: ReferenceImage, data: bytes, mime_type: str | None, *,
                     original_url: str | None = None, file_id: str | None = None) -> ResolvedImage:
    max_bytes = int(os.environ.get("REFERENCE_IMAGE_MAX_BYTES", str(DEFAULT_MAX_IMAGE_BYTES)))
    if len(data) > max_bytes:
        raise ValueError(f"Reference image is too large; max is {max_bytes // (1024 * 1024)} MB.")
    if mime_type and mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError(f"Unsupported reference image type: {mime_type}")

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ValueError("Reference image could not be decoded.") from exc

    max_edge = int(os.environ.get("REFERENCE_IMAGE_MAX_EDGE", str(DEFAULT_MAX_IMAGE_EDGE)))
    image.thumbnail((max_edge, max_edge))
    output = io.BytesIO()
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    if has_alpha:
        image.save(output, format="PNG", optimize=True)
        normalized_mime = "image/png"
    else:
        image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
        normalized_mime = "image/jpeg"

    return ResolvedImage(
        data=output.getvalue(),
        mime_type=normalized_mime,
        role=reference.role,
        source=reference.source,
        original_url=original_url,
        file_id=file_id,
    )


def _clean_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()
