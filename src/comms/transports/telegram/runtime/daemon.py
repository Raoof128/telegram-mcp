"""``telegram-mcp daemon``: the one process that holds Telegram (spec §9.2, §9.4).

Order matters and is fail-closed:

1. Take the runtime lock before touching any secret or the database. A second
   daemon learns it is second without reading anything.
2. Open and migrate the database, and create the owner principal.
3. Read ``api_hash`` from the login Keychain once. If it is missing, Telegram
   stays ``AUTH_REQUIRED`` and everything else still runs. An unreachable
   Telegram is a state (``TELEGRAM_UNAVAILABLE``), retried every 30 seconds,
   never a failed start: the operator keeps the admin socket.
4. ``comms daemon`` (D39-PRE E6) then starts the comms side (``comms.runtime.serve``): its
   settings, ``comms.db``, startup recovery, the one composition root, the MCP and webhook
   listeners and the workers. A refusal there refuses the whole start.
5. Serve the admin socket (the retained legacy handlers plus the comms ``tool call``,
   ``operator`` and ``hello``), then wait. There is no consent socket (comms spec v0.2), and
   the Telegram MCP surface is retired (A3). With the comms side, SIGTERM and SIGINT stop the
   daemon cleanly.

Shutdown stops the comms side, disconnects Telegram (never ``log_out``) and unlinks the
sockets. A worker's fatal failure stops the daemon with a ``DaemonError`` after that cleanup.
"""

from __future__ import annotations

import asyncio
import contextlib
import grp
import logging
import os
import secrets
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from comms.transports.telegram.ipc.admin import serve_admin
from comms.transports.telegram.keys.keychain import KeychainError, read_api_hash
from comms.transports.telegram.keys.store import load_key, set_store_dir
from comms.transports.telegram.runtime.composition import build_admin, build_telegram
from comms.transports.telegram.runtime.lock import RuntimeActive, acquire_lock
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.identity import ensure_owner_principal
from comms.transports.telegram.storage.migrations import migrate

__all__ = ["DaemonConfig", "DaemonError", "run_daemon"]

_logger = logging.getLogger("telegram_mcp.daemon")
_RECONNECT_S = 30.0


class DaemonError(Exception):
    """The daemon refused to start; the message is fixed and names the fix."""


@dataclass(frozen=True)
class DaemonConfig:
    runtime_dir: Path
    state_dir: Path
    key_dir: Path
    api_id: int | None
    test_dc: tuple[int, str, int] | None = None
    admin_group: str | None = None
    comms: bool = False  # D39-PRE E6: ``comms daemon`` serves the comms side too
    adapters_factory: Any = None  # the injected seam: None is production (selftest-daemon only)


async def run_daemon(
    config: DaemonConfig,
    *,
    api_hash_reader: Callable[[], str] = read_api_hash,
    client_factory: Callable[..., Any] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    runtime_dir = Path(config.runtime_dir)
    admin_gid: int | None = None
    if config.admin_group is not None:
        # Production shape: the installer owns this directory; never create or chmod it.
        try:
            admin_gid = grp.getgrnam(config.admin_group).gr_gid
        except KeyError:
            raise DaemonError("the admin group does not exist (run the installer)") from None
        if not runtime_dir.is_dir():
            raise DaemonError("the runtime directory is missing (run the installer)")
    else:
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    admin_path = runtime_dir / "admin.sock"
    runtime_id = secrets.token_bytes(16)
    try:
        lock = acquire_lock(
            runtime_dir / "runtime.lock",
            socket_paths=(admin_path,),
            runtime_id=runtime_id,
            mode="daemon",
        )
    except RuntimeActive:
        raise DaemonError("a daemon is already running for this runtime directory") from None
    session: Any = None
    keeper: asyncio.Task[None] | None = None
    closers: list[Any] = []
    conn = None
    comms_server: Any = None
    stop_event = stop or asyncio.Event()
    try:
        set_store_dir(config.key_dir)
        state = Path(config.state_dir)
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        (state / "anchor").mkdir(mode=0o700, exist_ok=True)
        conn = open_db(state / "meta.db")
        migrate(conn)
        ensure_owner_principal(conn, privacy_key=load_key("privacy-key"))
        if config.api_id is not None:
            try:
                api_hash = api_hash_reader()
            except KeychainError:
                _logger.warning("api_hash unavailable: Telegram stays AUTH_REQUIRED")
            else:
                session = build_telegram(
                    api_id=config.api_id,
                    session_dir=state / "telegram",
                    test_dc=config.test_dc,
                    api_hash=api_hash,
                    client_factory=client_factory,
                )
                await session.start()
                if session.readiness() == "TELEGRAM_UNAVAILABLE":
                    _logger.warning("Telegram unreachable at start; retrying in the background")

                async def keep_connected(link: Any = session) -> None:
                    while True:
                        await asyncio.sleep(_RECONNECT_S)
                        if not link.connected and not link.logged_out:
                            await link.reconnect()

                keeper = asyncio.create_task(keep_connected())
        if config.comms:
            from comms.runtime.serve import CommsServer, CommsStartRefused

            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(signum, stop_event.set)
            comms_server = CommsServer(
                state_dir=state,
                lock=lock,
                stop_event=stop_event,
                adapters_factory=config.adapters_factory,
                legacy_conn=conn,
            )
            try:
                await comms_server.start()
            except CommsStartRefused as refused:
                raise DaemonError(str(refused)) from None
        router = build_admin(
            conn,
            key_dir=Path(config.key_dir),
            anchor_path=state / "anchor" / "anchor.json",
            telegram=session,
            comms_handlers=None if comms_server is None else comms_server.admin_handlers,
            control_handlers=None if comms_server is None else comms_server.control_handlers,
            audit_writer=None if comms_server is None else comms_server.writer,
        )
        closers.append(
            await serve_admin(
                admin_path,
                router,
                allow_uid=os.getuid() if admin_gid is None else None,
                allow_gids=() if admin_gid is None else (admin_gid,),
            )
        )
        if admin_gid is not None:
            os.chown(admin_path, -1, admin_gid)  # serve_admin already made it 0660
        await stop_event.wait()  # production runs until a signal or cancellation
    finally:
        if comms_server is not None:
            await comms_server.stop()
        if keeper is not None:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
        for closer in closers:
            closer.close()
            with contextlib.suppress(Exception):
                await closer.wait_closed()
        if session is not None:
            await session.stop()  # disconnect only
        if conn is not None:
            conn.close()
        with contextlib.suppress(FileNotFoundError):
            admin_path.unlink()
        lock.release()
    if comms_server is not None and comms_server.failed:
        raise DaemonError("a comms worker hit an integrity failure (run: comms doctor)")
