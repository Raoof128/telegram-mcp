"""The production wiring point (design §1; comms v0.3 A3).

Since v0.3 the daemon serves the admin socket only: the Telegram MCP ingress, the
coordinator, the read routes and the project/grant/scope/policy authority are retired
and live in ``legacy_composition`` for retained tests. The architecture test pins that only these two modules import a
concrete adapter, and the retired-surface test that no production entry reaches the
legacy one.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from comms.transports.telegram.disclosure.keys import (
    ensure_current_published,
)
from comms.transports.telegram.disclosure.lineage import NoRestoreLineage
from comms.transports.telegram.ipc.admin import AdminRouter
from comms.transports.telegram.ipc.handlers._wrapper import AuditSink
from comms.transports.telegram.ipc.handlers.audit import audit_handlers
from comms.transports.telegram.ipc.handlers.auth import auth_handlers
from comms.transports.telegram.ipc.handlers.clients import client_handlers
from comms.transports.telegram.ipc.handlers.inspect import inspect_handlers
from comms.transports.telegram.ipc.handlers.leases import auth_headers_handler
from comms.transports.telegram.keys.store import load_key, read_lease_seed, set_store_dir
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession

__all__ = ["admin_handlers", "build_admin", "build_telegram"]


class _Seeds:
    """``verify_lease``'s seed lookup: read-only, never mints."""

    def __init__(self, key_dir: Path) -> None:
        self._dir = key_dir

    def get(self, client_ref: str) -> bytes | None:
        return read_lease_seed(self._dir, client_ref)


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def admin_handlers(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    telegram: Any,
    seeds: Callable[[str], bytes | None],
    runtime_id: bytes,
    clock: Callable[[], float],
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """The one assembly point for the production admin handler map (Phase-5 design §2).

    Comms v0.3 retired projects, grants, scope, the policy commands and the exposure
    budget (``RETIRED_ADMIN_COMMANDS``); the router refuses them before any handler.
    """
    sink = AuditSink(load_key("audit-chain-key"), anchor_path, _iso_now)
    checkpoint_key = load_key("audit-checkpoint-key")
    ensure_current_published(
        conn, purpose="audit_checkpoint", private_seed=checkpoint_key, now=_iso_now()
    )
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        **client_handlers(conn, key_dir=key_dir),
        "auth headers": auth_headers_handler(
            conn, seed_for=seeds, runtime_id=runtime_id, clock=clock
        ),
        **audit_handlers(conn, sink=sink, checkpoint_key=checkpoint_key),
        **inspect_handlers(conn, lineage=NoRestoreLineage()),
    }
    if telegram is not None:
        handlers.update(auth_handlers(conn, telegram))
    return handlers


def build_admin(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    runtime_id: bytes,
    telegram: Any = None,
    clock: Callable[[], float] = time.time,
) -> AdminRouter:
    """The daemon's production surface: the admin socket's router, nothing else (v0.3 A3)."""
    set_store_dir(key_dir)
    return AdminRouter(
        admin_handlers(
            conn,
            key_dir=key_dir,
            anchor_path=anchor_path,
            telegram=telegram,
            seeds=_Seeds(key_dir).get,
            runtime_id=runtime_id,
            clock=clock,
        )
    )


def build_telegram(
    *,
    api_id: int,
    session_dir: Path,
    test_dc: tuple[int, str, int] | None,
    api_hash: str,
    client_factory: Callable[..., Any] | None = None,
) -> TelethonSession:
    return TelethonSession(
        TelegramConfig(api_id, session_dir, test_dc),
        api_hash=api_hash,
        client_factory=client_factory,
    )
