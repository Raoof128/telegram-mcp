"""RV-1 consent rendezvous: HELLO / CHALLENGE / READY over `consent.sock`.

The handshake exists so the daemon and the consent agent authenticate each
other before any challenge is offered, and so starting MCP costs no
biometric prompt. The agent authenticates with its **transport** key — an
Ed25519 software key in the operator keychain — never with the Secure
Enclave approval key, which signs approvals only.

Wire (frozen with Plan 2b Task 3):

1. agent → ``HELLO {version: "rv-1", agent_key_id, agent_nonce,
   expected_runtime_id}``
2. daemon → ``CHALLENGE {version, runtime_id, agent_nonce, daemon_nonce,
   daemon_key_id, sig}`` where ``sig`` is Ed25519 over
   ``SHA256(b"telegram-mcp-rendezvous/v1" || JCS(transcript))`` under the
   daemon challenge key
3. agent → ``READY {agent_nonce, daemon_nonce, sig}`` signed with the agent
   transport key over the same transcript digest

Both sides hold a 5-second deadline for the whole handshake, and either
side closes on any violation. ``expected_runtime_id``, when the agent sends
one, must equal this runtime's id: an agent left over from a previous
runtime cannot be adopted.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.ipc.framing import (
    FrameError,
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "HANDSHAKE_DEADLINE_S",
    "RV_VERSION",
    "RendezvousError",
    "RendezvousSession",
    "build_challenge",
    "serve_rendezvous",
    "transcript_digest",
]

RV_VERSION = "rv-1"
HANDSHAKE_DEADLINE_S = 5.0
SOCKET_MODE = 0o660
SOCKET_DIR_MODE = 0o770

_RV_DOMAIN = b"telegram-mcp-rendezvous/v1"
_NONCE_BYTES = 16
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{22}\Z")
_KEY_ID_RE = re.compile(r"(ed25519|p256):[0-9a-f]{64}\Z")

_logger = logging.getLogger("telegram_mcp.rendezvous")


class RendezvousError(Exception):
    """Handshake refusal. Fixed reason; never carries key material."""


@dataclass(frozen=True)
class RendezvousSession:
    """A completed handshake: who connected and under which nonces."""

    agent_key_id: str
    agent_nonce: str
    daemon_nonce: str
    runtime_id: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes:
    if not isinstance(text, str) or re.fullmatch(r"[A-Za-z0-9_-]+", text) is None:
        raise RendezvousError("non-canonical base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error) as exc:
        raise RendezvousError("non-canonical base64url") from exc


def _runtime_hex(runtime_id: bytes | str) -> str:
    if isinstance(runtime_id, bytes):
        if len(runtime_id) != 16:
            raise RendezvousError("invalid runtime_id")
        return runtime_id.hex()
    if isinstance(runtime_id, str) and re.fullmatch(r"[0-9a-f]{32}", runtime_id):
        return runtime_id
    raise RendezvousError("invalid runtime_id")


def transcript_digest(
    *, runtime_id: str, agent_key_id: str, agent_nonce: str, daemon_nonce: str, daemon_key_id: str
) -> bytes:
    """``SHA256(domain || JCS(transcript))`` — what both sides sign."""
    payload = jcs_dumps(
        {
            "agent_key_id": agent_key_id,
            "agent_nonce": agent_nonce,
            "daemon_key_id": daemon_key_id,
            "daemon_nonce": daemon_nonce,
            "runtime_id": runtime_id,
            "version": RV_VERSION,
        }
    )
    return hashlib.sha256(_RV_DOMAIN + payload).digest()


def _check_hello(hello: dict[str, Any], runtime_hex: str) -> tuple[str, str]:
    if hello.get("type") != "HELLO":
        raise RendezvousError("expected HELLO")
    if set(hello) - {"type", "version", "agent_key_id", "agent_nonce", "expected_runtime_id"}:
        raise RendezvousError("unknown HELLO field")
    if hello.get("version") != RV_VERSION:
        raise RendezvousError("unsupported protocol version")
    key_id = hello.get("agent_key_id")
    if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
        raise RendezvousError("invalid agent_key_id")
    nonce = hello.get("agent_nonce")
    if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
        raise RendezvousError("invalid agent_nonce")
    if len(_unb64url(nonce)) != _NONCE_BYTES:
        raise RendezvousError("invalid agent_nonce")
    expected = hello.get("expected_runtime_id")
    if expected is not None and expected != runtime_hex:
        raise RendezvousError("runtime_id mismatch")
    return key_id, nonce


def build_challenge(
    *,
    challenge_key: bytes,
    runtime_id: bytes | str,
    daemon_key_id: str,
    agent_key_id: str,
    agent_nonce: str,
    daemon_nonce: str,
) -> dict[str, Any]:
    """Build the signed CHALLENGE frame body."""
    runtime_hex = _runtime_hex(runtime_id)
    digest = transcript_digest(
        runtime_id=runtime_hex,
        agent_key_id=agent_key_id,
        agent_nonce=agent_nonce,
        daemon_nonce=daemon_nonce,
        daemon_key_id=daemon_key_id,
    )
    signature = Ed25519PrivateKey.from_private_bytes(challenge_key).sign(digest)
    return {
        "type": "CHALLENGE",
        "version": RV_VERSION,
        "runtime_id": runtime_hex,
        "agent_nonce": agent_nonce,
        "daemon_nonce": daemon_nonce,
        "daemon_key_id": daemon_key_id,
        "sig": _b64url(signature),
    }


def _check_ready(
    ready: dict[str, Any],
    *,
    agent_transport_public: bytes,
    digest: bytes,
    agent_nonce: str,
    daemon_nonce: str,
) -> None:
    if ready.get("type") != "READY":
        raise RendezvousError("expected READY")
    if set(ready) - {"type", "agent_nonce", "daemon_nonce", "sig"}:
        raise RendezvousError("unknown READY field")
    if ready.get("agent_nonce") != agent_nonce or ready.get("daemon_nonce") != daemon_nonce:
        raise RendezvousError("nonce mismatch")
    sig = ready.get("sig")
    if not isinstance(sig, str):
        raise RendezvousError("invalid READY signature")
    try:
        Ed25519PublicKey.from_public_bytes(agent_transport_public).verify(_unb64url(sig), digest)
    except (InvalidSignature, ValueError) as exc:
        raise RendezvousError("agent transport signature failed") from exc


async def _handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    challenge_key: bytes,
    runtime_id: bytes | str,
    daemon_key_id: str,
    agent_transport_public: bytes,
) -> RendezvousSession:
    runtime_hex = _runtime_hex(runtime_id)
    hello = decode_json_frame(await read_frame(reader, idle_s=HANDSHAKE_DEADLINE_S))
    agent_key_id, agent_nonce = _check_hello(hello, runtime_hex)
    daemon_nonce = _b64url(secrets.token_bytes(_NONCE_BYTES))
    challenge = build_challenge(
        challenge_key=challenge_key,
        runtime_id=runtime_hex,
        daemon_key_id=daemon_key_id,
        agent_key_id=agent_key_id,
        agent_nonce=agent_nonce,
        daemon_nonce=daemon_nonce,
    )
    await write_frame(writer, encode_json_frame(challenge))
    digest = transcript_digest(
        runtime_id=runtime_hex,
        agent_key_id=agent_key_id,
        agent_nonce=agent_nonce,
        daemon_nonce=daemon_nonce,
        daemon_key_id=daemon_key_id,
    )
    ready = decode_json_frame(await read_frame(reader, idle_s=HANDSHAKE_DEADLINE_S))
    _check_ready(
        ready,
        agent_transport_public=agent_transport_public,
        digest=digest,
        agent_nonce=agent_nonce,
        daemon_nonce=daemon_nonce,
    )
    return RendezvousSession(
        agent_key_id=agent_key_id,
        agent_nonce=agent_nonce,
        daemon_nonce=daemon_nonce,
        runtime_id=runtime_hex,
    )


async def serve_rendezvous(
    socket_path: str | Path,
    *,
    challenge_key: bytes,
    runtime_id: bytes | str,
    daemon_key_id: str,
    agent_transport_public: bytes,
    on_session: Callable[[RendezvousSession, asyncio.StreamReader, asyncio.StreamWriter], Any]
    | None = None,
) -> asyncio.Server:
    """Serve ``consent.sock``; run ``on_session`` after a good handshake."""
    target = Path(socket_path)
    parent = target.parent
    if not parent.exists():
        parent.mkdir(mode=SOCKET_DIR_MODE, parents=True)
        os.chmod(parent, SOCKET_DIR_MODE)
    if target.exists() or target.is_symlink():
        target.unlink()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            session = await asyncio.wait_for(
                _handshake(
                    reader,
                    writer,
                    challenge_key=challenge_key,
                    runtime_id=runtime_id,
                    daemon_key_id=daemon_key_id,
                    agent_transport_public=agent_transport_public,
                ),
                timeout=HANDSHAKE_DEADLINE_S,
            )
        except (RendezvousError, FrameError) as exc:
            _logger.warning("rendezvous refused", extra={"reason": str(exc)})
            writer.close()
            return
        except (TimeoutError, asyncio.CancelledError):
            _logger.warning("rendezvous handshake deadline exceeded")
            writer.close()
            return
        try:
            if on_session is not None:
                result = on_session(session, reader, writer)
                if asyncio.iscoroutine(result):
                    await result
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=str(target))
    os.chmod(target, SOCKET_MODE)
    if stat.S_IMODE(target.stat().st_mode) != SOCKET_MODE:  # pragma: no cover - defensive
        server.close()
        raise OSError("consent socket permissions could not be set")
    return server
