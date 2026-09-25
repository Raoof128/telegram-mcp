"""``comms selftest-daemon``: the real daemon over deterministic, always-accepting providers.

D39-PRE Task E6 (the injected seam; no test flag in a production path). The daemon, its
state, its listeners, its admin socket and its workers are the production ones; only the
adapter factory differs. Every provider here is local and deterministic:

- admin writes go through the real adapters' ``validate`` (so a malformed request is still
  refused exactly as in production) and then succeed with a synthetic provider ref;
- every capability is ``AVAILABLE``;
- context sources serve fixed synthetic pages under the provenance each actor really has;
- campaign delivery accepts every job;
- the bot actor is the real Bot API poller and local context over a scripted HTTP transport
  (three messages in the chat ``channel:1234567890``, served by the stored offset, so a restart
  never re-ingests), so group context reads are ``telegram_local`` from the real retained
  updates (D39-PRE E11c);
- with a webhook port configured, the real webhook pipeline (inbox, worker, the comms archive,
  the ingress) runs with fixed selftest secrets.

Nothing here opens a socket beyond the daemon's own listeners, or reads a credential; any other
provider transport that is touched fails loudly (``_Unreachable``).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from comms.core import timeutil
from comms.core.canonical import jcs_dumps
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryResult,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
    Skip,
)
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import (
    CapabilitySnapshot,
    ContextPage,
    ContextQuery,
    ProviderResult,
    ProviderTarget,
    SemanticOperation,
)
from comms.runtime.adapters import Adapters
from comms.runtime.settings import DaemonSettings
from comms.runtime.state import CommsState
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.context import BotContext
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.bot.updates import BotPoller
from comms.transports.telegram.peers import marked_chat_id
from comms.transports.telegram.user.admin import UserAdmin
from comms.transports.whatsapp.cloud.groups import WhatsAppAdmin
from comms.transports.whatsapp.numbers import e164
from comms.transports.whatsapp.webhooks.archive import ArchiveContext, CommsArchive
from comms.transports.whatsapp.webhooks.inbox import Inbox
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from comms.transports.whatsapp.webhooks.worker import WebhookWorker

__all__ = ["selftest_adapters"]

_ACTORS = ("telegram_bot", "telegram_user", "whatsapp_cloud")
_serial = itertools.count(1)


class _Unreachable:
    """A provider transport the selftest never reaches: any use is a defect."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError("selftest: a provider transport was reached")


def _no_run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    coroutine.close()
    raise AssertionError("selftest: an MTProto call was attempted")


def _created(capability: C) -> str | None:
    n = next(_serial)
    return {
        C.INVITE_CREATE: f"https://t.me/+Selftest{n:06d}",
        C.GROUP_INVITE_RESET: f"https://chat.whatsapp.com/Selftest{n:06d}",
        C.TOPIC_CREATE: str(n),
        C.MESSAGE_SEND: str(1000 + n),
        C.TEMPLATE_CREATE: str(9_000_000 + n),
    }.get(capability)


class _Accepting:
    """Real validation, then success."""

    def __init__(self, validator: Any) -> None:
        self._validator = validator

    def validate(self, op: SemanticOperation, target: ProviderTarget) -> None:
        self._validator.validate(op, target)

    def invoke(self, op: SemanticOperation, target: ProviderTarget, key: str) -> ProviderResult:
        return ProviderResult("SUCCEEDED", None, provider_ref=_created(op.capability))


class _Available:
    def snapshot(self, actor: str, target: ProviderTarget) -> CapabilitySnapshot:
        stamp = timeutil.iso(datetime.now(UTC))
        return CapabilitySnapshot(
            actor, target.destination_ref, dict.fromkeys(C, S.AVAILABLE), stamp
        )


class _Pages:
    def __init__(self, provenance: str) -> None:
        self._provenance = provenance

    def read(self, query: ContextQuery) -> ContextPage:
        if query.kind == "members":
            return ContextPage((), self._provenance, None)
        start = int(query.args.get("cursor") or 1000)
        stamp = timeutil.iso(datetime.now(UTC))
        items = tuple(
            {
                "source": self._provenance,
                "observed_at": stamp,
                "message_id": start - i,
                "sent_at": stamp,
                "sender_id": "1",
                "chat_id": query.target.identity,
                "untrusted": {"text": f"selftest message {start - i}", "sender_name": "Selftest"},
            }
            for i in range(3)
        )
        return ContextPage(items, self._provenance, str(start - 3) if start > 991 else None)


class _AcceptingDelivery:
    def __init__(self, name: str, normalize: Callable[[str], str]) -> None:
        self.name, self._normalize = name, normalize

    def normalize(self, platform_identity: str) -> str:
        return self._normalize(platform_identity)

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip:
        data = jcs_dumps(
            {
                "content": dict(intent.content),
                "send_at": timeutil.iso(send_at),
                "to": intent.identity,
            }
        )
        return PreparedPayload(data=data, digest=hashlib.sha256(data).hexdigest())

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        return True

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult:
        return DeliveryResult(
            ResultKind.ACCEPTED, provider_message_ref=f"{self.name}-{next(_serial)}"
        )


# Public selftest constants: a selftest daemon is never a production daemon.
SELFTEST_APP_SECRET = b"selftest-app-secret-0000000000000"
SELFTEST_VERIFY_TOKEN = "selftest-verify-token-00000000000"
SELFTEST_CHAT = -1001234567890  # channel:1234567890, marked
_SELFTEST_BOT = "1:selftest"  # the token shape the pinned client checks; no real bot


def _bot_updates() -> list[dict[str, Any]]:
    return [
        {
            "update_id": n,
            "message": {
                "message_id": 100 + n,
                "date": 1758800000 + n,
                "chat": {"id": SELFTEST_CHAT, "type": "supergroup", "title": "MQ Society"},
                "from": {"id": 42, "is_bot": False, "first_name": "Sara"},
                "text": f"selftest update {n}",
            },
        }
        for n in (1, 2, 3)
    ]


def _bot_transport() -> Any:
    import httpx

    def handle(request: Any) -> Any:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            offset = int((json.loads(request.content or b"{}") or {}).get("offset") or 0)
            result: Any = [u for u in _bot_updates() if u["update_id"] >= offset]
        elif method == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "selftest"}
        else:
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})

    return httpx.MockTransport(handle)


class _OneSecret:
    def get(self, item: str, version: int) -> bytes:
        return _SELFTEST_BOT.encode()


def selftest_adapters(state: CommsState, settings: DaemonSettings) -> Adapters:
    def clock() -> datetime:
        return datetime.now(UTC)

    unreachable: Any = _Unreachable()
    admins = {
        "telegram_bot": _Accepting(BotAdmin(unreachable)),
        "telegram_user": _Accepting(UserAdmin(unreachable, run=_no_run, clock=clock)),
        "whatsapp_cloud": _Accepting(WhatsAppAdmin(unreachable, unreachable)),
    }
    store: Any = _OneSecret()
    api = BotApi(store, version=1, transport=_bot_transport())
    adapters = Adapters(
        delivery={
            "telegram": _AcceptingDelivery("telegram", marked_chat_id),
            "whatsapp": _AcceptingDelivery("whatsapp", e164),
        },
        capability=dict.fromkeys(_ACTORS, _Available()),
        admin=dict(admins),
        context={"telegram_bot": BotContext(api, state.conn, clock=clock)},
    )
    adapters.poller = BotPoller(api, state.conn, clock=clock)
    if settings.webhook_port is not None:
        inbox = Inbox(state.conn, clock=clock)
        adapters.inbox = inbox
        adapters.worker = WebhookWorker(
            state.conn, CommsArchive(state.conn, clock=clock), clock=clock
        )
        adapters.webhook = WebhookIngress(
            app_secret=SELFTEST_APP_SECRET,
            verify_token=SELFTEST_VERIFY_TOKEN,
            accept=inbox.accept,
            clock=time.monotonic,
        )
        adapters.listeners["webhook"] = adapters.webhook
        adapters.context["whatsapp_cloud"] = ArchiveContext(state.conn, clock=clock)
    return adapters
