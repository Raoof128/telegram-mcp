"""The adapter registry (comms v0.3 Task C33; A18, A24, A36).

``build_adapters`` builds the four adapters from the credentials active in comms.db, read from
the secret store (``active_credential`` recomputes each id). A missing credential never
crashes: the actor's capability provider still exists and reports ``NOT_CONFIGURED``, and the
actor offers nothing else. The Telegram user actor uses the one Telethon session for delivery,
capability, admin, context and the update stream (which the registry claims, A24). The webhook
ingress is served only when both the app secret and the verify token are configured, and only
as its own listener.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from comms.core.credentials import active_credential, active_version
from comms.core.delivery.transport import DeliveryTransport
from comms.core.keys.secrets import SecretStore
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.capability import BotCapability
from comms.transports.telegram.bot.context import BotContext
from comms.transports.telegram.bot.delivery import BotDelivery
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.bot.updates import BotPoller
from comms.transports.telegram.user.admin import UserAdmin
from comms.transports.telegram.user.capability import UserCapability
from comms.transports.telegram.user.context import UserContext
from comms.transports.telegram.user.delivery import UserDelivery
from comms.transports.telegram.user.updates import UserUpdateConsumer
from comms.transports.whatsapp.cloud.account import WhatsAppCapability
from comms.transports.whatsapp.cloud.delivery import WhatsAppDelivery
from comms.transports.whatsapp.cloud.groups import GroupDiscovery, WhatsAppAdmin
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.templates import TemplateCatalog
from comms.transports.whatsapp.webhooks.inbox import Inbox
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from comms.transports.whatsapp.webhooks.worker import Archive, WebhookWorker

__all__ = ["UPDATE_OWNER", "AdapterSettings", "Adapters", "build_adapters"]

UPDATE_OWNER = "update-consumer"
Runner = Callable[[Coroutine[Any, Any, Any]], Any]


@dataclass(frozen=True)
class AdapterSettings:
    telegram_delivery_actor: Literal["telegram_bot", "telegram_user"] = "telegram_bot"
    meta_phone_number_id: str | None = None
    meta_waba_id: str | None = None


@dataclass
class Adapters:
    delivery: dict[str, DeliveryTransport] = field(default_factory=dict)
    capability: dict[str, Any] = field(default_factory=dict)
    admin: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    webhook: WebhookIngress | None = None
    inbox: Inbox | None = None
    worker: WebhookWorker | None = None
    updates: UserUpdateConsumer | None = None
    poller: BotPoller | None = None  # D39-PRE E5: fills bot_updates, the bot's local context
    listeners: dict[str, Any] = field(default_factory=dict)
    catalog: TemplateCatalog = field(default_factory=TemplateCatalog)

    def __repr__(self) -> str:
        return (
            f"Adapters(delivery={sorted(self.delivery)}, capability={sorted(self.capability)},"
            f" listeners={sorted(self.listeners)})"
        )


class _NoSession:
    """The user actor without a session: every capability is NOT_CONFIGURED (AUTH_REQUIRED)."""

    def readiness(self) -> str:
        return "AUTH_REQUIRED"

    async def self_rights(self, peer_type: str, peer_id: int, *, timeout: float) -> Any:
        raise AssertionError("never called without a session")


def _never(coroutine: Coroutine[Any, Any, Any]) -> Any:
    coroutine.close()
    raise AssertionError("the user actor has no session")


def _configured(conn: Any, secrets: SecretStore, purpose: str) -> int | None:
    """The active version, once its stored value checks out; None when not configured."""
    if active_credential(conn, secrets, purpose) is None:
        return None
    row = active_version(conn, purpose)
    return None if row is None else int(row[0])


def build_adapters(
    conn: Any,
    secrets: SecretStore,
    settings: AdapterSettings,
    *,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    archive: Archive | None,
    telegram_session: Any = None,
    run: Runner | None = None,
) -> Adapters:
    adapters = Adapters()
    telegram = {
        "telegram_bot": _telegram_bot(adapters, conn, secrets, clock),
        "telegram_user": _telegram_user(adapters, conn, telegram_session, run, clock),
    }
    fallback = (
        "telegram_user" if settings.telegram_delivery_actor == "telegram_bot" else "telegram_bot"
    )
    chosen = telegram[settings.telegram_delivery_actor] or telegram[fallback]  # P §10: auto
    if chosen is not None:
        adapters.delivery["telegram"] = chosen
    _whatsapp(adapters, conn, secrets, settings, clock)
    _webhooks(adapters, conn, secrets, clock, monotonic, archive)
    return adapters


def _telegram_bot(
    adapters: Adapters, conn: Any, secrets: SecretStore, clock: Callable[[], datetime]
) -> DeliveryTransport | None:
    version = _configured(conn, secrets, "telegram-bot-token")
    if version is None:
        adapters.capability["telegram_bot"] = BotCapability(None, clock=clock)
        return None
    api = BotApi(secrets, version=version)
    adapters.capability["telegram_bot"] = BotCapability.from_api(api, clock=clock)
    adapters.admin["telegram_bot"] = BotAdmin(api)
    adapters.context["telegram_bot"] = BotContext(api, conn, clock=clock)
    adapters.poller = BotPoller(api, conn, clock=clock)
    return BotDelivery(api)


def _telegram_user(
    adapters: Adapters, conn: Any, session: Any, run: Runner | None, clock: Callable[[], datetime]
) -> DeliveryTransport | None:
    if session is None or run is None:
        adapters.capability["telegram_user"] = UserCapability(_NoSession(), run=_never, clock=clock)
        return None
    session.claim_updates(UPDATE_OWNER)  # A24: the one consumer of the one session's stream
    adapters.capability["telegram_user"] = UserCapability(session, run=run, clock=clock)
    adapters.admin["telegram_user"] = UserAdmin(session, run=run, clock=clock)
    adapters.context["telegram_user"] = UserContext(session, run=run, clock=clock)
    adapters.updates = UserUpdateConsumer(conn, clock=clock)
    return UserDelivery(session, run=run)


def _whatsapp(
    adapters: Adapters,
    conn: Any,
    secrets: SecretStore,
    settings: AdapterSettings,
    clock: Callable[[], datetime],
) -> None:
    version = _configured(conn, secrets, "meta-access-token")
    if version is None or settings.meta_phone_number_id is None:
        adapters.capability["whatsapp_cloud"] = WhatsAppCapability(None, None, clock=clock)
        return
    api = GraphApi(
        secrets,
        version=version,
        phone_number_id=settings.meta_phone_number_id,
        waba_id=settings.meta_waba_id,
    )
    discovery = GroupDiscovery(api)
    adapters.capability["whatsapp_cloud"] = WhatsAppCapability(api, discovery, clock=clock)
    adapters.admin["whatsapp_cloud"] = WhatsAppAdmin(api, discovery)
    adapters.delivery["whatsapp"] = WhatsAppDelivery(api, catalog=adapters.catalog)


def _webhooks(
    adapters: Adapters,
    conn: Any,
    secrets: SecretStore,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    archive: Archive | None,
) -> None:
    secret_version = _configured(conn, secrets, "meta-app-secret")
    token_version = _configured(conn, secrets, "meta-webhook-secret")
    if secret_version is None or token_version is None or archive is None:
        return  # D39-PRE: never accept an event the inbox could not archive
    inbox = Inbox(conn, clock=clock)
    adapters.inbox = inbox
    adapters.worker = WebhookWorker(conn, archive, clock=clock)
    adapters.webhook = WebhookIngress(
        app_secret=secrets.get("meta-app-secret", secret_version),
        verify_token=secrets.get("meta-webhook-secret", token_version).decode("utf-8"),
        accept=inbox.accept,
        clock=monotonic,
    )
    adapters.listeners["webhook"] = adapters.webhook
