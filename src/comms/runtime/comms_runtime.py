"""The Comms runtime composition (comms v0.3 Task D34; A36, A37).

The one place that turns an open ``comms.db``, its audit writer, the key-slot store and the
built adapters (C33) into the running surface: the typed services, the facades and the closed
dispatcher, the admin socket's ``tool call`` / ``operator`` handlers and ``hello`` control
request, and the three listener apps. Nothing else builds services.

Operator commands wired here: ``client add|rotate|disable`` and ``oauth approve``. Every other
operator command is refused with a fixed message until it is wired (named in the evidence).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core.audit.writer import AuditWriter
from comms.core.auth import clients
from comms.core.delivery.commitment import commit_context
from comms.core.keys.slots import KeySlotStore
from comms.mcp.dispatch import Dispatcher
from comms.mcp.http import lease_authenticator
from comms.mcp.oauth.server import Built, OAuthSettings, build_oauth
from comms.runtime.adapters import Adapters
from comms.runtime.facades import Services, build_registry
from comms.runtime.hello import hello_handler
from comms.runtime.listeners import Listeners, RemoteListener, build_listeners
from comms.runtime.tool_calls import tool_call_handler
from comms.services.account import AccountService
from comms.services.campaigns import CampaignService
from comms.services.capability import CapabilityService
from comms.services.context import ContextEngine
from comms.services.directory import DirectoryService
from comms.services.groups import GroupService
from comms.services.handles import ContextHandles
from comms.services.identity import IdentityService
from comms.services.messages import MessageService
from comms.services.mutations import MutationExecutor

__all__ = ["CommsRuntime", "RemoteConfig", "build_comms_runtime"]

Handler = Callable[[dict[str, Any]], Any]
_TELEGRAM = ("telegram_bot", "telegram_user")


@dataclass(frozen=True)
class RemoteConfig:
    settings: OAuthSettings
    client_ref: str
    port: int


@dataclass(frozen=True)
class CommsRuntime:
    services: Services
    dispatcher: Dispatcher
    admin_handlers: Mapping[str, Handler]
    control_handlers: Mapping[str, Handler]
    listeners: Listeners
    oauth: Built | None

    def __repr__(self) -> str:
        return "CommsRuntime(<redacted>)"


class _InboxCounts:
    def __init__(self, conn: Any, adapters: Adapters) -> None:
        self._conn, self._configured = conn, adapters.webhook is not None

    def counts(self) -> dict[str, Any]:
        pending, completed = self._conn.execute(
            "SELECT sum(completed_at IS NULL), sum(completed_at IS NOT NULL) FROM webhook_inbox"
        ).fetchone()
        return {
            "configured": self._configured,
            "pending": pending or 0,
            "completed": completed or 0,
        }


def _operator_handler(
    conn: Any, store: KeySlotStore, oauth: Built | None, clock: Callable[[], datetime]
) -> Handler:
    def handle(args: dict[str, Any]) -> dict[str, Any]:
        command = tuple(args.get("command") or ())
        if command == ("client", "add"):
            cli = clients.add_client(
                conn, store, args["name"], now=clock(), helper_path=Path(args["helper_path"])
            )
            return {"client": cli}
        if command == ("client", "rotate"):
            clients.rotate_client(
                conn, store, args["client"], now=clock(), helper_path=Path(args["helper_path"])
            )
            return {"client": args["client"], "rotated": True}
        if command == ("client", "disable"):
            clients.disable_client(conn, args["client"])
            return {"client": args["client"], "disabled": True}
        if command == ("oauth", "approve"):
            if oauth is None:
                raise ValueError("the remote listener is not configured")
            return {"owner_code": oauth.approvals.issue(), "valid_for_seconds": 300}
        raise ValueError("this operator command is not wired in this release")

    return handle


def build_comms_runtime(
    conn: Any,
    writer: AuditWriter,
    store: KeySlotStore,
    adapters: Adapters,
    *,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    host: str,
    local_port: int,
    remote: RemoteConfig | None = None,
) -> CommsRuntime:
    capability = CapabilityService(adapters.capability, clock=clock)
    executor = MutationExecutor(writer, adapters.admin)
    services = Services(
        conn=conn,
        capability=capability,
        context=ContextEngine(
            conn, adapters.context, clock=clock, monotonic=monotonic, capability=capability
        ),
        handles=ContextHandles(conn, store, clock=clock),
        groups=GroupService(conn, capability, executor),
        messages=MessageService(conn, capability, executor),
        campaigns=CampaignService(
            writer, executor, adapters.delivery, commit=lambda: commit_context(writer, store)
        ),
        directory=DirectoryService(writer, executor),
        templates=None,
        media=None,
        account=AccountService(capability, webhooks=_InboxCounts(conn, adapters)),
        identity=IdentityService(conn),
        actors=tuple(a for a in _TELEGRAM if a in adapters.admin or a in adapters.context),
    )
    dispatcher = Dispatcher(build_registry(services))
    oauth = None
    remote_listener = None
    if remote is not None:
        oauth = build_oauth(
            conn,
            store,
            remote.settings,
            clock=clock,
            client_enabled=lambda _client_id: _enabled(conn, remote.client_ref),
        )
        remote_listener = RemoteListener(
            oauth=oauth, settings=remote.settings, client_ref=remote.client_ref, port=remote.port
        )
    listeners = build_listeners(
        dispatcher,
        local_authenticate=lease_authenticator(conn, store, clock),
        host=host,
        local_port=local_port,
        remote=remote_listener,
        webhook=adapters.webhook,
    )
    return CommsRuntime(
        services=services,
        dispatcher=dispatcher,
        admin_handlers={
            "tool call": tool_call_handler(dispatcher),
            "operator": _operator_handler(conn, store, oauth, clock),
        },
        control_handlers={"hello": hello_handler(conn)},
        listeners=listeners,
        oauth=oauth,
    )


def _enabled(conn: Any, client_ref: str) -> bool:
    row = conn.execute("SELECT enabled FROM clients WHERE ref = ?", (client_ref,)).fetchone()
    return row is not None and bool(row[0])
