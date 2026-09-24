"""comms v0.3 Task C7: the Bot API DeliveryTransport (5b-4 S2, S3; A19, A21)."""

import hashlib
import json
import socket
from datetime import UTC, datetime

import httpx
import pytest

from comms.core.canonical import jcs_dumps
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryTransport,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
    Skip,
    SkipReason,
)
from comms.transports.telegram.bot.delivery import MAX_TEXT, BotDelivery
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.peers import marked_chat_id
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite
from tests.transports.telegram_bot.helpers import Secrets, fixture_transport, raising

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _transport(inner, seen=None):
    def spy(request):
        if seen is not None:
            seen.append(request)
        return inner.handle_request(request)

    return BotDelivery(BotApi(Secrets(), version=1, transport=httpx.MockTransport(spy)))


def _frozen(payload, identity="-1001234567890"):
    return FrozenDelivery(
        job_ref="djb_" + "a" * 26,
        generation_ref="gen_" + "a" * 26,
        transport="telegram",
        identity=identity,
        payload=payload,
        idempotency_key="k" * 64,
    )


def _prepared(transport, text="hello", identity="-1001234567890"):
    payload = transport.prepare(DeliveryIntent("telegram", identity, {"text": text}), NOW)
    assert isinstance(payload, PreparedPayload)
    return payload


def test_it_is_a_delivery_transport_named_telegram():
    transport = _transport(fixture_transport("sendMessage_ok"))
    assert isinstance(transport, DeliveryTransport) and transport.name == "telegram"


@pytest.mark.parametrize(
    "raw,marked",
    [
        ("user:42", "42"),
        ("private:42", "42"),
        ("group:42", "-42"),
        ("channel:42", "-1000000000042"),  # -(10**12 + id), Telethon's get_peer_id and the Bot API
        ("channel:1234567890", "-1001234567890"),
    ],
)
def test_normalize_is_the_marked_chat_id(raw, marked):
    assert _transport(fixture_transport("sendMessage_ok")).normalize(raw) == marked
    assert marked_chat_id(raw) == marked


@pytest.mark.parametrize("raw", ["chat:1", "user:", "user:-1", "user:1a", "42", "group:0"])
def test_normalize_refuses_anything_else(raw):
    with pytest.raises(ValueError):
        marked_chat_id(raw)


def test_prepare_renders_one_send_message_payload():
    payload = _prepared(_transport(fixture_transport("sendMessage_ok")))
    assert json.loads(payload.data) == {"chat_id": -1001234567890, "text": "hello"}
    assert payload.data == jcs_dumps(json.loads(payload.data))
    assert payload.digest == hashlib.sha256(payload.data).hexdigest()


def test_prepare_skips_oversize_empty_or_unknown_content():
    transport = _transport(fixture_transport("sendMessage_ok"))
    astral = "\U0001f600"  # two UTF-16 code units, as Telegram counts
    assert isinstance(
        transport.prepare(DeliveryIntent("telegram", "1", {"text": "x" * MAX_TEXT}), NOW),
        PreparedPayload,
    )
    for content in (
        {"text": "x" * (MAX_TEXT + 1)},
        {"text": astral * (MAX_TEXT // 2 + 1)},
        {"text": ""},
        {"text": 5},
        {"text": "hi", "photo": "x"},
        {},
    ):
        result = transport.prepare(DeliveryIntent("telegram", "1", content), NOW)
        assert result == Skip(SkipReason.CONTENT_UNSUPPORTED), content


def test_still_valid_is_true_the_bot_has_no_window():
    transport = _transport(fixture_transport("sendMessage_ok"))
    assert transport.still_valid(_prepared(transport), NOW) is True


def test_prepare_is_pure_no_io(monkeypatch):
    seen = []
    transport = _transport(fixture_transport("sendMessage_ok"), seen)

    def no_network(*_a, **_k):
        raise AssertionError("network touched")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr("builtins.open", no_network)
    transport.normalize("channel:1")
    payload = _prepared(transport)
    transport.still_valid(payload, NOW)
    assert seen == []


def test_accepted_returns_the_chat_qualified_message_id_as_the_opaque_ref():
    seen = []
    transport = _transport(fixture_transport("sendMessage_ok"), seen)
    result = transport.deliver(_frozen(_prepared(transport)))
    assert (result.kind, result.provider_message_ref) == (
        ResultKind.ACCEPTED,
        "-1001234567890:4711",
    )
    assert [r.url.path.rsplit("/", 1)[1] for r in seen] == ["sendMessage"]


@pytest.mark.parametrize(
    "exc_type", [httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.WriteError]
)
def test_one_http_call_per_deliver_even_on_ambiguity(exc_type):
    seen = []
    transport = _transport(raising(exc_type), seen)
    result = transport.deliver(_frozen(_prepared(transport)))
    assert result.kind is ResultKind.OUTCOME_UNKNOWN and len(seen) == 1


def test_ambiguous_timeout_is_outcome_unknown():
    transport = _transport(raising(httpx.ReadTimeout))
    assert transport.deliver(_frozen(_prepared(transport))).kind is ResultKind.OUTCOME_UNKNOWN


def test_429_is_failed_transient_with_retry_after_recorded():
    seen = []
    transport = _transport(fixture_transport("sendMessage_429"), seen)
    result = transport.deliver(_frozen(_prepared(transport)))
    assert (result.kind, result.retry_after, len(seen)) == (ResultKind.FAILED_TRANSIENT, 17, 1)


def test_a_payload_whose_chat_differs_from_the_frozen_identity_is_refused():
    seen = []
    transport = _transport(fixture_transport("sendMessage_ok"), seen)
    payload = _prepared(transport, identity="42")
    with pytest.raises(ValueError):
        transport.deliver(_frozen(payload, identity="-1001234567890"))
    assert seen == []


def test_the_conformance_delivery_contract_passes():
    report = run_suite(
        REGISTRY.subset("telegram_bot", "delivery"), {"telegram_bot": frozenset({"delivery"})}
    )
    assert report.ok, report.failures
