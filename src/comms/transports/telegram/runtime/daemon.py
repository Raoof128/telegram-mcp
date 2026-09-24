"""``telegram-mcp daemon``: the one process that holds Telegram (spec §9.2, §9.4).

Order matters and is fail-closed:

1. Take the runtime lock before touching any secret or the database. A second
   daemon learns it is second without reading anything.
2. Open and migrate the database, and create the owner principal.
3. Require both consent-agent pins. An unpaired daemon cannot ask anyone
   anything, so it does not start.
4. Read ``api_hash`` from the login Keychain once. If it is missing, Telegram
   stays ``AUTH_REQUIRED`` and everything else still runs. An unreachable
   Telegram is a state (``TELEGRAM_UNAVAILABLE``), retried every 30 seconds,
   never a failed start: the operator keeps the admin socket.
5. Serve consent, admin and ingress, then wait.

Shutdown disconnects Telegram (never ``log_out``) and unlinks the sockets.
"""

from __future__ import annotations

import asyncio
import contextlib
import grp
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn

from comms.transports.telegram.consent.challenge import verify_agent_signature
from comms.transports.telegram.ipc.admin import serve_admin
from comms.transports.telegram.keys.keychain import KeychainError, read_api_hash
from comms.transports.telegram.keys.store import (
    fingerprint_for,
    get_store_dir,
    load_key,
    set_store_dir,
)
from comms.transports.telegram.runtime.composition import (
    build_runtime,
    build_telegram,
    serve_consent,
)
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
    port: int = 8766
    admin_group: str | None = None


def _pins() -> tuple[bytes, bytes]:
    root = get_store_dir()
    approval = root / "agent-approval-key.pin"
    transport = root / "agent-transport-key.pin"
    if not approval.exists() or not transport.exists():
        raise DaemonError("pair the consent agent first (telegram-mcp pair import ...)")
    return approval.read_bytes(), transport.read_bytes()


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
    admin_path, consent_path = runtime_dir / "admin.sock", runtime_dir / "consent.sock"
    runtime_id = secrets.token_bytes(16)
    try:
        lock = acquire_lock(
            runtime_dir / "runtime.lock",
            socket_paths=(admin_path, consent_path),
            runtime_id=runtime_id,
            mode="daemon",
        )
    except RuntimeActive:
        raise DaemonError("a daemon is already running for this runtime directory") from None
    session: Any = None
    keeper: asyncio.Task[None] | None = None
    closers: list[Any] = []
    server: uvicorn.Server | None = None
    conn = None
    try:
        set_store_dir(config.key_dir)
        approval_der, transport = _pins()
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
        services = build_runtime(
            conn,
            key_dir=Path(config.key_dir),
            anchor_path=state / "anchor" / "anchor.json",
            runtime_id=runtime_id,
            agent_verify=lambda sig, msg: verify_agent_signature(sig, msg, approval_der),
            pinned_key_id=fingerprint_for("agent-approval-key", approval_der),
            port=config.port,
            telegram=session,
        )
        closers.append(
            await serve_consent(services, consent_path, agent_transport_public=transport)
        )
        closers.append(
            await serve_admin(
                admin_path,
                services.admin_router,
                allow_uid=os.getuid() if admin_gid is None else None,
                allow_gids=() if admin_gid is None else (admin_gid,),
            )
        )
        if admin_gid is not None:
            os.chown(admin_path, -1, admin_gid)  # serve_admin already made it 0660
        server = uvicorn.Server(
            uvicorn.Config(
                services.ingress_app, host="127.0.0.1", port=config.port, log_level="warning"
            )
        )
        serving = asyncio.create_task(server.serve())
        if stop is None:
            await serving
        else:
            await stop.wait()
            server.should_exit = True
            await serving
    finally:
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
        for path in (admin_path, consent_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        lock.release()
