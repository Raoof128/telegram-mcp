"""The Meta webhook ingress (comms v0.3 Task C28; A36, design §C.5).

A raw ASGI app with exactly two routes, ``GET /webhooks/meta`` (the verify-token handshake,
which never delivers anything) and ``POST /webhooks/meta``. A POST is refused, in this order,
by a token-bucket rate bound (429), a content type other than JSON (415), a body over
``MAX_BODY_BYTES`` whether declared or streamed (413), a read slower than the deadline (408),
and an ``X-Hub-Signature-256`` that is not the HMAC-SHA256 of the exact raw bytes under the app
secret, compared in constant time (401). Only then are the raw bytes handed to ``accept`` (the
durable inbox, C29), and 200 is sent only after ``accept`` returns; if it fails the answer is
503 so Meta retries. The ingress never parses the body.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs

__all__ = ["MAX_BODY_BYTES", "WebhookIngress"]

PATH = "/webhooks/meta"
MAX_BODY_BYTES = 256 * 1024
READ_DEADLINE_S = 5.0

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class _Refused(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class WebhookIngress:
    def __init__(
        self,
        *,
        app_secret: bytes,
        verify_token: str,
        accept: Callable[[bytes], Awaitable[None]],
        clock: Callable[[], float],
        rate_capacity: int = 60,
        rate_per_second: float = 20.0,
        read_deadline_s: float = READ_DEADLINE_S,
        on_confirmed: Callable[[str], None] | None = None,
    ) -> None:
        if not app_secret or not verify_token:
            raise ValueError("the webhook secrets are required")
        self._secret, self._verify_token, self._accept = app_secret, verify_token, accept
        self._clock, self._deadline = clock, read_deadline_s
        self._capacity, self._rate = float(rate_capacity), rate_per_second
        self._tokens, self._refilled = float(rate_capacity), clock()
        # R-E6 (D39-PRE E10b): each secret is confirmed in operation, never by a whoami call
        self._on_confirmed = on_confirmed

    def __repr__(self) -> str:
        return "WebhookIngress(<redacted>)"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if scope["path"] != PATH:
            await _respond(send, 404)
            return
        method = scope["method"]
        try:
            if method == "GET":
                body = self._handshake(scope)
                await _respond(send, 200, body, content_type=b"text/plain")
            elif method == "POST":
                await self._post(scope, receive)
                await _respond(send, 200)
            else:
                await _respond(send, 405)
        except _Refused as refused:
            await _respond(send, refused.status)

    def _handshake(self, scope: Scope) -> bytes:
        if _header(scope, b"x-hub-signature-256") is not None:
            raise _Refused(400)  # a signed GET is not a handshake
        query = parse_qs(scope.get("query_string", b"").decode("ascii", "replace"))
        mode, token, challenge = (
            query.get(k, [None])[0] for k in ("hub.mode", "hub.verify_token", "hub.challenge")
        )
        if mode != "subscribe" or token is None or not challenge or not challenge.isascii():
            raise _Refused(400)
        if not hmac.compare_digest(token.encode(), self._verify_token.encode()):
            raise _Refused(403)
        self._confirmed("meta-webhook-secret")  # Meta's GET subscription challenge succeeded
        return challenge.encode()

    async def _post(self, scope: Scope, receive: Receive) -> None:
        if not self._take_token():
            raise _Refused(429)
        content_type = (_header(scope, b"content-type") or b"").split(b";")[0].strip().lower()
        if content_type != b"application/json":
            raise _Refused(415)
        declared = _header(scope, b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            raise _Refused(413)
        try:
            async with asyncio.timeout(self._deadline):
                raw = await _read(receive)
        except TimeoutError:
            raise _Refused(408) from None
        if not self._signed(raw, _header(scope, b"x-hub-signature-256")):
            raise _Refused(401)
        self._confirmed("meta-app-secret")  # its X-Hub-Signature-256 verified
        try:
            await self._accept(raw)
        except Exception:  # noqa: BLE001 -- not recorded, so not acknowledged: Meta retries
            raise _Refused(503) from None

    def _confirmed(self, purpose: str) -> None:
        if self._on_confirmed is not None:
            self._on_confirmed(purpose)

    def _signed(self, raw: bytes, header: bytes | None) -> bool:
        if header is None or not header.startswith(b"sha256="):
            return False
        expected = hmac.new(self._secret, raw, hashlib.sha256).hexdigest().encode()
        return hmac.compare_digest(header[len(b"sha256=") :], expected)

    def _take_token(self) -> bool:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._refilled) * self._rate)
        self._refilled = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


async def _read(receive: Receive) -> bytes:
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _Refused(400)
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _Refused(413)
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return bytes(value)
    return None


async def _respond(
    send: Send, status: int, body: bytes = b"", *, content_type: bytes = b"text/plain"
) -> None:
    headers = [(b"content-type", content_type), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
