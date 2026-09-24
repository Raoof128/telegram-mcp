"""Admin Unix socket: peer credentials, strict frames, §33 command routing.

Filesystem permissions grant *reachability* and peer credentials prove
*which OS identity connected*. Under comms spec v0.2 that is the whole of
admin authority: the owner's own account, on the owner's own machine. There
is no second factor, and a ``presence`` argument is refused as malformed so
nothing can pretend otherwise.

Frames are decoded strictly before dispatch, so a body like
``{"cmd": "lock", "cmd": "status"}`` never reaches a handler: duplicate keys
are a decode failure, not a last-wins merge.

The §33 command surface is routed in full. Commands Phase 2a does not
implement answer ``NOT_AVAILABLE_IN_PHASE`` — routed, honest, and clearly
not a success — while a name outside §33 answers ``UNKNOWN_COMMAND``.

Peer credentials come from ``getpeereid(2)`` on macOS, the frozen primitive
for this platform, and ``SO_PEERCRED`` on Linux so headless tests can run.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import os
import socket
import stat
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from comms.transports.telegram.ipc.framing import (
    IDLE_TIMEOUT_S,
    FrameError,
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "ADMIN_COMMANDS",
    "ADMIN_PEER",
    "CONTROL_REQUESTS",
    "AdminRouter",
    "PeerCredentials",
    "getpeereid",
    "peer_credentials",
    "serve_admin",
    "verify_peer",
]

_logger = logging.getLogger("telegram_mcp.admin")

# Fixed admin-plane codes. These are not MCP tool error codes.
MALFORMED_REQUEST = "MALFORMED_REQUEST"
UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
NOT_AVAILABLE_IN_PHASE = "NOT_AVAILABLE_IN_PHASE"
PERMISSION_DENIED = "PERMISSION_DENIED"
INTERNAL_ERROR = "INTERNAL_ERROR"

SOCKET_DIR_MODE = 0o770
SOCKET_MODE = 0o660

# Spec §33 operator CLI surface, verbatim minus the "telegram-mcp " prefix.
ADMIN_COMMANDS: tuple[str, ...] = (
    "auth login",
    "auth status",
    "auth logout-local",
    "client list",
    "client rotate",
    "client disable",
    "auth headers",
    "consent status",
    "consent approve",
    "tunnel rotate-binding",
    "auth revoke-this-session",
    "scope discover",
    "scope list",
    "scope allow",
    "scope deny",
    "scope remove",
    "scope mode",
    "project create",
    "project list",
    "project rename",
    "project enable",
    "project disable",
    "project members",
    "project add-peer",
    "project remove-peer",
    "project grant-client",
    "project set-egress",
    "project revoke-client",
    "project grant-cross-search",
    "project revoke-cross-search",
    "project instruction",
    "project overlap",
    "project drift",
    "policy explain",
    "policy simulate",
    "policy diff",
    "policy export",
    "policy import",
    "disclosure show",
    "disclosure verify",
    "disclosure key",
    "exposure status",
    "audit verify",
    "audit repair-anchor",
    "audit checkpoint",
    "lock",
    "unlock",
    "lock status",
    "release verify",
    "doctor",
    "serve",
)

# Bootstrap control requests, outside the §33 surface (design §2).
CONTROL_REQUESTS: tuple[str, ...] = ("stop",)


@dataclass(frozen=True)
class PeerCredentials:
    """The connecting process's effective UID and GID."""

    uid: int
    gid: int


# The authenticated admin peer for the request being dispatched (Phase-5
# design §2.3: staged changes are bound to it). Set by serve_admin around
# each request; None for in-process callers.
ADMIN_PEER: ContextVar[PeerCredentials | None] = ContextVar("admin_peer", default=None)


def getpeereid(fd: int) -> PeerCredentials:
    """``getpeereid(2)`` on macOS; ``SO_PEERCRED`` elsewhere."""
    if sys.platform == "darwin":
        libc_name = ctypes.util.find_library("c")
        if libc_name is None:  # pragma: no cover - macOS always has libc
            raise OSError("libc is unavailable")
        libc = ctypes.CDLL(libc_name, use_errno=True)
        uid = ctypes.c_uint32()
        gid = ctypes.c_uint32()
        if libc.getpeereid(ctypes.c_int(fd), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise OSError(ctypes.get_errno(), "getpeereid failed")
        return PeerCredentials(uid=int(uid.value), gid=int(gid.value))
    sock = socket.socket(fileno=os.dup(fd))  # takes ownership of the dup
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    finally:
        sock.close()
    _pid, uid, gid = (int.from_bytes(raw[i : i + 4], sys.byteorder) for i in (0, 4, 8))
    return PeerCredentials(uid=uid, gid=gid)


def peer_credentials(writer: asyncio.StreamWriter) -> PeerCredentials:
    """Credentials of the peer on an accepted connection."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        raise OSError("connection has no socket")
    return getpeereid(sock.fileno())


def verify_peer(
    uid: int, gid: int, *, allow_uid: int | None = None, allow_gids: tuple[int, ...] = ()
) -> bool:
    """Authenticate the OS identity. Default: only the running euid."""
    expected = os.geteuid() if allow_uid is None else allow_uid
    if uid == expected:
        return True
    return gid in allow_gids


class AdminRouter:
    """Routes the §33 surface; unimplemented commands answer honestly."""

    def __init__(
        self,
        handlers: Mapping[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
        *,
        control_handlers: Mapping[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    ) -> None:
        unknown = set(handlers or {}) - set(ADMIN_COMMANDS)
        if unknown:
            raise ValueError("handler for a command outside spec §33")
        bad_control = set(control_handlers or {}) - set(CONTROL_REQUESTS)
        if bad_control:
            raise ValueError("handler for an unknown control request")
        self._handlers = dict(handlers or {})
        self._control = dict(control_handlers or {})

    @staticmethod
    def _error(code: str, reason: str) -> dict[str, Any]:
        return {"ok": False, "code": code, "reason": reason}

    def _dispatch_control(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Bootstrap control channel: the runtime's own stop request.

        Design §2 keeps bootstrap control (start/stop/status) separate from
        the §33 admin surface. ``stop`` still travels over this socket so a
        running runtime can drain gracefully instead of being signalled, so
        it is a ``control`` request, never a ``cmd``.
        """
        name = request.get("control")
        if set(request) - {"control", "args"}:
            return self._error(MALFORMED_REQUEST, "unknown request field")
        args = request.get("args", {})
        if not isinstance(args, dict):
            return self._error(MALFORMED_REQUEST, "args must be an object")
        if not isinstance(name, str) or name not in CONTROL_REQUESTS:
            return self._error(UNKNOWN_COMMAND, "unknown control request")
        handler = self._control.get(name)
        if handler is None:
            return self._error(NOT_AVAILABLE_IN_PHASE, "control request is not wired")
        try:
            data = handler(args)
        except Exception:
            _logger.exception("admin control failed", extra={"control": name})
            return self._error(INTERNAL_ERROR, "handler failed")
        return {"ok": True, "data": data}

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Route one already strictly decoded request object."""
        if "control" in request:
            return self._dispatch_control(request)
        command = request.get("cmd")
        if not isinstance(command, str) or not command:
            return self._error(MALFORMED_REQUEST, "cmd must be a non-empty string")
        args = request.get("args", {})
        if not isinstance(args, dict):
            return self._error(MALFORMED_REQUEST, "args must be an object")
        if set(request) - {"cmd", "args"}:
            return self._error(MALFORMED_REQUEST, "unknown request field")
        if command not in ADMIN_COMMANDS:
            return self._error(UNKNOWN_COMMAND, "unknown command")
        if "presence" in args:
            # Retired by comms spec v0.2: authority is the peer, not a proof.
            return self._error(MALFORMED_REQUEST, "unknown argument")
        handler = self._handlers.get(command)
        if handler is None:
            return self._error(NOT_AVAILABLE_IN_PHASE, "command is not implemented in this phase")
        try:
            data = handler(args)
        except PermissionError:
            return self._error(PERMISSION_DENIED, "operation refused")
        except ValueError as exc:
            return self._error(MALFORMED_REQUEST, str(exc))
        except Exception:
            _logger.exception("admin handler failed", extra={"cmd": command})
            return self._error(INTERNAL_ERROR, "handler failed")
        return {"ok": True, "data": data}

    def has_handler(self, command: Any) -> bool:
        return isinstance(command, str) and command in self._handlers

    async def adispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """``dispatch``, awaiting handlers that are coroutines (network commands).

        A routed command with no handler in this phase is refused first, as
        ``dispatch`` would.
        """
        command = request.get("cmd")  # a decoded strict-JSON object (framing guarantees it)
        if isinstance(command, str) and command in ADMIN_COMMANDS and not self.has_handler(command):
            return self._error(NOT_AVAILABLE_IN_PHASE, "command is not implemented in this phase")
        response = self.dispatch(request)
        data = response.get("data") if response.get("ok") else None
        if not asyncio.iscoroutine(data):
            return response
        try:
            return {"ok": True, "data": await data}
        except PermissionError:
            return self._error(PERMISSION_DENIED, "operation refused")
        except ValueError as exc:
            return self._error(MALFORMED_REQUEST, str(exc))
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            _logger.exception("admin handler failed", extra={"cmd": request.get("cmd")})
            return self._error(INTERNAL_ERROR, "handler failed")


def _prepare_socket_path(socket_path: Path) -> None:
    parent = socket_path.parent
    if not parent.exists():
        parent.mkdir(mode=SOCKET_DIR_MODE, parents=True)
        os.chmod(parent, SOCKET_DIR_MODE)
    if socket_path.exists() or socket_path.is_symlink():
        socket_path.unlink()


async def serve_admin(
    socket_path: str | Path,
    router: AdminRouter,
    *,
    allow_uid: int | None = None,
    allow_gids: tuple[int, ...] = (),
    idle_s: float = IDLE_TIMEOUT_S,
) -> asyncio.Server:
    """Serve the admin socket; caller owns the returned server's lifetime."""
    target = Path(socket_path)
    _prepare_socket_path(target)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                creds = peer_credentials(writer)
            except OSError:
                await _refuse(writer, PERMISSION_DENIED, "peer credentials unavailable")
                return
            if not verify_peer(creds.uid, creds.gid, allow_uid=allow_uid, allow_gids=allow_gids):
                _logger.warning("admin peer refused", extra={"uid": creds.uid, "gid": creds.gid})
                await _refuse(writer, PERMISSION_DENIED, "peer is not authorized")
                return
            while True:
                try:
                    raw = await read_frame(reader, idle_s=idle_s)
                except FrameError as exc:
                    await _refuse(writer, MALFORMED_REQUEST, str(exc))
                    return
                if not raw:
                    return
                try:
                    request = decode_json_frame(raw)
                except FrameError as exc:
                    # Never log the payload bytes: only the fixed reason.
                    _logger.warning("admin frame refused", extra={"reason": str(exc)})
                    await _refuse(writer, MALFORMED_REQUEST, str(exc))
                    return
                peer_token = ADMIN_PEER.set(creds)
                try:
                    await write_frame(writer, encode_json_frame(await router.adispatch(request)))
                finally:
                    ADMIN_PEER.reset(peer_token)
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=str(target))
    os.chmod(target, SOCKET_MODE)
    if stat.S_IMODE(target.stat().st_mode) != SOCKET_MODE:  # pragma: no cover - defensive
        server.close()
        raise OSError("admin socket permissions could not be set")
    return server


async def _refuse(writer: asyncio.StreamWriter, code: str, reason: str) -> None:
    try:
        await write_frame(writer, encode_json_frame({"ok": False, "code": code, "reason": reason}))
    except (FrameError, OSError, ConnectionError):  # pragma: no cover - peer already gone
        return
