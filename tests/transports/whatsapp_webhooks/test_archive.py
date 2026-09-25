"""D39-PRE Task E10b: the comms-native WhatsApp archive (owner decision, 2026-09-25).

Webhook bodies already land durably in ``webhook_inbox``; the worker hands each to the archive,
which parses it with WhatsVault's public, pure normaliser and keeps each message once, keyed by
its semantic key. The archive is the ``whatsapp_webhook_archive`` context source.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest

from comms.core.maintenance.retention import purge_inbound
from comms.core.providers.protocols import ContextQuery, ProviderTarget
from comms.core.storage.db import write_tx
from comms.transports.whatsapp.webhooks.archive import ArchiveContext, CommsArchive
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

PHONE = "61400000001"


def _webhook(*messages, statuses=()):
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "1234567890"},
            "contacts": [{"wa_id": PHONE, "profile": {"name": "Sara"}}],
            "messages": list(messages), "statuses": list(statuses)}}]}]}).encode()  # fmt: skip


def _text(wamid, body, ts):
    return {
        "from": PHONE,
        "id": wamid,
        "timestamp": str(ts),
        "type": "text",
        "text": {"body": body},
    }


@pytest.fixture
def conn(tmp_path):
    return comms_world(tmp_path)["conn"]


def _rows(conn):
    return conn.execute(
        "SELECT wamid, chat, body FROM whatsapp_messages ORDER BY sent_at"
    ).fetchall()


def test_ingest_keeps_each_message_once(conn):
    archive = CommsArchive(conn, clock=lambda: NOW)
    raw = _webhook(_text("wamid.A", "Salaam", 1758800000), _text("wamid.B", "Nowruz?", 1758800060))
    archive.ingest(raw)
    archive.ingest(raw)  # a redelivery, or a crash between the archive and its flag
    assert _rows(conn) == [("wamid.A", PHONE, "Salaam"), ("wamid.B", PHONE, "Nowruz?")]


def test_a_group_message_is_archived_under_its_group(conn):
    archive = CommsArchive(conn, clock=lambda: NOW)
    message = {**_text("wamid.G", "hello group", 1758800000), "group_id": "120363049891234567"}
    archive.ingest(_webhook(message))
    assert _rows(conn) == [("wamid.G", "group:120363049891234567", "hello group")]
    source = ArchiveContext(conn, clock=lambda: NOW)
    target = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_g", "group:120363049891234567")
    page = source.read(ContextQuery(target, "recent", {}))
    assert [i["untrusted"]["text"] for i in page.items] == ["hello group"]


def test_statuses_write_no_message_and_a_malformed_body_raises(conn):
    archive = CommsArchive(conn, clock=lambda: NOW)
    archive.ingest(_webhook(statuses=[{"id": "wamid.A", "status": "read", "timestamp": "1758800100",
                                       "recipient_id": PHONE}]))  # fmt: skip
    assert _rows(conn) == []
    with pytest.raises(ValueError):
        archive.ingest(b"{not json")


def test_the_archive_is_a_context_source_newest_first(conn):
    archive = CommsArchive(conn, clock=lambda: NOW)
    archive.ingest(_webhook(*(_text(f"wamid.{i}", f"m{i}", 1758800000 + i) for i in range(5))))
    source = ArchiveContext(conn, clock=lambda: NOW)
    target = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_x", f"+{PHONE}")
    page = source.read(ContextQuery(target, "recent", {"limit": 3}))
    assert page.provenance == "whatsapp_webhook_archive"
    assert [i["untrusted"]["text"] for i in page.items] == ["m4", "m3", "m2"]
    assert all(i["source"] == "whatsapp_webhook_archive" and i["message_id"] for i in page.items)
    rest = source.read(ContextQuery(target, "recent", {"limit": 3, "cursor": page.next_cursor}))
    assert [i["untrusted"]["text"] for i in rest.items] == ["m1", "m0"] and rest.next_cursor is None
    other = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_y", "+61400000009")
    assert source.read(ContextQuery(other, "recent", {})).items == ()


def test_retention_purges_old_archive_rows(conn):
    CommsArchive(conn, clock=lambda: NOW).ingest(_webhook(_text("wamid.A", "old", 1758800000)))
    with write_tx(conn):
        assert purge_inbound(conn, cutoff=NOW + timedelta(days=1)) >= 1
    assert _rows(conn) == []


def test_the_ingress_records_each_secret_confirmed_in_operation(conn):
    """R-E6: the verify token by Meta's GET challenge, the app secret by a verified POST."""
    confirmed = []

    async def accept(raw):
        return None

    app = WebhookIngress(app_secret=b"s" * 32, verify_token="v" * 32, accept=accept,
                         clock=lambda: 0.0, on_confirmed=confirmed.append)  # fmt: skip

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            await client.get("/webhooks/meta", params={"hub.mode": "subscribe",
                             "hub.verify_token": "v" * 32, "hub.challenge": "42"})  # fmt: skip
            body = _webhook(_text("wamid.A", "x", 1758800000))
            sig = "sha256=" + hmac.new(b"s" * 32, body, hashlib.sha256).hexdigest()
            await client.post("/webhooks/meta", content=body,
                              headers={"content-type": "application/json", "x-hub-signature-256": sig})  # fmt: skip
            await client.get("/webhooks/meta", params={"hub.mode": "subscribe",
                             "hub.verify_token": "wrong", "hub.challenge": "42"})  # fmt: skip

    asyncio.run(go())
    assert confirmed == ["meta-webhook-secret", "meta-app-secret"]  # the refused GET adds nothing
