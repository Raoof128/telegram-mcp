"""comms v0.3 Task C31: privacy canaries across all four adapters.

Each adapter runs its conformance-style paths — success, refusal, ambiguity — with a canary
credential, phone number, Telegram id and message body. None may appear in a log line (DEBUG,
every logger), an exception's text, a repr, or a result's repr. The provider ref is the one
place an identity may live, and only inside encrypted state: it is never in a repr either.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from comms.core.delivery.transport import DeliveryIntent, FrozenDelivery
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.delivery import BotDelivery
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.user.delivery import UserDelivery
from comms.transports.whatsapp.cloud.delivery import WhatsAppDelivery
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.templates import TemplateCatalog
from comms.transports.whatsapp.webhooks.inbox import Inbox
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from comms.transports.whatsapp.webhooks.worker import WebhookWorker
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person
from tests.transports.telegram_bot import helpers as bot
from tests.transports.telegram_user.helpers import FakeSession
from tests.transports.whatsapp_cloud import helpers as meta

TG_ID = "7777123456"
TG_CHAT = f"-100{TG_ID}"
PHONE = "+61455501234"
BODY = "CANARY-BODY-7f3a private words"
SEND_AT = datetime(2026, 9, 24, 12, tzinfo=UTC)
CANARIES = (
    bot.CANARY,
    bot.CANARY.split(":")[1],
    meta.CANARY,
    TG_ID,
    PHONE.lstrip("+"),
    "CANARY-BODY-7f3a",
)


def _clean(texts):
    leaks = [(c, t) for t in texts for c in CANARIES if c in t]
    assert leaks == [], leaks[:3]


def _bot_answer(status, envelope):
    return httpx.MockTransport(lambda request: httpx.Response(status, json=envelope))


def _frozen(transport, identity, content, facts=None):
    payload = transport.prepare(
        DeliveryIntent(transport.name, identity, content, facts or {}), SEND_AT
    )
    return FrozenDelivery("djb_x", "gen_x", transport.name, identity, payload, "ab" * 32)


@pytest.fixture
def texts(caplog):
    caplog.set_level(logging.DEBUG)
    collected = []
    yield collected
    collected += [r.getMessage() for r in caplog.records] + [repr(r.args) for r in caplog.records]
    _clean(collected)


def _attempt(texts, call, *args):
    try:
        result = call(*args)
    except Exception as exc:  # noqa: BLE001 -- the text of any refusal is scanned too
        texts += [str(exc), repr(exc), repr(exc.args)]
        return None
    texts.append(repr(result))
    return result


def test_telegram_bot(texts):
    ok = {"ok": True, "result": {"message_id": 5, "chat": {"id": int(TG_CHAT)}, "text": BODY}}
    for transport in (
        _bot_answer(200, ok),
        _bot_answer(
            403,
            {
                "ok": False,
                "error_code": 403,
                "description": "Forbidden: bot was blocked by the user",
            },
        ),
        bot.raising(httpx.ReadTimeout),
        bot.raising(httpx.ConnectError),
    ):
        api = BotApi(bot.Secrets(), version=1, transport=transport)
        delivery = BotDelivery(api)
        frozen = _frozen(delivery, TG_CHAT, {"canonical": BODY})
        texts += [repr(api), repr(delivery), repr(frozen), repr(frozen.payload)]
        _attempt(texts, delivery.deliver, frozen)
        admin = BotAdmin(api)
        target = ProviderTarget("telegram", "telegram_bot", "dst_x", TG_CHAT)
        texts.append(repr(target))
        _attempt(
            texts,
            admin.invoke,
            SemanticOperation(C.MEMBER_BAN, {"user_id": int(TG_ID)}),
            target,
            "k",
        )


def test_telegram_user(texts):
    for script in ([], ["drop", "drop"], ["flood"]):
        session = FakeSession(script)
        delivery = UserDelivery(session, run=asyncio.run)
        frozen = _frozen(delivery, TG_CHAT, {"canonical": BODY})
        texts += [repr(delivery), repr(frozen)]
        _attempt(texts, delivery.deliver, frozen)


def test_whatsapp_cloud(texts):
    ok = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": PHONE, "wa_id": PHONE.lstrip("+")}],
        "messages": [{"id": "wamid.X"}],
    }
    catalog = TemplateCatalog()
    for transport in (
        httpx.MockTransport(lambda request: httpx.Response(200, json=ok)),
        httpx.MockTransport(
            lambda request: httpx.Response(
                400, json={"error": {"code": 131026, "message": f"undeliverable to {PHONE}"}}
            )
        ),
        meta.raising(httpx.ReadTimeout),
    ):
        api = GraphApi(
            meta.Secrets(), version=1, phone_number_id="106540352242922", transport=transport
        )
        delivery = WhatsAppDelivery(api, catalog=catalog)
        window = {
            "window": {
                "last_customer_message_at": "2026-09-24T09:00:00Z",
                "observed_at": "x",
                "source_event_ref": "w",
            }
        }
        frozen = _frozen(delivery, PHONE, {"canonical": BODY}, window)
        texts += [repr(api), repr(delivery), repr(frozen)]
        _attempt(texts, delivery.deliver, frozen)


def test_whatsapp_webhooks(texts, tmp_path):
    conn = fx.migrated(tmp_path)
    person(conn, phone=PHONE)
    raw = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": PHONE.lstrip("+"),
                                        "id": "wamid.IN",
                                        "timestamp": "1790000000",
                                        "type": "text",
                                        "text": {"body": BODY},
                                    }
                                ],
                                "statuses": [
                                    {
                                        "id": "wamid.X",
                                        "status": "failed",
                                        "timestamp": "1790000001",
                                        "recipient_id": PHONE.lstrip("+"),
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()
    inbox = Inbox(conn, clock=lambda: NOW)
    secret = b"canary-app-secret"
    app = WebhookIngress(
        app_secret=secret, verify_token="t", accept=inbox.accept, clock=lambda: 0.0
    )

    async def post(signature):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://h"
        ) as client:
            response = await client.post(
                "/webhooks/meta",
                content=raw,
                headers={"content-type": "application/json", "x-hub-signature-256": signature},
            )
            return response.status_code, response.text

    good = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    texts += [
        repr(asyncio.run(post("sha256=" + "0" * 64))),
        repr(asyncio.run(post(good))),
        repr(app),
        repr(inbox),
    ]

    class Archive:
        def ingest(self, body):
            return None

    worker = WebhookWorker(conn, Archive(), clock=lambda: NOW)
    texts += [repr(worker.run_once()), repr(worker)]
    texts += [
        json.dumps(
            [tuple(r) for r in conn.execute("SELECT event_type, payload FROM campaign_events")]
        )
    ]
