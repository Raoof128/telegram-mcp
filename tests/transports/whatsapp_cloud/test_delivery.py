"""comms v0.3 Task C23: WhatsApp Cloud delivery — the mirrored window and the frozen template
binding (A22, A23; O8; 5b-4 S3)."""

import hashlib
import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from comms.core.campaigns.templates import bind_template
from comms.core.delivery import freeze
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryTransport,
    FrozenDelivery,
    ResultKind,
    Skip,
    SkipReason,
)
from comms.core.delivery.window import mirror_window
from comms.transports.whatsapp.cloud.delivery import WhatsAppDelivery
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.templates import TemplateCatalog
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready
from tests.transports.whatsapp_cloud.helpers import Secrets, fixture_transport, raising

PHONE = "+61400000001"
SEND_AT = datetime(2026, 9, 24, 12, tzinfo=UTC)
TEMPLATE = {"name": "nowruz_greeting", "language": "en", "schema_version": 3, "parameters": ["Ali"]}
CONTENT = {"canonical": "Happy Nowruz"}


def _catalog(status="APPROVED", version=3):
    catalog = TemplateCatalog()
    catalog.replace({("nowruz_greeting", "en"): (status, version)})
    return catalog


def _transport(answer="send_ok", seen=None, catalog=None):
    def spy(request):
        if seen is not None:
            seen.append(request)
        return inner.handle_request(request)

    inner = fixture_transport(answer) if isinstance(answer, str) else raising(answer)
    api = GraphApi(
        Secrets(), version=1, phone_number_id="106540352242922", transport=httpx.MockTransport(spy)
    )
    return WhatsAppDelivery(api, catalog=catalog or _catalog())


def _intent(window_last=None, template=TEMPLATE):
    facts = {}
    if window_last is not None:
        facts["window"] = {
            "last_customer_message_at": window_last,
            "observed_at": window_last,
            "source_event_ref": "wamid.IN",
        }
    if template is not None:
        facts["template"] = template
    return DeliveryIntent("whatsapp", PHONE, CONTENT, facts)


def _frozen(payload):
    return FrozenDelivery("djb_x", "gen_x", "whatsapp", PHONE, payload, "k" * 64)


def test_it_is_a_delivery_transport():
    transport = _transport()
    assert isinstance(transport, DeliveryTransport) and (transport.name, transport.actor) == (
        "whatsapp",
        "whatsapp_cloud",
    )
    assert transport.normalize("+61 400 000 001") == PHONE


def test_in_window_free_form():
    payload = _transport().prepare(_intent("2026-09-24T09:00:00Z"), SEND_AT)
    body = json.loads(payload.data)
    assert body == {
        "kind": "text",
        "to": PHONE,
        "text": "Happy Nowruz",
        "window_closes_at": "2026-09-25T09:00:00.000000Z",
    }


def test_outside_window_uses_frozen_template():
    payload = _transport().prepare(_intent("2026-09-22T09:00:00Z"), SEND_AT)
    body = json.loads(payload.data)
    assert body["kind"] == "template" and body["parameters"] == ["Ali"]
    assert body["template"] == {
        "name": "nowruz_greeting",
        "language": "en",
        "schema_version": 3,
        "param_digests": [hashlib.sha256(b"Ali").hexdigest()],
    }


def test_missing_window_state_takes_template_path():
    assert json.loads(_transport().prepare(_intent(None), SEND_AT).data)["kind"] == "template"


def test_no_template_is_template_required_skip():
    assert _transport().prepare(_intent(None, template=None), SEND_AT) == Skip(
        SkipReason.TEMPLATE_REQUIRED
    )
    assert _transport().prepare(
        DeliveryIntent("whatsapp", PHONE, {"canonical": ""}, {}), SEND_AT
    ) == Skip(SkipReason.CONTENT_UNSUPPORTED)


def test_scheduled_free_form_judged_at_send_time_not_freeze_time():
    last = "2026-09-24T09:00:00Z"  # open now; closed by the scheduled send a day later
    later = SEND_AT + timedelta(days=1)
    assert json.loads(_transport().prepare(_intent(last), later).data)["kind"] == "template"
    assert _transport().prepare(_intent(last, template=None), later) == Skip(
        SkipReason.TEMPLATE_REQUIRED
    )


def test_still_valid_rechecks_the_window_at_claim_time():
    transport = _transport()
    payload = transport.prepare(_intent("2026-09-24T09:00:00Z"), SEND_AT)
    assert transport.still_valid(payload, SEND_AT) is True
    assert transport.still_valid(payload, datetime(2026, 9, 25, 9, 0, 1, tzinfo=UTC)) is False


@pytest.mark.parametrize("status,version", [("PAUSED", 3), ("DISABLED", 3), ("APPROVED", 4)])
def test_template_unavailable_at_claim_skips_never_swaps(status, version):
    catalog = _catalog()
    transport = _transport(catalog=catalog)
    payload = transport.prepare(_intent(None), SEND_AT)
    assert transport.still_valid(payload, SEND_AT) is True
    catalog.replace(
        {("nowruz_greeting", "en"): (status, version), ("other_template", "en"): ("APPROVED", 1)}
    )
    assert transport.still_valid(payload, SEND_AT) is False


def test_prepare_reads_no_archive_and_does_no_io(monkeypatch):
    transport = _transport()

    def refuse(*_a, **_k):
        raise AssertionError("I/O")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    monkeypatch.setattr(sqlite3, "connect", refuse)
    for intent in (_intent("2026-09-24T09:00:00Z"), _intent(None)):
        payload = transport.prepare(intent, SEND_AT)
        transport.still_valid(payload, SEND_AT)


def test_deliver_free_form_and_template_bodies():
    seen = []
    transport = _transport(seen=seen)
    text = transport.deliver(_frozen(transport.prepare(_intent("2026-09-24T09:00:00Z"), SEND_AT)))
    template = transport.deliver(_frozen(transport.prepare(_intent(None), SEND_AT)))
    assert text.kind is ResultKind.ACCEPTED and text.provider_message_ref.startswith("wamid.")
    assert template.kind is ResultKind.ACCEPTED
    first, second = (json.loads(r.content) for r in seen)
    assert first == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "61400000001",
        "type": "text",
        "text": {"body": "Happy Nowruz"},
    }
    assert second["type"] == "template" and second["template"] == {
        "name": "nowruz_greeting",
        "language": {"code": "en"},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": "Ali"}]}],
    }


@pytest.mark.parametrize(
    "answer,kind",
    [
        ("err_131047_reengagement", ResultKind.FAILED_PERMANENT),
        ("err_130429_throughput", ResultKind.FAILED_TRANSIENT),
    ],
)
def test_one_call_per_deliver_classified(answer, kind):
    for outcome, expected in ((answer, kind), (httpx.ReadTimeout, ResultKind.OUTCOME_UNKNOWN)):
        seen = []
        transport = _transport(outcome, seen)
        assert (
            transport.deliver(_frozen(transport.prepare(_intent(None), SEND_AT))).kind is expected
        )
        assert len(seen) == 1


def test_a_payload_for_another_number_is_refused_before_any_call():
    seen = []
    transport = _transport(seen=seen)
    payload = transport.prepare(
        DeliveryIntent("whatsapp", "+61400000009", CONTENT, {"template": TEMPLATE}), SEND_AT
    )
    with pytest.raises(ValueError):
        transport.deliver(_frozen(payload))
    assert seen == []


def test_the_freeze_copies_the_window_and_the_template_into_the_intent(tmp_path):
    conn = fx.migrated(tmp_path)
    rcp, _ = person(conn, phone=PHONE)
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"whatsapp"}), body="Happy Nowruz")
    identity_id = conn.execute("SELECT id FROM delivery_identities").fetchone()[0]
    mirror_window(conn, identity_id, "2026-09-24T09:00:00Z", "wamid.IN", now=NOW)
    mirror_window(
        conn, identity_id, "2026-09-23T09:00:00Z", "wamid.OLD", now=NOW
    )  # never moves back
    bind_template(conn, cmp, **TEMPLATE, now=NOW)
    freeze.send(conn, cmp, {"whatsapp": _transport()}, now=NOW)
    payload = json.loads(conn.execute("SELECT payload FROM delivery_jobs").fetchone()[0])
    assert payload["kind"] == "text"  # NOW (24 Sep 00:00) ... the window row is from 09:00 that day
    row = conn.execute(
        "SELECT last_customer_message_at, source_event_ref FROM endpoint_window"
    ).fetchone()
    assert tuple(row) == ("2026-09-24T09:00:00Z", "wamid.IN")


def test_the_conformance_delivery_contract_passes():
    report = run_suite(
        REGISTRY.subset("whatsapp_cloud", "delivery"), {"whatsapp_cloud": frozenset({"delivery"})}
    )
    assert report.ok and report.passed >= 2, report.failures
