"""The only wiring point (design §1).

Nothing else constructs a coordinator, hands it a retrieval adapter, or gives
the dispatcher its ``disclose``. The architecture test pins that: no other
module imports a concrete adapter.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telegram_mcp.consent.admin_approval import AdminApprover
from telegram_mcp.consent.broker import ConsentBroker
from telegram_mcp.consent.prompter import Prompter
from telegram_mcp.disclosure.budget import BudgetLedger
from telegram_mcp.disclosure.coordinator import DisclosureCoordinator
from telegram_mcp.disclosure.keys import current_verification_key, publish_verification_key
from telegram_mcp.disclosure.seams import CoordinatorAuthority, CoordinatorConsent
from telegram_mcp.http_guards import DEFAULT_LIMITS, RateLimiter
from telegram_mcp.ipc.admin import AdminRouter
from telegram_mcp.ipc.handlers.auth import auth_handlers
from telegram_mcp.ipc.handlers.clients import client_handlers
from telegram_mcp.ipc.handlers.leases import auth_headers_handler
from telegram_mcp.ipc.handlers.projects import project_handlers
from telegram_mcp.ipc.handlers.scope import scope_handlers
from telegram_mcp.ipc.leases import LeaseError, verify_lease
from telegram_mcp.ipc.rendezvous import serve_rendezvous
from telegram_mcp.keys.store import key_id, load_key, read_lease_seed, set_store_dir
from telegram_mcp.runtime.identity import PrincipalContext, resolve_principal
from telegram_mcp.runtime.ingress import create_ingress_app
from telegram_mcp.sensitive_dispatch import SensitiveDispatcher
from telegram_mcp.storage.authority_view import load_security
from telegram_mcp.storage.db import bind_cursor_store
from telegram_mcp.telegram.discovery import DiscoveryStore
from telegram_mcp.telegram.metadata import MetadataReadAdapter
from telegram_mcp.telegram.reads import TelegramReads
from telegram_mcp.telegram.service import RoutedRetrieval
from telegram_mcp.telegram.telethon_adapter import TelegramConfig, TelethonSession

__all__ = ["RuntimeServices", "build_runtime", "build_telegram", "serve_consent"]


@dataclass
class RuntimeServices:
    ingress_app: Any
    admin_router: AdminRouter
    coordinator: DisclosureCoordinator
    prompter: Prompter
    broker: ConsentBroker
    runtime_id: bytes
    approver: AdminApprover


class _Seeds:
    """``verify_lease``'s seed lookup: read-only, never mints."""

    def __init__(self, key_dir: Path) -> None:
        self._dir = key_dir

    def get(self, client_ref: str) -> bytes | None:
        return read_lease_seed(self._dir, client_ref)


def _disclosure_public(conn: sqlite3.Connection) -> tuple[str, str]:
    """Publish the disclosure key's public half once; return it for status."""
    raw = (
        Ed25519PrivateKey.from_private_bytes(load_key("disclosure-key"))
        .public_key()
        .public_bytes_raw()
    )
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    ident = key_id("disclosure-key")
    current = current_verification_key(conn, "disclosure_proof")
    if current is None or current["key_id"] != ident:
        publish_verification_key(
            conn,
            key_id=ident,
            purpose="disclosure_proof",
            algorithm="Ed25519",
            public_key_b64url=public,
            activated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    return ident, public


def build_runtime(
    conn: sqlite3.Connection,
    *,
    key_dir: Path,
    anchor_path: Path,
    runtime_id: bytes,
    agent_verify: Callable[[bytes, bytes], bool],
    pinned_key_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    presence_verifier: Callable[[Any], bool] | None = None,
    limits: Mapping[str, int] | None = None,
    clock: Callable[[], float] = time.time,
    telegram: Any = None,
) -> RuntimeServices:
    set_store_dir(key_dir)
    privacy_key = load_key("privacy-key")
    broker = ConsentBroker(
        challenge_key=load_key("challenge-key"),
        agent_verify=agent_verify,
        runtime_id=runtime_id,
        pinned_key_id=pinned_key_id,
    )
    prompter = Prompter()
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
        consent=CoordinatorConsent(broker, prompter),
    )
    metadata = MetadataReadAdapter(mint_cursor=authority.mint_catalogue_cursor)
    routes: dict[str, Any] = {
        "telegram_list_projects": metadata.list_projects,
        "telegram_resolve_project": metadata.resolve_project,
    }
    if telegram is not None:
        reads = TelegramReads(telegram, conn, mint_cursor=authority.mint_project_cursor)
        routes.update(
            {
                "telegram_list_chats": reads.list_chats,
                "telegram_resolve_peer": reads.resolve_peer,
                "telegram_get_messages": reads.get_messages,
                "telegram_get_unread": reads.get_unread,
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
    approver = AdminApprover(broker, prompter, privacy_key=privacy_key, conn=conn)
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        **project_handlers(conn),
        **client_handlers(conn, key_dir=key_dir),
        "auth headers": auth_headers_handler(
            conn, seed_for=seeds.get, runtime_id=runtime_id, clock=clock
        ),
    }
    if telegram is not None:
        handlers.update(auth_handlers(conn, telegram))
        handlers.update(scope_handlers(conn, telegram, DiscoveryStore()))
    return RuntimeServices(
        ingress_app=app,
        admin_router=AdminRouter(
            handlers,
            presence_verifier=presence_verifier,
            request_verifier=None if presence_verifier is not None else approver.verify,
        ),
        coordinator=coordinator,
        prompter=prompter,
        broker=broker,
        runtime_id=runtime_id,
        approver=approver,
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


async def serve_consent(
    services: RuntimeServices, socket_path: Path, *, agent_transport_public: bytes
) -> asyncio.Server:
    """The live RV-1 socket; each authenticated session feeds the prompter."""

    async def on_session(
        session: Any, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await services.prompter.attach(reader, writer)

    return await serve_rendezvous(
        socket_path,
        challenge_key=load_key("challenge-key"),
        runtime_id=services.runtime_id,
        daemon_key_id=key_id("challenge-key"),
        agent_transport_public=agent_transport_public,
        on_session=on_session,
    )
