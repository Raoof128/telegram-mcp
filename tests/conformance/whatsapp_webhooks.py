"""Conformance cases for ``whatsapp_webhooks`` (A18): the signed ingress into the durable inbox
and the resumable fan-out, over the Meta oracle's own signed webhook bodies."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from comms.transports.whatsapp.webhooks.inbox import Inbox
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from comms.transports.whatsapp.webhooks.worker import WebhookWorker
from tests.conformance.meta_oracle import APP_SECRET, oracle
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Mode, Skip
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person

PHONE = "+61400000001"


class _Archive:
    def __init__(self) -> None:
        self.calls = 0

    def ingest(self, raw: bytes) -> None:
        self.calls += 1


def _post(app: WebhookIngress, raw: bytes, headers: dict[str, str]) -> int:
    async def go() -> int:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://h"
        ) as client:
            return (await client.post("/webhooks/meta", content=raw, headers=headers)).status_code

    return asyncio.run(go())


def _world(mode: Mode) -> tuple[object, Inbox, WebhookIngress]:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    conn = fx.migrated(Path(tempfile.mkdtemp()))
    person(conn, phone=PHONE)
    inbox = Inbox(conn, clock=lambda: NOW)
    app = WebhookIngress(
        app_secret=APP_SECRET, verify_token="t", accept=inbox.accept, clock=lambda: 0.0
    )
    return conn, inbox, app


def _statuses_body() -> tuple[bytes, dict[str, str]]:
    graph = oracle()
    body = {
        "messaging_product": "whatsapp",
        "to": PHONE.lstrip("+"),
        "type": "text",
        "text": {"body": "x"},
    }
    graph.handle(
        "POST",
        "graph.facebook.com",
        f"/v21.0/{graph.phone_number_id}/messages",
        {},
        json.dumps(body).encode(),
    )
    raw, headers = graph.webhook_for_statuses()
    return raw, {k.lower(): v for k, v in headers.items()}


@REGISTRY.case("whatsapp_webhooks", "inbound_context")
def inbound_only_a_verified_body_reaches_the_inbox(mode: Mode) -> None:
    conn, _inbox, app = _world(mode)
    raw, headers = _statuses_body()
    assert _post(app, raw, {**headers, "x-hub-signature-256": "sha256=" + "0" * 64}) == 401
    assert conn.execute("SELECT count(*) FROM webhook_inbox").fetchone()[0] == 0
    assert _post(app, raw, headers) == 200
    assert conn.execute("SELECT count(*) FROM webhook_inbox").fetchone()[0] == 1


@REGISTRY.case("whatsapp_webhooks", "inbound_context")
def inbound_duplicate_is_acknowledged_and_stored_once(mode: Mode) -> None:
    conn, _inbox, app = _world(mode)
    raw, headers = _statuses_body()
    assert [_post(app, raw, headers) for _ in range(2)] == [200, 200]
    assert conn.execute("SELECT count(*) FROM webhook_inbox").fetchone()[0] == 1


@REGISTRY.case("whatsapp_webhooks", "provider_updates")
def provider_updates_apply_once_through_the_worker(mode: Mode) -> None:
    conn, _inbox, app = _world(mode)
    raw, headers = _statuses_body()
    _post(app, raw, headers)
    archive = _Archive()
    for _ in range(2):
        WebhookWorker(conn, archive, clock=lambda: NOW).run_once()
    assert (
        conn.execute("SELECT count(*) FROM provider_events").fetchone()[0] == 2
    )  # sent, delivered
    assert archive.calls == 1
