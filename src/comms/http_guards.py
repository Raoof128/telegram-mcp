"""HTTP guards shared by every comms HTTP listener (moved here from the Telegram transport in
comms v0.3 Task D28, so the Comms ``/mcp`` and the legacy ingress share them).

One copy each: the strict-JSON duplicate-key preflight, the no-store header,
the bearer gate and the rate limiter. The bearer gate runs *before* the body
is read (spec §8.2.1: authenticate before schema parsing), and every refusal
cause returns the same status and the same bytes, so a response never says
which check failed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import Any

from comms.core.strict_json import strict_json_loads

__all__ = [
    "RATE_LIMITED_BODY",
    "UNAUTHORIZED_BODY",
    "RateLimiter",
    "bearer_gate",
    "duplicate_key_preflight",
    "no_store",
]

PARSE_ERROR_BODY = b'{"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"},"id":null}'
UNAUTHORIZED_BODY = b'{"error":"unauthorized"}'
RATE_LIMITED_BODY = b'{"error":"rate_limited"}'

_HEADERS = [(b"content-type", b"application/json"), (b"cache-control", b"private, no-store")]


async def _refuse(
    send: Any, status: int, body: bytes, extra: Sequence[tuple[bytes, bytes]] = ()
) -> None:
    headers = [*_HEADERS, (b"content-length", str(len(body)).encode()), *extra]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def no_store(app: Any) -> Any:
    async def wrapper(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_with_headers)

    return wrapper


def _loopback(client: Any) -> bool:
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def bearer_gate(
    app: Any, *, authenticate: Callable[[str], Any | None], principal_var: ContextVar[Any]
) -> Any:
    async def middleware(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        principal = None
        if _loopback(scope.get("client")):
            header = dict(scope.get("headers", [])).get(b"authorization", b"")
            if header.startswith(b"Bearer ") and header.isascii():
                principal = authenticate(header[7:].decode("ascii"))
        if principal is None:
            await _refuse(send, 401, UNAUTHORIZED_BODY, [(b"www-authenticate", b"Bearer")])
            return
        token = principal_var.set(principal)
        try:
            await app(scope, receive, send)
        finally:
            principal_var.reset(token)

    return middleware


class RateLimiter:
    """Fixed one-minute windows per (client, tool), plus an owner ceiling."""

    def __init__(
        self,
        limits: Mapping[str, int],
        *,
        owner_factor: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = dict(limits)
        self._factor = owner_factor
        self._clock = clock
        self._window = -1
        self._counts: dict[tuple[str, str], int] = {}
        self._owner: dict[str, int] = {}

    def check(self, client_ref: str, tool: str) -> float | None:
        """None to admit; otherwise seconds until the window turns."""
        now = self._clock()
        window = int(now // 60)
        if window != self._window:
            self._window, self._counts, self._owner = window, {}, {}
        limit = self._limits.get(tool, 30)
        if (
            self._counts.get((client_ref, tool), 0) >= limit
            or self._owner.get(tool, 0) >= limit * self._factor
        ):
            return max(1.0, (window + 1) * 60 - now)
        self._counts[(client_ref, tool)] = self._counts.get((client_ref, tool), 0) + 1
        self._owner[tool] = self._owner.get(tool, 0) + 1
        return None


def duplicate_key_preflight(
    app: Any, max_bytes: int, *, admit: Callable[[Any], float | None] | None = None
) -> Any:
    """Bounded strict-JSON preflight; optional admission after decoding.

    The installed SDK accepts duplicate request keys (last-wins), so the
    already-bounded body is checked with the strict decoder before SDK
    dispatch. Valid bodies are replayed once without being persisted.
    """

    async def middleware(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            if len(body) > max_bytes + 1:
                break
        if len(body) > max_bytes + 1:
            replayed = False

            async def replay_large() -> Any:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            await app(scope, replay_large, send)
            return
        try:
            decoded = strict_json_loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            await _refuse(send, 400, PARSE_ERROR_BODY)
            return
        if admit is not None:
            retry = admit(decoded)
            if retry is not None:
                await _refuse(
                    send, 429, RATE_LIMITED_BODY, [(b"retry-after", str(math.ceil(retry)).encode())]
                )
                return
        replayed = False

        async def replay() -> Any:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await _run_until_disconnect(app(scope, replay, send), receive)

    return middleware


async def _run_until_disconnect(call: Any, receive: Any) -> None:
    """Run the app, cancelling it if the client disconnects first.

    A disconnected client's call must not keep running. The body is already
    buffered and replayed, so the real ``receive`` is free to watch for
    ``http.disconnect``. Cancelling the app runs the coordinator's
    cancellation path, which releases any reservation before retrieval or
    commit. In stateless JSON mode the SDK never does this itself.
    """
    task = asyncio.ensure_future(call)

    async def watch() -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                task.cancel()
                return

    watcher = asyncio.ensure_future(watch())
    try:
        await task
    except asyncio.CancelledError:
        if not watcher.done() or not task.cancelled():
            raise  # we were cancelled from outside, not by a disconnect
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
