"""The only wiring point (design §1).

Nothing else constructs a coordinator, hands it a retrieval adapter, or gives
the dispatcher its ``disclose``. The architecture test pins that: no other
module imports a concrete adapter.
"""

from __future__ import annotations

import functools
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from comms.transports.telegram.authority.staging import StagingRegistry
from comms.transports.telegram.disclosure.budget import BudgetLedger
from comms.transports.telegram.disclosure.coordinator import DisclosureCoordinator
from comms.transports.telegram.disclosure.keys import (
    current_verification_key,
    ensure_current_published,
)
from comms.transports.telegram.disclosure.lineage import NoRestoreLineage
from comms.transports.telegram.disclosure.seams import CoordinatorAuthority
from comms.transports.telegram.http_guards import DEFAULT_LIMITS, RateLimiter
from comms.transports.telegram.ipc.admin import AdminRouter
from comms.transports.telegram.ipc.handlers._wrapper import AuditSink
from comms.transports.telegram.ipc.handlers.audit import audit_handlers
from comms.transports.telegram.ipc.handlers.auth import auth_handlers
from comms.transports.telegram.ipc.handlers.clients import CLIENT_COMMANDS, client_handlers
from comms.transports.telegram.ipc.handlers.inspect import inspect_handlers
from comms.transports.telegram.ipc.handlers.leases import auth_headers_handler
from comms.transports.telegram.ipc.handlers.policy import policy_handlers
from comms.transports.telegram.ipc.handlers.projects import (
    PROJECT_COMMANDS,
    member_commands,
    project_handlers,
)
from comms.transports.telegram.ipc.handlers.scope import scope_commands, scope_handlers
from comms.transports.telegram.ipc.leases import LeaseError, verify_lease
from comms.transports.telegram.keys.store import key_id, load_key, read_lease_seed, set_store_dir
from comms.transports.telegram.runtime.identity import PrincipalContext, resolve_principal
from comms.transports.telegram.runtime.ingress import create_ingress_app
from comms.transports.telegram.sensitive_dispatch import SensitiveDispatcher
from comms.transports.telegram.storage.authority_view import load_security
from comms.transports.telegram.storage.db import bind_cursor_store
from comms.transports.telegram.telegram.discovery import DiscoveryStore
from comms.transports.telegram.telegram.metadata import MetadataReadAdapter
from comms.transports.telegram.telegram.reads import TelegramReads
from comms.transports.telegram.telegram.service import RoutedRetrieval
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession

__all__ = [
    "RuntimeServices",
    "admin_handlers",
    "build_runtime",
    "build_telegram",
]


@dataclass
class RuntimeServices:
    ingress_app: Any
    admin_router: AdminRouter
    coordinator: DisclosureCoordinator
    runtime_id: bytes


class _Seeds:
    """``verify_lease``'s seed lookup: read-only, never mints."""

    def __init__(self, key_dir: Path) -> None:
        self._dir = key_dir

    def get(self, client_ref: str) -> bytes | None:
        return read_lease_seed(self._dir, client_ref)


def _disclosure_public(conn: sqlite3.Connection) -> tuple[str, str]:
    ident = ensure_current_published(
        conn,
        purpose="disclosure_proof",
        private_seed=load_key("disclosure-key"),
        now=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    current = current_verification_key(conn, "disclosure_proof")
    if current is None or current["key_id"] != ident:
        raise RuntimeError("disclosure key publication did not take effect")  # fail closed
    return ident, current["public_key_b64url"]


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
    """The one assembly point for the admin handler map (Phase-5 design §2)."""
    sink = AuditSink(load_key("audit-chain-key"), anchor_path, _iso_now)
    checkpoint_key = load_key("audit-checkpoint-key")
    ensure_current_published(
        conn, purpose="audit_checkpoint", private_seed=checkpoint_key, now=_iso_now()
    )
    discovery = DiscoveryStore()
    members = DiscoveryStore()  # one store: minted by `project members`, used by remove-peer
    simulatable: dict[str, Any] = {
        **PROJECT_COMMANDS,
        **CLIENT_COMMANDS,
        **member_commands(members),
    }
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        **project_handlers(conn, members=members),
        **client_handlers(conn, key_dir=key_dir),
        "auth headers": auth_headers_handler(
            conn, seed_for=seeds, runtime_id=runtime_id, clock=clock
        ),
        **audit_handlers(conn, sink=sink, checkpoint_key=checkpoint_key),
        **inspect_handlers(conn, lineage=NoRestoreLineage()),
    }
    if telegram is not None:
        handlers.update(auth_handlers(conn, telegram))
        handlers.update(scope_handlers(conn, telegram, discovery))
        simulatable.update(scope_commands(discovery))
    handlers.update(policy_handlers(conn, registry=StagingRegistry(), simulatable=simulatable))
    return handlers


def build_runtime(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    runtime_id: bytes,
    host: str = "127.0.0.1",
    port: int = 8766,
    limits: Mapping[str, int] | None = None,
    clock: Callable[[], float] = time.time,
    telegram: Any = None,
) -> RuntimeServices:
    set_store_dir(key_dir)
    privacy_key = load_key("privacy-key")
    authority = CoordinatorAuthority(
        conn,
        privacy_key=privacy_key,
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=runtime_id,
        clock=clock,
        telegram_gate=lambda: "AUTH_REQUIRED" if telegram is None else telegram.readiness(),
    )
    coordinator = DisclosureCoordinator(
        conn,
        chain_key=load_key("audit-chain-key"),
        disclosure_seed=load_key("disclosure-key"),
        disclosure_key_id=key_id("disclosure-key"),
        anchor_path=anchor_path,
        ledger=BudgetLedger(conn),
        authority=authority,
    )
    metadata = MetadataReadAdapter(mint_cursor=authority.mint_catalogue_cursor)
    routes: dict[str, Any] = {
        "telegram_list_projects": metadata.list_projects,
        "telegram_resolve_project": metadata.resolve_project,
    }
    if telegram is not None:
        reads = TelegramReads(
            telegram,
            conn,
            mint_cursor=authority.mint_project_cursor,
            mint_search_cursor=authority.mint_search_cursor,
        )
        routes.update(
            {
                "telegram_list_chats": reads.list_chats,
                "telegram_resolve_peer": reads.resolve_peer,
                "telegram_get_messages": reads.get_messages,
                "telegram_get_unread": reads.get_unread,
                "telegram_get_context": reads.get_context,
                "telegram_search_messages": reads.search_messages,
                "telegram_cross_project_search": reads.cross_project_search,
            }
        )
    routed = RoutedRetrieval(routes)
    dispatcher = SensitiveDispatcher(functools.partial(coordinator.disclose, adapter=routed))
    seeds = _Seeds(key_dir)

    def authenticate(token: str) -> PrincipalContext | None:
        epoch, _locked = load_security(conn)
        try:
            claims = verify_lease(
                token,
                seeds=seeds,  # type: ignore[arg-type]  # read-only lookup, see _Seeds
                epoch=epoch,
                now=int(clock()),
                runtime_id=runtime_id,
            )
        except LeaseError:
            return None
        return resolve_principal(conn, claims.client)

    app = create_ingress_app(
        host=host,
        port=port,
        authenticate=authenticate,
        dispatcher=dispatcher,
        limiter=RateLimiter(limits or DEFAULT_LIMITS),
        status_key=_disclosure_public(conn),
    )
    handlers = admin_handlers(
        conn,
        key_dir=key_dir,
        anchor_path=anchor_path,
        telegram=telegram,
        seeds=seeds.get,
        runtime_id=runtime_id,
        clock=clock,
    )
    return RuntimeServices(
        ingress_app=app,
        admin_router=AdminRouter(handlers),
        coordinator=coordinator,
        runtime_id=runtime_id,
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
