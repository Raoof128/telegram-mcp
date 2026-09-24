"""The host-pinned HTTP client for provider adapters (comms v0.3 Task C1, A26).

Every provider adapter reaches the network only through ``pinned_client``: one allowed
``https`` origin, checked on every request (scheme, host and port), and no redirect is ever
followed. Only the adapter network modules may import ``httpx`` (pinned by
``tests/security/test_egress.py``); the typed egress matrix (A44) builds on this.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

__all__ = ["EgressRefused", "pinned_client"]


class EgressRefused(Exception):
    """A request left its pinned origin. Fixed message; never the URL."""

    def __init__(self) -> None:
        super().__init__("egress refused: the request is not to the pinned origin")


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query:
        raise ValueError("the pinned origin must be https://host[:port]")
    return parts.scheme, parts.hostname, parts.port or 443


class _Pinned(httpx.BaseTransport):
    def __init__(self, origin: tuple[str, str, int], inner: httpx.BaseTransport) -> None:
        self._origin, self._inner = origin, inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if (url.scheme, url.host, url.port or 443) != self._origin:
            raise EgressRefused
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def pinned_client(
    allowed_origin: str, *, timeout: float, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """An ``httpx.Client`` bound to one origin; ``transport`` is the injected network seam."""
    origin = _origin(allowed_origin)
    return httpx.Client(
        transport=_Pinned(origin, transport or httpx.HTTPTransport(retries=0)),
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,  # no proxy or certificate settings from the environment
    )
