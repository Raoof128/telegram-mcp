"""WhatsApp media and the Meta-only bounded downloader (comms v0.3 Task C25; A26, O6).

Media moves by provider media id. Upload and delete are one Graph call each. Retrieval asks
Graph for the media's URL and fetches it with the one downloader: the URL must be ``https`` on
443 with no userinfo and a host under ``META_MEDIA_HOST_SUFFIXES`` (exact suffix, after IDNA);
a redirect is followed only to another such host, at most ``MAX_REDIRECTS`` times; the body is
streamed and cut off at ``MAX_MEDIA_BYTES``; the content type must be on the allowlist; the
whole fetch has ``DOWNLOAD_DEADLINE_S``. No caller-supplied URL is ever fetched, and media
bytes never reach a log or a ``repr``.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from comms.core.providers.protocols import ProviderResult
from comms.transports.net import EgressRefused, host_allowed, suffix_pinned_client
from comms.transports.whatsapp.cloud.classify import admin_call
from comms.transports.whatsapp.cloud.http import GraphApi

__all__ = [
    "MAX_MEDIA_BYTES",
    "META_MEDIA_HOST_SUFFIXES",
    "MediaBlob",
    "MediaOps",
    "MediaRefused",
]

META_MEDIA_HOST_SUFFIXES = ("fbsbx.com", "fbcdn.net", "whatsapp.net")
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MAX_REDIRECTS = 2
DOWNLOAD_DEADLINE_S = 30.0
MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/3gpp",
        "audio/aac",
        "audio/amr",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "application/pdf",
        "text/plain",
    }
)


class MediaRefused(Exception):
    """A media fetch the downloader will not make or finish. Fixed messages, never a URL."""


@dataclass(frozen=True)
class MediaBlob:
    data: bytes = field(repr=False)
    mime: str
    sha256: str


class MediaOps:
    def __init__(
        self, api: GraphApi, *, download_transport: httpx.BaseTransport | None = None
    ) -> None:
        self._api = api
        self._download = suffix_pinned_client(
            META_MEDIA_HOST_SUFFIXES, timeout=DOWNLOAD_DEADLINE_S, transport=download_transport
        )

    def __repr__(self) -> str:
        return "MediaOps(<redacted>)"

    def upload(self, data: bytes, mime: str) -> ProviderResult:
        """``media.upload`` (CREATE, resolve-only): the ref is the new media id."""
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_MEDIA_BYTES:
            raise ValueError("media size refused")
        if mime not in MEDIA_TYPES:
            raise ValueError("media type refused")
        result = admin_call(lambda: self._api.upload_media(data, mime))
        if result.outcome != "SUCCEEDED":
            return result
        media_id = result.detail.get("id")
        if not isinstance(media_id, str) or not media_id.isdigit():
            return ProviderResult("OUTCOME_UNKNOWN", None)
        return ProviderResult("SUCCEEDED", None, provider_ref=media_id)

    def info(self, media_id: str) -> dict[str, Any]:
        """``media.inspect``: type, size and digest — never the download URL."""
        if not isinstance(media_id, str) or not media_id.isascii() or not media_id.isdigit():
            raise ValueError("media id refused")
        info = self._api.media_info(media_id)
        envelope = info.envelope or {}
        if info.http_status != 200 or not isinstance(envelope.get("mime_type"), str):
            raise MediaRefused("media info unavailable")
        return {
            "mime_type": envelope["mime_type"],
            "file_size": envelope.get("file_size"),
            "sha256": envelope.get("sha256"),
        }

    def delete(self, media_id: str) -> ProviderResult:
        """``media.delete`` (destructive, resolve-only)."""
        return admin_call(lambda: self._api.delete_media(media_id))

    def retrieve(self, media_id: str) -> MediaBlob:
        if not isinstance(media_id, str) or not media_id.isascii() or not media_id.isdigit():
            raise ValueError("media id refused")  # a URL is never accepted from a caller
        info = self._api.media_info(media_id)
        url = (info.envelope or {}).get("url") if info.http_status == 200 else None
        if not isinstance(url, str):
            raise MediaRefused("media url unavailable")
        return self._fetch(url)

    def _fetch(self, url: str) -> MediaBlob:
        deadline = time.monotonic() + DOWNLOAD_DEADLINE_S
        for _hop in range(MAX_REDIRECTS + 1):
            if not host_allowed(url, META_MEDIA_HOST_SUFFIXES):
                raise MediaRefused("media host refused")
            try:
                with self._download.stream("GET", url, headers=self._api.bearer()) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    return _bounded(response, deadline)
            except EgressRefused:
                raise MediaRefused("media host refused") from None
            except httpx.HTTPError:
                raise MediaRefused("media download failed") from None
        raise MediaRefused("too many media redirects")

    def close(self) -> None:
        self._download.close()


def _bounded(response: httpx.Response, deadline: float) -> MediaBlob:
    if response.status_code != 200:
        raise MediaRefused("media download failed")
    mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime not in MEDIA_TYPES:
        raise MediaRefused("media type refused")
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_MEDIA_BYTES:
            raise MediaRefused("media too large")
        if time.monotonic() > deadline:
            raise MediaRefused("media download too slow")
        chunks.append(chunk)
    data = b"".join(chunks)
    return MediaBlob(data, mime, hashlib.sha256(data).hexdigest())
