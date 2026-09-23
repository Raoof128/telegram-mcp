"""Daemon half of the frozen prompt-frame wire (design §2.4).

Frames (frozen with Plan 2b, documented in ``tests/agent/stub_broker.py``):

* daemon -> agent ``{"type": "PROMPT", "handle", "challenge", "sig", "display"}``
* agent -> daemon ``{"type": "APPROVAL", "handle", "envelope": {...}}``
* agent -> daemon ``{"type": "DENIAL", "handle", "reason"}``

One prompt is in flight per session; later requests queue inside their own
45-second windows. An answer whose handle is not the current prompt's (the
operator's late tap after a timeout) is discarded, never matched to the next
prompt. Agent loss mid-prompt is a denial, and detaches the session so the
next caller gets an immediate ``CONSENT_UNAVAILABLE`` rather than a 45-second
wait on a dead socket.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from typing import Any

from telegram_mcp.ipc.framing import (
    ERR_IDLE,
    FrameError,
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)

__all__ = [
    "PromptDenied",
    "PromptUnavailable",
    "Prompter",
    "parse_answer",
    "prompt_frame",
]

_ENVELOPE_KEYS = ("challenge_sha256", "key_id", "sig")


class PromptUnavailable(Exception):
    """No live agent session: CONSENT_UNAVAILABLE."""


class PromptDenied(Exception):
    """The agent denied, the operator did not answer, or the agent was lost."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def prompt_frame(
    *, handle: str, challenge: bytes, signature: str, display: Mapping[str, Any]
) -> dict[str, Any]:
    """The one builder of the PROMPT frame."""
    return {
        "type": "PROMPT",
        "handle": handle,
        "challenge": _b64url(challenge),
        "sig": signature,
        "display": dict(display),
    }


def parse_answer(frame: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
    """``(handle, envelope, denial_reason)``; anything else is a ValueError."""
    handle = frame.get("handle")
    if not isinstance(handle, str) or not handle.startswith("tgu_"):
        raise ValueError("answer carries no handle")
    kind = frame.get("type")
    if kind == "APPROVAL":
        envelope = frame.get("envelope")
        if not isinstance(envelope, dict) or not all(
            isinstance(envelope.get(key), str) for key in _ENVELOPE_KEYS
        ):
            raise ValueError("malformed approval envelope")
        return handle, {key: envelope[key] for key in _ENVELOPE_KEYS}, None
    if kind == "DENIAL":
        reason = frame.get("reason")
        return handle, None, reason[:64] if isinstance(reason, str) and reason else "denied"
    raise ValueError("unknown answer type")


class Prompter:
    """Holds the live RV-1 session and runs one prompt at a time over it."""

    def __init__(self) -> None:
        self._conn: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self._closed: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    async def attach(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one authenticated session until it ends (``on_session`` body)."""
        self._conn = (reader, writer)
        self._closed = asyncio.Event()
        try:
            await self._closed.wait()
        finally:
            self._conn = None

    def _drop(self) -> None:
        self._conn = None
        if self._closed is not None:
            self._closed.set()

    async def prompt(
        self,
        *,
        handle: str,
        challenge: bytes,
        signature: str,
        display: Mapping[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        if self._conn is None:
            raise PromptUnavailable
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            async with asyncio.timeout(timeout):
                await self._lock.acquire()
        except TimeoutError:
            raise PromptDenied("timeout") from None
        try:
            if self._conn is None:
                raise PromptUnavailable
            reader, writer = self._conn
            frame = prompt_frame(
                handle=handle, challenge=challenge, signature=signature, display=display
            )
            try:
                await write_frame(writer, encode_json_frame(frame))
            except (FrameError, OSError, ConnectionError):
                self._drop()
                raise PromptUnavailable from None
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise PromptDenied("timeout")
                try:
                    raw = await read_frame(reader, idle_s=remaining)
                except FrameError as exc:
                    if str(exc) == ERR_IDLE:
                        raise PromptDenied("timeout") from None
                    self._drop()
                    raise PromptDenied("agent-lost") from None
                except (OSError, ConnectionError):
                    self._drop()
                    raise PromptDenied("agent-lost") from None
                if not raw:
                    self._drop()
                    raise PromptDenied("agent-lost")
                try:
                    answer_handle, envelope, reason = parse_answer(decode_json_frame(raw))
                except (FrameError, ValueError):
                    self._drop()
                    raise PromptDenied("malformed") from None
                if answer_handle != handle:
                    continue  # a late answer to an earlier prompt: discard
                if envelope is None:
                    raise PromptDenied(reason or "denied")
                return envelope
        finally:
            self._lock.release()
