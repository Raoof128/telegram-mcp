"""comms v0.3 Task C16: the MTProto DeliveryTransport (A20, A42; 5b-4 S2, S3)."""

import asyncio
import json
import socket
from datetime import UTC, datetime

import pytest

from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryTransport,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
    Skip,
    SkipReason,
)
from comms.transports.telegram.user.delivery import UserDelivery
from comms.transports.telegram.user.send import random_id_for
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite
from tests.transports.telegram_user.helpers import FakeSession

NOW = datetime(2026, 9, 25, tzinfo=UTC)
CHAT = "-1001234567890"
KEY = "ab" * 32


def _transport(session):
    return UserDelivery(session, run=asyncio.run)


def _frozen(transport, attempt_no=1, identity=CHAT, key=KEY):
    payload = transport.prepare(DeliveryIntent("telegram", identity, {"canonical": "hello"}), NOW)
    assert isinstance(payload, PreparedPayload)
    return FrozenDelivery("djb_x", "gen_x", "telegram", identity, payload, key, attempt_no)


def test_it_is_a_keyed_delivery_transport():
    transport = _transport(FakeSession())
    assert isinstance(transport, DeliveryTransport)
    assert (transport.name, transport.actor) == ("telegram", "telegram_user")
    assert transport.provider_request_key(KEY) == str(random_id_for(KEY))


def test_deliver_uses_send_with_the_jobs_idempotency_key():
    session = FakeSession()
    result = _transport(session).deliver(_frozen(_transport(session)))
    assert (result.kind, result.provider_message_ref) == (ResultKind.ACCEPTED, f"{CHAT}:100")
    assert session.sent == [(("channel", 1234567890), "hello", random_id_for(KEY))]


def test_retry_failed_after_transient_reuses_random_id():
    session = FakeSession(["flood", "ok"])
    transport = _transport(session)
    first = transport.deliver(_frozen(transport, attempt_no=1))
    second = transport.deliver(_frozen(transport, attempt_no=2))
    assert (first.kind, first.retry_after) == (ResultKind.FAILED_TRANSIENT, 12)
    assert second.kind is ResultKind.ACCEPTED
    assert {rid for _p, _t, rid in session.sent} == {random_id_for(KEY)} and len(
        session.visible
    ) == 1


def test_an_ambiguous_attempt_then_a_retry_shows_one_message():
    session = FakeSession(["drop", "drop"])  # both calls of attempt 1 ambiguous
    transport = _transport(session)
    assert transport.deliver(_frozen(transport, attempt_no=1)).kind is ResultKind.OUTCOME_UNKNOWN
    assert transport.deliver(_frozen(transport, attempt_no=2)).kind is ResultKind.ACCEPTED
    assert len(session.visible) == 1


@pytest.mark.parametrize("state", ["SESSION_REVOKED", "ACCOUNT_UNAVAILABLE", "AUTH_REQUIRED"])
def test_an_unusable_session_is_provably_unsent(state):
    session = FakeSession(readiness=state)
    transport = _transport(session)
    assert transport.deliver(_frozen(transport)).kind is ResultKind.FAILED_TRANSIENT
    assert session.sent == []


def test_a_peer_missing_from_the_cache_is_provably_unsent():
    session = FakeSession(cached=False)
    transport = _transport(session)
    assert transport.deliver(_frozen(transport)).kind is ResultKind.FAILED_TRANSIENT
    assert session.sent == []


def test_a_payload_for_another_chat_is_refused_before_any_call():
    session = FakeSession()
    transport = _transport(session)
    frozen = _frozen(transport, identity="42")
    with pytest.raises(ValueError):
        transport.deliver(FrozenDelivery("djb_x", "gen_x", "telegram", CHAT, frozen.payload, KEY))
    assert session.sent == []


def test_prepare_pure(monkeypatch):
    session = FakeSession()
    transport = _transport(session)

    def refuse(*_a, **_k):
        raise AssertionError("I/O")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    assert transport.normalize("channel:1234567890") == CHAT
    payload = transport.prepare(DeliveryIntent("telegram", CHAT, {"canonical": "hi"}), NOW)
    assert json.loads(payload.data) == {"chat_id": int(CHAT), "text": "hi"}
    assert transport.still_valid(payload, NOW) is True
    assert transport.prepare(DeliveryIntent("telegram", CHAT, {"canonical": ""}), NOW) == Skip(
        SkipReason.CONTENT_UNSUPPORTED
    )
    assert session.sent == []


def test_the_conformance_delivery_contract_passes():
    report = run_suite(
        REGISTRY.subset("telegram_user", "delivery"), {"telegram_user": frozenset({"delivery"})}
    )
    assert report.ok and report.passed >= 3, report.failures
