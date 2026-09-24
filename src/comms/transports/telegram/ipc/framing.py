"""Length-prefixed frame codec for the admin socket.

One wire, one implementation: ``uint32`` big-endian length followed by that many
payload bytes, 64 KiB maximum, strict UTF-8, strict JSON with duplicate keys
rejected, and an idle deadline on every read.

The length is checked *before* the payload is read, so an oversized frame is
refused without buffering it, and a frame that is not valid UTF-8 is refused
without its bytes reaching a log line.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from comms.core.strict_json import strict_json_loads  # single strict JSON decoder

__all__ = [
    "IDLE_TIMEOUT_S",
    "MAX_FRAME_BYTES",
    "FrameError",
    "decode_json_frame",
    "encode_json_frame",
    "read_frame",
    "write_frame",
]

MAX_FRAME_BYTES = 64 * 1024
IDLE_TIMEOUT_S = 30.0

ERR_OVERSIZED = "frame exceeds the 64 KiB maximum"
ERR_TRUNCATED = "frame ended early"
ERR_IDLE = "connection idle deadline exceeded"
ERR_ENCODING = "frame is not valid UTF-8"
ERR_JSON = "frame is not a strict JSON object"


class FrameError(Exception):
    """Framing or decoding refusal. Never carries payload bytes."""


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
    idle_s: float = IDLE_TIMEOUT_S,
) -> bytes:
    """Read one frame, or raise ``FrameError``; ``b""`` means clean EOF."""
    try:
        header = await asyncio.wait_for(reader.readexactly(4), timeout=idle_s)
    except TimeoutError as exc:
        raise FrameError(ERR_IDLE) from exc
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return b""
        raise FrameError(ERR_TRUNCATED) from exc
    length = int.from_bytes(header, "big")
    if length > max_bytes:
        raise FrameError(ERR_OVERSIZED)
    if length == 0:
        return b""
    try:
        return await asyncio.wait_for(reader.readexactly(length), timeout=idle_s)
    except TimeoutError as exc:
        raise FrameError(ERR_IDLE) from exc
    except asyncio.IncompleteReadError as exc:
        raise FrameError(ERR_TRUNCATED) from exc


async def write_frame(writer: asyncio.StreamWriter, payload: bytes) -> None:
    """Write one length-prefixed frame."""
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameError(ERR_OVERSIZED)
    writer.write(len(payload).to_bytes(4, "big") + payload)
    await writer.drain()


def decode_json_frame(raw: bytes) -> dict[str, Any]:
    """Strict UTF-8 + strict JSON object, duplicate keys rejected."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrameError(ERR_ENCODING) from exc
    try:
        decoded = strict_json_loads(text)
    except ValueError as exc:
        raise FrameError(ERR_JSON) from exc
    if not isinstance(decoded, dict):
        raise FrameError(ERR_JSON)
    return decoded


def encode_json_frame(obj: Any) -> bytes:
    """Canonical JSON bytes for one frame."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
