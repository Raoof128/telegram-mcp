"""comms v0.3 Task C29: the durable webhook inbox and the resumable fan-out (G12, A43)."""

import hashlib
import hmac
import json
import logging

import httpx
import pytest

from comms.core.delivery import freeze
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.providers.protocols import ADAPTER_CONTRACTS
from comms.transports.whatsapp.webhooks.inbox import Inbox
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from comms.transports.whatsapp.webhooks.worker import WebhookWorker
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Registry, run_suite
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, job_states, person, ready

SECRET = b"fixture-app-secret"
PHONE = "+61400000001"


class Archive:
    """WhatsVault's importer, as the worker sees it: idempotent by message id."""

    def __init__(self):
        self.imported = {}
        self.calls = 0

    def ingest(self, raw):
        self.calls += 1
        for entry in json.loads(raw)["entry"]:
            for change in entry["changes"]:
                for message in change["value"].get("messages", []):
                    self.imported.setdefault(message["id"], message)


def body(messages=(), statuses=()):
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "106540352242922"}}
    if messages:
        value["messages"] = list(messages)
    if statuses:
        value["statuses"] = list(statuses)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def inbound(wamid="wamid.IN1", ts=1790000000, text="salam, this is private"):
    return {
        "from": PHONE.lstrip("+"),
        "id": wamid,
        "timestamp": str(ts),
        "type": "text",
        "text": {"body": text},
    }


def status(wamid, state, ts=1790000100):
    return {"id": wamid, "status": state, "timestamp": str(ts), "recipient_id": PHONE.lstrip("+")}


@pytest.fixture
def conn(tmp_path):
    conn = fx.migrated(tmp_path)
    person(conn, phone=PHONE)
    return conn


def _window(conn):
    row = conn.execute(
        "SELECT last_customer_message_at, source_event_ref FROM endpoint_window"
    ).fetchone()
    return tuple(row) if row else None


def _flags(conn):
    return conn.execute(
        "SELECT archive_done, window_done, status_done, completed_at IS NOT NULL FROM webhook_inbox ORDER BY id"
    ).fetchall()


async def test_ack_only_after_the_inbox_commit(conn):
    inbox = Inbox(conn, clock=lambda: NOW)
    committed_before_ack = []

    async def accept(raw):
        await inbox.accept(raw)
        committed_before_ack.append(
            conn.execute("SELECT count(*) FROM webhook_inbox").fetchone()[0]
        )

    app = WebhookIngress(app_secret=SECRET, verify_token="t", accept=accept, clock=lambda: 0.0)
    raw = body([inbound()])
    signature = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://h"
    ) as client:
        response = await client.post(
            "/webhooks/meta",
            content=raw,
            headers={"content-type": "application/json", "x-hub-signature-256": signature},
        )
    assert response.status_code == 200 and committed_before_ack == [1]
    assert not conn.in_transaction


async def test_a_failed_inbox_write_is_not_acked(conn):
    fakes.plant_failure(conn, "webhook_inbox", "INSERT")
    inbox = Inbox(conn, clock=lambda: NOW)
    app = WebhookIngress(
        app_secret=SECRET, verify_token="t", accept=inbox.accept, clock=lambda: 0.0
    )
    raw = body([inbound()])
    signature = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://h"
    ) as client:
        response = await client.post(
            "/webhooks/meta",
            content=raw,
            headers={"content-type": "application/json", "x-hub-signature-256": signature},
        )
    assert response.status_code == 503


def test_a_clean_run_applies_every_effect_once(conn):
    archive = Archive()
    inbox = Inbox(conn, clock=lambda: NOW)
    assert inbox.store(body([inbound()])) is True
    report = WebhookWorker(conn, archive, clock=lambda: NOW).run_once()
    assert report.completed == 1 and _flags(conn) == [(1, 1, 1, 1)]
    assert list(archive.imported) == ["wamid.IN1"]
    assert _window(conn) == ("2026-09-21T14:13:20.000000Z", "wamid.IN1")


def test_duplicate_webhook_resumes_incomplete_fanout(conn):
    archive = Archive()
    inbox = Inbox(conn, clock=lambda: NOW)
    raw = body([inbound()])
    inbox.store(raw)
    with pytest.raises(BaseException, match="after_archive_before_flag"):
        WebhookWorker(
            conn, archive, clock=lambda: NOW, crash_at="after_archive_before_flag"
        ).run_once()
    assert inbox.store(raw) is False  # the duplicate is recorded as seen, never as "processed"
    report = WebhookWorker(conn, archive, clock=lambda: NOW).run_once()
    assert report.completed == 1 and _flags(conn) == [(1, 1, 1, 1)]
    assert list(archive.imported) == ["wamid.IN1"] and archive.calls == 2  # replayed, idempotent


@pytest.mark.parametrize(
    "boundary",
    [
        "after_inbox_commit",
        "after_archive_before_flag",
        "after_window_before_flag",
        "after_status_before_flag",
    ],
)
def test_crash_after_boundary_resumes_to_completion_with_each_effect_once(conn, boundary):
    transport = fakes.FakeWhatsApp(conn=conn)
    rcp = conn.execute("SELECT ref FROM recipients").fetchone()[0]
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"whatsapp"}))
    freeze.send(conn, cmp, {"whatsapp": transport}, now=NOW)
    Engine(conn, {"whatsapp": transport}, clock=lambda: NOW).execute(
        ExecutorLease(fakes.FakeLock()), cmp
    )
    archive = Archive()
    inbox = Inbox(conn, clock=lambda: NOW)
    raw = body([inbound()], [status("whatsapp-msg-1", "delivered")])
    inbox.store(raw)
    if boundary != "after_inbox_commit":
        with pytest.raises(BaseException, match=boundary):
            WebhookWorker(conn, archive, clock=lambda: NOW, crash_at=boundary).run_once()
    inbox.store(raw)  # Meta redelivers: harmless
    WebhookWorker(conn, archive, clock=lambda: NOW).run_once()
    assert _flags(conn) == [(1, 1, 1, 1)]
    assert list(archive.imported) == ["wamid.IN1"]
    assert _window(conn)[1] == "wamid.IN1"
    assert conn.execute("SELECT count(*) FROM provider_events").fetchone()[0] == 1
    assert set(job_states(conn, cmp).values()) == {"DELIVERED"}


def test_status_before_result_is_pending_match_then_reconciled(conn):
    transport = fakes.FakeWhatsApp(conn=conn)
    rcp = conn.execute("SELECT ref FROM recipients").fetchone()[0]
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"whatsapp"}))
    freeze.send(conn, cmp, {"whatsapp": transport}, now=NOW)
    Inbox(conn, clock=lambda: NOW).store(body(statuses=[status("whatsapp-msg-1", "delivered")]))
    WebhookWorker(conn, Archive(), clock=lambda: NOW).run_once()
    assert conn.execute("SELECT disposition FROM provider_events").fetchone()[0] == "pending_match"
    Engine(conn, {"whatsapp": transport}, clock=lambda: NOW).execute(
        ExecutorLease(fakes.FakeLock()), cmp
    )
    assert set(job_states(conn, cmp).values()) == {"DELIVERED"}
    assert conn.execute("SELECT disposition FROM provider_events").fetchone()[0] == "applied"


@pytest.mark.parametrize(
    "state,expected", [("sent", "ACCEPTED"), ("read", "DELIVERED"), ("failed", "FAILED_PERMANENT")]
)
def test_meta_statuses_map_to_provider_statuses(conn, state, expected):
    Inbox(conn, clock=lambda: NOW).store(body(statuses=[status("wamid.X", state)]))
    WebhookWorker(conn, Archive(), clock=lambda: NOW).run_once()
    assert conn.execute("SELECT reported_status FROM provider_events").fetchone()[0] == expected


def test_an_unknown_sender_moves_no_window_and_a_malformed_body_completes(conn):
    stranger = inbound("wamid.S1")
    stranger["from"] = "61499999999"
    inbox = Inbox(conn, clock=lambda: NOW)
    inbox.store(body([stranger]))
    inbox.store(b'{"not": "meta"')
    report = WebhookWorker(conn, Archive(), clock=lambda: NOW).run_once()
    assert _window(conn) is None and report.completed == 2 and report.malformed == 1


def test_body_and_phone_absent_from_events_and_logs(conn, caplog):
    caplog.set_level(logging.DEBUG)
    inbox = Inbox(conn, clock=lambda: NOW)
    inbox.store(body([inbound()], [status("wamid.X", "delivered")]))
    report = WebhookWorker(conn, Archive(), clock=lambda: NOW).run_once()
    events = json.dumps(
        [tuple(r) for r in conn.execute("SELECT event_type, payload FROM campaign_events")]
    )
    texts = [events, repr(report), repr(inbox)] + [r.getMessage() for r in caplog.records]
    assert all("salam" not in t and "61400000001" not in t for t in texts)


def test_the_whole_whatsapp_webhooks_conformance_suite_passes():
    registry = Registry({k: v for k, v in REGISTRY.cases.items() if k[0] == "whatsapp_webhooks"})
    report = run_suite(registry, {"whatsapp_webhooks": ADAPTER_CONTRACTS["whatsapp_webhooks"]})
    assert report.ok and report.skipped == {} and report.passed == 3, report.failures
