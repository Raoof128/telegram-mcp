"""The production wiring point (design §1; comms v0.3 A3).

Since v0.3 the daemon serves the admin socket only: the Telegram MCP ingress, the
coordinator, the read routes and the project/grant/scope/policy authority are retired
and live in ``legacy_composition`` for retained tests. The architecture test pins that only these two modules import a
concrete adapter, and the retired-surface test that no production entry reaches the
legacy one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from comms.runtime.adapters import build_adapters
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
from comms.transports.telegram.keys.store import load_key, set_store_dir
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession

__all__ = ["admin_handlers", "build_admin", "build_telegram"]


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def admin_handlers(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    telegram: Any,
    audit_writer: Any = None,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """The one assembly point for the production admin handler map (Phase-5 design §2).

    Comms v0.3 retired projects, grants, scope, the policy commands, the exposure
    budget and ``tgml1`` issuance (``RETIRED_ADMIN_COMMANDS``); the router refuses them
    before any handler.
    """
    sink = AuditSink(load_key("audit-chain-key"), anchor_path, _iso_now)
    checkpoint_key = load_key("audit-checkpoint-key")
    ensure_current_published(
        conn, purpose="audit_checkpoint", private_seed=checkpoint_key, now=_iso_now()
    )
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        **client_handlers(conn, key_dir=key_dir),
        **audit_handlers(conn, sink=sink, checkpoint_key=checkpoint_key),
        **inspect_handlers(conn, lineage=NoRestoreLineage()),
    }
    if telegram is not None:
        # D39-PRE E8a: with the comms writer, ``auth revoke-this-session`` is registered too
        handlers.update(auth_handlers(conn, telegram, writer=audit_writer))
    return handlers


def build_admin(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    telegram: Any = None,
    comms_handlers: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
    control_handlers: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
    audit_writer: Any = None,
) -> AdminRouter:
    """The daemon's admin socket router: the retained legacy handlers (v0.3 A3) plus, since
    D39-PRE E6, the comms runtime's ``tool call``, ``operator`` and ``hello``."""
    set_store_dir(key_dir)
    handlers = admin_handlers(
        conn, key_dir=key_dir, anchor_path=anchor_path, telegram=telegram, audit_writer=audit_writer
    )
    clash = set(handlers) & set(comms_handlers or {})
    if clash:
        raise ValueError("an admin command has two handlers")
    return AdminRouter(
        {**handlers, **(comms_handlers or {})}, control_handlers=dict(control_handlers or {})
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


def build_comms_adapters(conn: Any, secrets: Any, settings: Any, **kw: Any) -> Any:
    """comms v0.3 C33: the one wiring point for the adapter registry (comms.runtime.adapters).

    The daemon calls this once it holds comms.db and the secret store (Part D's comms
    runtime); ``telegram_session`` is the single ``TelethonSession`` built by
    ``build_telegram``, shared by every Telegram user-actor adapter.
    """
    return build_adapters(conn, secrets, settings, **kw)
